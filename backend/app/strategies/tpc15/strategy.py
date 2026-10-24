"""TPC-15: Trend Pullback Confirmation — standalone, ISOLATED, non-executing.

Owner-provided strategy guide (pasted 2026-10-24). Source of truth: the
owner's guide text + reference code. The project also reportedly contains a
TPC-15 infographic with an additional five-candle liquidity-sweep condition:
that file is NOT present anywhere in this project (searched repo + workspace
2026-10-24), so THIS module implements the simpler GUIDE version and does
NOT combine the sweep variant. See TPC15_IMPLEMENTATION_REPORT.md.

Isolation contract (owner brief, section C):
  * NOT registered in app/strategies.STRATEGY_CLASSES -> never scanned,
    never signalled, never executed by the live loop.
  * No BaseStrategy / Candidate inheritance, no engine interface.
  * No imports from execution/, no broker calls, no order flow, no AI.
  * Pure functions: candle frames in, structured dict out.
  * No instruments are hardcoded - the caller supplies instrument and
    candles; nothing here knows or cares which market it is.
  * Closed candles only. The only forward-looking datum used is the NEXT
    candle's OPEN (known at the moment of entry), per the guide's
    NEXT_CANDLE_OPEN rule.

Rules (verbatim from the guide, unmodified - do not optimize):
  Indicators  EMA 9, EMA 21, EMA 200 on M15; session 14:00-18:00 UTC;
              take-profit distance = 1.25 x initial risk.
  BUY   1. price above EMA 200             2. EMA 9 above EMA 21
        3. pullback touches EMA 21         4. bullish candle closes above
                                              the previous candle's high
        5. entry at the NEXT candle's open 6. stop below the lowest point
                                              of the previous five candles
        7. take-profit = entry + 1.25 x risk
  SELL  mirror of BUY (below EMA 200, EMA 9 < EMA 21, pullback UP to EMA 21,
        bearish close below previous low, stop above the five-candle high).
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

CONFIG = {
    "timeframe": "M15",
    "fast_ema_period": 9,
    "pullback_ema_period": 21,
    "trend_ema_period": 200,
    "session_start_utc": "14:00",
    "session_end_utc": "18:00",
    "stop_lookback_candles": 5,
    "take_profit_r": 1.25,
}

TP_R = float(CONFIG["take_profit_r"])
LOOKBACK = int(CONFIG["stop_lookback_candles"])
_SESSION_START = time(*[int(x) for x in CONFIG["session_start_utc"].split(":")])
_SESSION_END = time(*[int(x) for x in CONFIG["session_end_utc"].split(":")])

# Status vocabulary (structured, machine-readable)
S_SIGNAL = "SIGNAL"
S_NO_SIGNAL = "NO_SIGNAL"
S_INSUFFICIENT = "INSUFFICIENT_DATA"
S_OUT_OF_SESSION = "OUT_OF_SESSION"
S_INVALID_RISK = "INVALID_RISK"
S_DUPLICATE = "DUPLICATE"

REASON_BUY = [
    "Price above EMA 200",
    "EMA 9 above EMA 21",
    "Pullback to EMA 21",
    "Bullish confirmation above previous high",
]
REASON_SELL = [
    "Price below EMA 200",
    "EMA 9 below EMA 21",
    "Pullback to EMA 21",
    "Bearish confirmation below previous low",
]


# ---------------------------------------------------------------------------
# helpers - closed-candle frames in, nothing mutated, nothing invented
# ---------------------------------------------------------------------------
def _frame(candles) -> pd.DataFrame:
    """Accept a DataFrame or a list of dicts; normalize to UTC-indexed
    OHLC. Never fabricates rows; raises ValueError on unusable input
    (None included - "missing data" is a NO-signal, never a crash)."""
    if candles is None:
        raise ValueError("candle data is missing (None)")
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(list(candles))
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"candle frame missing column '{col}'")
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    elif "ts" in df.columns:
        idx = pd.to_datetime(df["ts"], unit="s", utc=True)
    elif "time" in df.columns:
        idx = pd.to_datetime(df["time"], utc=True)
    else:
        raise ValueError("candle frame needs a UTC datetime index or a ts/time column")
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")       # naive timestamps are UTC by definition here
    else:
        idx = idx.tz_convert("UTC")
    out = df[["open", "high", "low", "close"]].astype(float)
    out.index = idx
    return out


def compute_emas(closes: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Standard EMA 9 / 21 / 200 (pandas ewm, adjust=False - the recursive
    definition). These are the guide's ONLY indicators; nothing added."""
    closes = pd.Series(closes, dtype=float)
    return (closes.ewm(span=int(CONFIG["fast_ema_period"]), adjust=False).mean(),
            closes.ewm(span=int(CONFIG["pullback_ema_period"]), adjust=False).mean(),
            closes.ewm(span=int(CONFIG["trend_ema_period"]), adjust=False).mean())


def inside_session(ts_utc) -> bool:
    """Session membership for an INSTANT: start <= t < end (UTC).

    The end boundary is exclusive everywhere in this module: a candle that
    closes exactly at 18:00 is out of session, and an entry candle opening
    exactly at 18:00 is rejected - no new positions at/after session end."""
    t = pd.Timestamp(ts_utc)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    clock = t.time()
    return _SESSION_START <= clock < _SESSION_END


def _iso(ts) -> str:
    return pd.Timestamp(ts).isoformat()


def setup_id_for(direction: str, confirmation_open_utc) -> str:
    """Stable identity of a confirmation setup: one signal per confirmation
    candle + direction (dedupe key, owner brief section C)."""
    return f"TPC15-{direction}-{_iso(confirmation_open_utc)}"


# ---------------------------------------------------------------------------
# 1) detection - runs on CLOSED candles only; candles[-1] is the closed
#    CONFIRMATION candle. Mirrors the owner's reference evaluate_setup().
# ---------------------------------------------------------------------------
def evaluate_setup(candles, ema9=None, ema21=None, ema200=None,
                   current_time_utc=None,
                   emitted: Optional[set] = None) -> Dict[str, Any]:
    """Detect a TPC-15 setup on closed candles.

    candles:   DataFrame or list of dicts; candles[-1] is the CLOSED
               confirmation candle; candles[-2] the previous candle;
               candles[-6:-1] are the FIVE completed candles whose high/low
               define the stop (the confirmation candle itself is excluded).
    ema9/21/200: EMA series aligned with candles (value [-1] is the value at
               the confirmation candle). If omitted they are computed from
               the candle closes (needs >= trend period candles).
    current_time_utc: the evaluation instant; defaults to the confirmation
               candle's close time. Session filter uses this instant.
    emitted:   caller-owned set of setup_ids already signalled; a repeat
               returns DUPLICATE and is not signalled again.

    Returns a structured dict; a SIGNAL dict carries reference levels based
    on the confirmation close - the ACTUAL entry is attached by
    finalize_entry() at the next candle's open.
    """
    try:
        df = _frame(candles)
    except ValueError as exc:
        return {"status": S_INSUFFICIENT, "reason": str(exc), "strategy": "TPC-15"}

    n = len(df)
    if n < LOOKBACK + 1:                      # 5 stop candles + 1 confirmation
        return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                "reason": f"need at least {LOOKBACK + 1} closed candles, got {n}"}

    if ema9 is None or ema21 is None or ema200 is None:
        if n < int(CONFIG["trend_ema_period"]):
            return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                    "reason": (f"EMA {CONFIG['trend_ema_period']} needs "
                               f"{CONFIG['trend_ema_period']} closed candles, got {n}")}
        ema9, ema21, ema200 = compute_emas(df["close"])
    else:
        ema9, ema21, ema200 = (pd.Series(ema9, dtype=float),
                               pd.Series(ema21, dtype=float),
                               pd.Series(ema200, dtype=float))
        if not (len(ema9) == len(ema21) == len(ema200) == n):
            return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                    "reason": "EMA series must align 1:1 with the candle frame"}

    current, previous = df.iloc[-1], df.iloc[-2]
    conf_open = df.index[-1]
    eval_ts = pd.Timestamp(current_time_utc) if current_time_utc is not None \
        else conf_open + pd.Timedelta(minutes=15)      # M15 close time
    if eval_ts.tzinfo is None:
        eval_ts = eval_ts.tz_localize("UTC")

    checks: List[Dict[str, Any]] = []
    if not inside_session(eval_ts):
        return {"status": S_OUT_OF_SESSION, "strategy": "TPC-15",
                "timestamp": _iso(eval_ts),
                "reason": (f"confirmation closes {eval_ts.strftime('%H:%M')} UTC, "
                           f"outside {CONFIG['session_start_utc']}-"
                           f"{CONFIG['session_end_utc']}")}

    # the FIVE completed candles before the confirmation candle (its own
    # high/low are NOT part of the stop window - guide: "previous five")
    window = df.iloc[-1 - LOOKBACK:-1]
    stop_low = float(window["low"].min())
    stop_high = float(window["high"].max())

    e9, e21, e200 = float(ema9.iloc[-1]), float(ema21.iloc[-1]), float(ema200.iloc[-1])
    c, o = float(current["close"]), float(current["open"])
    prev_high, prev_low = float(previous["high"]), float(previous["low"])

    # ---------------- BUY ----------------
    buy_trend = c > e200 and e9 > e21
    checks.append({"rule": "BUY 1 price above EMA200", "ok": c > e200,
                   "detail": f"close {c:.6g} vs EMA200 {e200:.6g}"})
    checks.append({"rule": "BUY 2 EMA9 above EMA21", "ok": e9 > e21,
                   "detail": f"EMA9 {e9:.6g} vs EMA21 {e21:.6g}"})
    buy_pullback = float(current["low"]) <= e21
    checks.append({"rule": "BUY 3 pullback touches EMA21", "ok": buy_pullback,
                   "detail": f"low {float(current['low']):.6g} <= EMA21 {e21:.6g}"})
    buy_confirm = c > prev_high and c > o
    checks.append({"rule": "BUY 4 bullish close above previous high", "ok": buy_confirm,
                   "detail": f"close {c:.6g} vs prev high {prev_high:.6g}, open {o:.6g}"})

    if buy_trend and buy_pullback and buy_confirm:
        reference_entry = c
        risk = reference_entry - stop_low
        if risk <= 0:
            return {"status": S_INVALID_RISK, "strategy": "TPC-15",
                    "reason": (f"reference entry {reference_entry:.6g} not above the "
                               f"5-candle low {stop_low:.6g} - invalid risk")}
        setup_id = setup_id_for("BUY", conf_open)
        if emitted is not None:
            if setup_id in emitted:
                return {"status": S_DUPLICATE, "strategy": "TPC-15", "setup_id": setup_id,
                        "reason": "this confirmation setup was already signalled"}
            emitted.add(setup_id)
        return {"status": S_SIGNAL, "strategy": "TPC-15", "direction": "BUY",
                "timeframe": CONFIG["timeframe"], "setup_id": setup_id,
                "timestamp": _iso(eval_ts),
                "confirmation_candle": _iso(conf_open),
                "entry_rule": "NEXT_CANDLE_OPEN",
                "reference_entry": reference_entry,
                "stop_loss": stop_low,
                "take_profit": reference_entry + risk * TP_R,   # provisional (reference-based)
                "risk": risk, "risk_reward": "1:1.25",
                "tp_multiple": TP_R,
                "stop_window": {"from": _iso(window.index[0]), "to": _iso(window.index[-1]),
                                "candles": LOOKBACK,
                                "definition": "the five completed candles before the confirmation candle"},
                "reasons": list(REASON_BUY), "checks": checks}

    # ---------------- SELL ----------------
    sell_trend = c < e200 and e9 < e21
    sell_pullback = float(current["high"]) >= e21
    sell_confirm = c < prev_low and c < o
    checks.append({"rule": "SELL 1 price below EMA200", "ok": c < e200,
                   "detail": f"close {c:.6g} vs EMA200 {e200:.6g}"})
    checks.append({"rule": "SELL 2 EMA9 below EMA21", "ok": e9 < e21,
                   "detail": f"EMA9 {e9:.6g} vs EMA21 {e21:.6g}"})
    checks.append({"rule": "SELL 3 pullback touches EMA21", "ok": sell_pullback,
                   "detail": f"high {float(current['high']):.6g} >= EMA21 {e21:.6g}"})
    checks.append({"rule": "SELL 4 bearish close below previous low", "ok": sell_confirm,
                   "detail": f"close {c:.6g} vs prev low {prev_low:.6g}, open {o:.6g}"})

    if sell_trend and sell_pullback and sell_confirm:
        reference_entry = c
        risk = stop_high - reference_entry
        if risk <= 0:
            return {"status": S_INVALID_RISK, "strategy": "TPC-15",
                    "reason": (f"reference entry {reference_entry:.6g} not below the "
                               f"5-candle high {stop_high:.6g} - invalid risk")}
        setup_id = setup_id_for("SELL", conf_open)
        if emitted is not None:
            if setup_id in emitted:
                return {"status": S_DUPLICATE, "strategy": "TPC-15", "setup_id": setup_id,
                        "reason": "this confirmation setup was already signalled"}
            emitted.add(setup_id)
        return {"status": S_SIGNAL, "strategy": "TPC-15", "direction": "SELL",
                "timeframe": CONFIG["timeframe"], "setup_id": setup_id,
                "timestamp": _iso(eval_ts),
                "confirmation_candle": _iso(conf_open),
                "entry_rule": "NEXT_CANDLE_OPEN",
                "reference_entry": reference_entry,
                "stop_loss": stop_high,
                "take_profit": reference_entry - risk * TP_R,   # provisional (reference-based)
                "risk": risk, "risk_reward": "1:1.25",
                "tp_multiple": TP_R,
                "stop_window": {"from": _iso(window.index[0]), "to": _iso(window.index[-1]),
                                "candles": LOOKBACK,
                                "definition": "the five completed candles before the confirmation candle"},
                "reasons": list(REASON_SELL), "checks": checks}

    return {"status": S_NO_SIGNAL, "strategy": "TPC-15", "checks": checks}


# ---------------------------------------------------------------------------
# 2) entry finalization - the guide's NEXT_CANDLE_OPEN rule. Risk, stop
#    validity and take-profit are computed FROM THE ACTUAL ENTRY.
# ---------------------------------------------------------------------------
def finalize_entry(setup: Dict[str, Any], next_open: float,
                   next_open_time_utc=None) -> Dict[str, Any]:
    """Attach the actual entry (the next candle's open) to a detected setup.

    next_open_time_utc, when given, must still be inside the session - the
    session end is enforced consistently: an entry at/after 18:00 UTC is
    refused, never shifted. Risk <= 0 (entry gapped through the stop) is
    reported INVALID_RISK, never repaired."""
    if not setup or setup.get("status") != S_SIGNAL:
        return {"status": S_NO_SIGNAL, "strategy": "TPC-15",
                "reason": "no detected setup to finalize"}
    try:
        entry = float(next_open)
    except (TypeError, ValueError):
        return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                "reason": "next candle open missing - entry cannot be computed"}
    if not (entry > 0) or entry != entry or entry in (float("inf"),):
        return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                "reason": "next candle open is not a valid price"}

    if next_open_time_utc is not None and not inside_session(next_open_time_utc):
        return {"status": S_OUT_OF_SESSION, "strategy": "TPC-15",
                "setup_id": setup.get("setup_id"),
                "reason": ("entry candle opens at/after the session end "
                           f"{CONFIG['session_end_utc']} UTC - no trade")}

    sl = float(setup["stop_loss"])
    direction = setup["direction"]
    risk = (entry - sl) if direction == "BUY" else (sl - entry)
    if risk <= 0:
        return {"status": S_INVALID_RISK, "strategy": "TPC-15",
                "setup_id": setup.get("setup_id"),
                "reason": (f"entry {entry:.6g} is on the wrong side of the stop "
                           f"{sl:.6g} for a {direction} - invalid risk, no trade")}

    tp = entry + risk * TP_R if direction == "BUY" else entry - risk * TP_R
    out = dict(setup)
    out.update({
        "entry": entry,
        "entry_source": "NEXT_CANDLE_OPEN",
        "entry_time": _iso(next_open_time_utc) if next_open_time_utc is not None else None,
        "stop_loss": sl,
        "risk": risk,
        "take_profit": tp,
        "risk_reward": "1:1.25",
        "reference_entry": setup.get("reference_entry"),
        "status": S_SIGNAL,
        "final": True,
    })
    return out


# ---------------------------------------------------------------------------
# 3) all-in-one convenience: candles[-2] is the confirmation candle,
#    candles[-1] the entry candle (ONLY its open and open time are read).
# ---------------------------------------------------------------------------
def evaluate(candles, current_time_utc=None, emitted: Optional[set] = None,
             emas: Optional[Tuple[pd.Series, pd.Series, pd.Series]] = None) -> Dict[str, Any]:
    """Detect on the closed history and finalize at the last candle's open.

    Needs LOOKBACK + 1 confirmation candles + the entry candle = at least
    7 rows; with internally computed EMAs the trend period applies (>= 200
    closed candles before the entry candle)."""
    try:
        df = _frame(candles)
    except ValueError as exc:
        return {"status": S_INSUFFICIENT, "reason": str(exc), "strategy": "TPC-15"}
    if len(df) < LOOKBACK + 2:
        return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                "reason": (f"need at least {LOOKBACK + 2} candles "
                           f"(5 stop + 1 confirmation + 1 entry), got {len(df)}")}
    hist = df.iloc[:-1]                      # everything through the confirmation
    if emas is None:
        if len(hist) < int(CONFIG["trend_ema_period"]):
            return {"status": S_INSUFFICIENT, "strategy": "TPC-15",
                    "reason": (f"EMA {CONFIG['trend_ema_period']} needs "
                               f"{CONFIG['trend_ema_period']} closed candles, got {len(hist)}")}
        emas = compute_emas(hist["close"])
    setup = evaluate_setup(hist, ema9=emas[0], ema21=emas[1], ema200=emas[2],
                           current_time_utc=current_time_utc, emitted=emitted)
    if setup.get("status") != S_SIGNAL:
        return setup
    entry_candle = df.iloc[-1]
    return finalize_entry(setup, float(entry_candle["open"]),
                          next_open_time_utc=df.index[-1])
