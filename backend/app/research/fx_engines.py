"""FOREXMIND FX RESEARCH CANDIDATE ENGINES (owner handoff 2026-10-09).

Four XAUUSD research strategies, implemented EXACTLY per
FOREXMIND_STRATEGY_SPECIFICATIONS.md - entry, stop, target, session,
confirmation and sizing rules are verbatim from the specification.
Nothing here is optimized, invented, or tuned.

ADAPTATION NOTE (handoff item 2): the referenced `strategy-engine.js` file
was not supplied with the handoff; the specification document is the source
of truth. This repo's backend is Python/FastAPI, so the smallest mechanical
adaptation is a direct Python port of the documented rules with the module
contract preserved (evaluate -> status/reason/levels, executionEnabled
always False). No rule was altered in the port.

HARD RULES (FOREXMIND_AGENT_INSTRUCTIONS.md):
- RESEARCH ONLY. These functions NEVER touch order placement, the MT5
  bridge, or strategy switching - this module imports nothing from
  app.execution and never writes signals.
- Every response carries executionEnabled: False.
- Statuses: NO_SIGNAL | INSUFFICIENT_DATA | RESEARCH_CANDIDATE | SKIP_RISK.
- Sizing fails closed when the minimum lot exceeds the risk budget.
- Not profitable, not validated - explicit first-pass rules only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

# Shared research-sizing assumptions (spec: "research assumptions only")
SIZING = {
    "balance": 500.0,        # USD
    "risk_pct": 1.0,         # % of balance
    "contract_size": 100.0,  # XAUUSD per spec - verify with broker before use
    "min_lot": 0.01,
    "lot_step": 0.01,
    "max_lot": 1.0,
}

CANDIDATES = [
    {"id": "h1_breakout_v1", "name": "H1 Trend Breakout", "version": "v1"},
    {"id": "opening_range_v1", "name": "Opening Range Breakout", "version": "v1"},
    {"id": "liquidity_sweep_v1", "name": "Liquidity Sweep Reversal", "version": "v1"},
    {"id": "crt_4h_15m_v1", "name": "CRT 4H + 15M", "version": "v1"},
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _prepare(df: Any) -> Optional[pd.DataFrame]:
    """Completed, chronological, deduplicated, tz-aware candles - or None."""
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
        return None
    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return None
    out = df[list(need)].apply(pd.to_numeric, errors="coerce").dropna()
    if len(out) == 0:
        return None
    try:
        idx = pd.to_datetime(df.index, utc=True)
    except Exception:
        return None
    out = out.set_axis(idx)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if len(out) else None


def _wilder_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1:
        return None
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    v = float(atr.iloc[-1])
    return v if v == v else None


def _wilder_adx(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Standard Wilder ADX(14) estimate (spec: "ADX(14) estimate <= 20")."""
    if len(df) < 2 * period + 1:
        return None
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    # 0/0 in a perfectly flat market = zero directional movement -> DX 0
    # (honest chop), never a crash.
    import numpy as np
    den = (plus_di + minus_di).to_numpy(dtype="float64")
    num = (plus_di - minus_di).abs().to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(den > 0, 100.0 * num / den, 0.0)
    dx = pd.Series(dx, index=df.index).fillna(0.0)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    v = float(adx.iloc[-1])
    return v if v == v else None


def _sizing(entry: float, stop: float) -> Dict[str, Any]:
    """Research lot sizing - fails closed when min lot exceeds the budget."""
    risk_usd = SIZING["balance"] * SIZING["risk_pct"] / 100.0
    dist = abs(entry - stop)
    if dist <= 0:
        return {"lot": None, "risk_usd": risk_usd, "fail": "invalid stop distance"}
    raw_lot = risk_usd / (dist * SIZING["contract_size"])
    step = SIZING["lot_step"]
    lot = int(raw_lot / step) * step
    lot = round(max(0.0, min(lot, SIZING["max_lot"])), 10)
    min_risk = SIZING["min_lot"] * dist * SIZING["contract_size"]
    if min_risk > risk_usd + 1e-12:
        # spec: "fails closed if the minimum lot exceeds the risk budget"
        return {"lot": None, "risk_usd": risk_usd,
                "fail": (f"minimum lot {SIZING['min_lot']} would risk "
                         f"${round(min_risk, 2)} > ${round(risk_usd, 2)} budget")}
    if lot < SIZING["min_lot"]:
        return {"lot": None, "risk_usd": risk_usd,
                "fail": (f"sizing below minimum lot (raw {round(raw_lot, 4)} < "
                         f"{SIZING['min_lot']}) - risk budget not exceeded, "
                         "no valid research lot")}
    return {"lot": lot, "risk_usd": risk_usd, "fail": None}


def _result(cid: str, name: str, status: str, reason: str, **kw) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": cid, "name": name, "version": "v1", "market": "XAUUSD",
        "status": status, "reason": reason,
        "direction": kw.get("direction"), "entry": kw.get("entry"),
        "sl": kw.get("sl"), "tp": kw.get("tp"),
        "r_multiple": kw.get("r_multiple"),
        "break_even_marker_r": kw.get("be_r"),
        "signal_time": kw.get("signal_time"),
        "sizing": kw.get("sizing"),
        "executionEnabled": False,          # HARD RULE - never changes
        "label": "RESEARCH ONLY",
    }
    return out


def _r(level: float, entry: float, stop: float) -> float:
    return round(abs(entry - stop), 10)


# --------------------------------------------------------------------------
# 1. H1 Trend Breakout - h1_breakout_v1
# --------------------------------------------------------------------------
def h1_breakout(df: Any) -> Dict[str, Any]:
    """Session 17:00-21:59 UTC; bullish H1 close above prior 20 highs;
    stop = lower of prior 10-bar low and entry - 1 ATR(14); target 1.25R."""
    name = "H1 Trend Breakout"
    d = _prepare(df)
    if d is None or len(d) < 22:
        return _result("h1_breakout_v1", name, "INSUFFICIENT_DATA",
                       f"need >= 22 completed H1 bars, got {0 if d is None else len(d)}")
    i = len(d) - 1
    ts = d.index[i]
    hour = ts.hour + ts.minute / 60.0
    if not (17.0 <= hour <= 21.99):        # 17:00-21:59 UTC window
        return _result("h1_breakout_v1", name, "NO_SIGNAL",
                       f"latest H1 close {ts.strftime('%H:%M')} UTC outside "
                       "the 17:00-21:59 UTC session")
    c = float(d["close"].iloc[i])
    o = float(d["open"].iloc[i])
    prior20_high = float(d["high"].iloc[i - 20:i].max())
    if not (c > o and c > prior20_high):
        return _result("h1_breakout_v1", name, "NO_SIGNAL",
                       "no bullish close above the prior 20-bar high "
                       f"(close {round(c, 5):g} vs channel {round(prior20_high, 5):g})")
    entry = c
    prior10_low = float(d["low"].iloc[i - 10:i].min())
    atr = _wilder_atr(d, 14)
    if atr is None:
        return _result("h1_breakout_v1", name, "INSUFFICIENT_DATA",
                       "ATR(14) unavailable")
    stop = min(prior10_low, entry - atr)
    risk = _r(level=stop, entry=entry, stop=stop)
    if risk <= 0 or stop >= entry:
        return _result("h1_breakout_v1", name, "SKIP_RISK",
                       "stop is not below entry - invalid risk geometry",
                       direction="LONG", entry=round(entry, 5), sl=round(stop, 5))
    sizing = _sizing(entry, stop)
    if sizing["fail"]:
        return _result("h1_breakout_v1", name, "SKIP_RISK",
                       f"sizing fail-closed: {sizing['fail']}",
                       direction="LONG", entry=round(entry, 5), sl=round(stop, 5),
                       tp=round(entry + 1.25 * risk, 5), r_multiple=1.25,
                       signal_time=ts.isoformat(), sizing=sizing)
    return _result("h1_breakout_v1", name, "RESEARCH_CANDIDATE",
                   "bullish H1 close above prior 20-bar high inside the "
                   "17:00-21:59 UTC session",
                   direction="LONG", entry=round(entry, 5), sl=round(stop, 5),
                   tp=round(entry + 1.25 * risk, 5), r_multiple=1.25,
                   be_r=0.5, signal_time=ts.isoformat(), sizing=sizing)


# --------------------------------------------------------------------------
# 2. Opening Range Breakout - opening_range_v1
# --------------------------------------------------------------------------
def opening_range(df: Any) -> Dict[str, Any]:
    """OR 13:30-13:59 UTC (six completed M5 bars); entry 14:00-16:59 UTC;
    bullish M5 close above the day's OR high; stop OR low; target 1.5R."""
    name = "Opening Range Breakout"
    d = _prepare(df)
    if d is None or len(d) < 6:
        return _result("opening_range_v1", name, "INSUFFICIENT_DATA",
                       f"need >= 6 completed M5 bars, got {0 if d is None else len(d)}")
    i = len(d) - 1
    ts = d.index[i]
    minutes = ts.hour * 60 + ts.minute
    if not (14 * 60 <= minutes <= 16 * 60 + 59):     # 14:00-16:59 UTC
        return _result("opening_range_v1", name, "NO_SIGNAL",
                       f"latest M5 close {ts.strftime('%H:%M')} UTC outside the "
                       "14:00-16:59 entry window")
    day = ts.date()
    day_mask = d.index.date == day
    or_mask = day_mask & (d.index.hour * 60 + d.index.minute >= 13 * 60 + 30) \
        & (d.index.hour * 60 + d.index.minute < 14 * 60)
    or_bars = d[or_mask]
    if len(or_bars) < 6:
        return _result("opening_range_v1", name, "INSUFFICIENT_DATA",
                       f"opening range has {len(or_bars)}/6 completed M5 bars "
                       f"for {day.isoformat()}")
    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    c = float(d["close"].iloc[i])
    o = float(d["open"].iloc[i])
    if not (c > o and c > or_high):
        return _result("opening_range_v1", name, "NO_SIGNAL",
                       "no bullish M5 close above the opening-range high "
                       f"(close {round(c, 5):g} vs OR high {round(or_high, 5):g})")
    entry = c
    stop = or_low
    risk = _r(level=stop, entry=entry, stop=stop)
    if risk <= 0:
        return _result("opening_range_v1", name, "SKIP_RISK",
                       "opening-range low is not below entry - invalid risk geometry",
                       direction="LONG", entry=round(entry, 5), sl=round(stop, 5))
    sizing = _sizing(entry, stop)
    if sizing["fail"]:
        return _result("opening_range_v1", name, "SKIP_RISK",
                       f"sizing fail-closed: {sizing['fail']}",
                       direction="LONG", entry=round(entry, 5), sl=round(stop, 5),
                       tp=round(entry + 1.5 * risk, 5), r_multiple=1.5,
                       signal_time=ts.isoformat(), sizing=sizing)
    return _result("opening_range_v1", name, "RESEARCH_CANDIDATE",
                   "bullish M5 close above the 13:30-13:59 UTC opening-range "
                   "high inside the entry window",
                   direction="LONG", entry=round(entry, 5), sl=round(stop, 5),
                   tp=round(entry + 1.5 * risk, 5), r_multiple=1.5,
                   be_r=1.0, signal_time=ts.isoformat(), sizing=sizing)


# --------------------------------------------------------------------------
# 3. Liquidity Sweep Reversal - liquidity_sweep_v1
# --------------------------------------------------------------------------
def liquidity_sweep(df: Any) -> Dict[str, Any]:
    """M15, >=35 bars, ADX(14) <= 20; sweep of the prior 20-bar extreme with
    close back inside + matching candle direction; stop = sweep extreme;
    target 0.75R; no break-even marker."""
    name = "Liquidity Sweep Reversal"
    d = _prepare(df)
    if d is None or len(d) < 35:
        return _result("liquidity_sweep_v1", name, "INSUFFICIENT_DATA",
                       f"need >= 35 completed M15 bars, got {0 if d is None else len(d)}")
    i = len(d) - 1
    ts = d.index[i]
    adx = _wilder_adx(d, 14)
    if adx is None:
        return _result("liquidity_sweep_v1", name, "INSUFFICIENT_DATA",
                       "ADX(14) unavailable")
    if adx > 20:
        return _result("liquidity_sweep_v1", name, "NO_SIGNAL",
                       f"ADX(14) {round(adx, 1)} above the <= 20 chop filter")
    c = float(d["close"].iloc[i])
    o = float(d["open"].iloc[i])
    lo = float(d["low"].iloc[i])
    hi = float(d["high"].iloc[i])
    prior20_low = float(d["low"].iloc[i - 20:i].min())
    prior20_high = float(d["high"].iloc[i - 20:i].max())

    if lo < prior20_low and c > prior20_low and c > o:        # long sweep
        direction, stop, entry = "LONG", lo, c
        reason = (f"swept below the prior 20-bar low {round(prior20_low, 5):g} "
                  "and closed back above it, bullish close")
    elif hi > prior20_high and c < prior20_high and c < o:    # short sweep
        direction, stop, entry = "SHORT", hi, c
        reason = (f"swept above the prior 20-bar high {round(prior20_high, 5):g} "
                  "and closed back below it, bearish close")
    else:
        return _result("liquidity_sweep_v1", name, "NO_SIGNAL",
                       "no 20-bar liquidity sweep with close back inside and "
                       "matching candle direction")
    risk = _r(level=stop, entry=entry, stop=stop)
    if risk <= 0:
        return _result("liquidity_sweep_v1", name, "SKIP_RISK",
                       "sweep extreme is not beyond entry - invalid risk geometry",
                       direction=direction, entry=round(entry, 5), sl=round(stop, 5))
    sign = 1.0 if direction == "LONG" else -1.0
    sizing = _sizing(entry, stop)
    if sizing["fail"]:
        return _result("liquidity_sweep_v1", name, "SKIP_RISK",
                       f"sizing fail-closed: {sizing['fail']}",
                       direction=direction, entry=round(entry, 5), sl=round(stop, 5),
                       tp=round(entry + sign * 0.75 * risk, 5), r_multiple=0.75,
                       signal_time=ts.isoformat(), sizing=sizing)
    return _result("liquidity_sweep_v1", name, "RESEARCH_CANDIDATE", reason,
                   direction=direction, entry=round(entry, 5), sl=round(stop, 5),
                   tp=round(entry + sign * 0.75 * risk, 5), r_multiple=0.75,
                   be_r=None, signal_time=ts.isoformat(), sizing=sizing)


# --------------------------------------------------------------------------
# 4. CRT 4H + 15M - crt_4h_15m_v1
# --------------------------------------------------------------------------
def crt_4h_15m(h4: Any, m15: Any) -> Dict[str, Any]:
    """Previous completed H4 = CRT range; latest H4 = sweep/reclaim candle
    (caller supplies it completed). Key-level proxy: sweep extreme within
    1 x M15 ATR(14) of the prior H4 boundary. M15 confirms beyond the prior
    5-bar structure. Stop = H4 sweep extreme; target 1R; BE marker +0.5R."""
    name = "CRT 4H + 15M"
    h = _prepare(h4)
    m = _prepare(m15)
    if h is None or len(h) < 3 or m is None or len(m) < 22:
        return _result("crt_4h_15m_v1", name, "INSUFFICIENT_DATA",
                       f"need >= 3 H4 and >= 22 M15 bars, got "
                       f"{0 if h is None else len(h)} H4 / {0 if m is None else len(m)} M15")
    prev = h.iloc[-2]
    sweep = h.iloc[-1]
    prev_low = float(prev["low"])
    prev_high = float(prev["high"])
    m15_atr = _wilder_atr(m, 14)
    if m15_atr is None:
        return _result("crt_4h_15m_v1", name, "INSUFFICIENT_DATA",
                       "M15 ATR(14) unavailable")

    direction = None
    sweep_extreme = None
    boundary = None
    if float(sweep["low"]) < prev_low and prev_low < float(sweep["close"]) \
            and float(sweep["close"]) <= prev_high:
        direction, sweep_extreme, boundary = "LONG", float(sweep["low"]), prev_low
        setup = ("H4 swept below the previous H4 low and reclaimed it, "
                 "closing within the previous range")
    elif float(sweep["high"]) > prev_high and float(sweep["close"]) < prev_high \
            and float(sweep["close"]) >= prev_low:
        direction, sweep_extreme, boundary = "SHORT", float(sweep["high"]), prev_high
        setup = ("H4 swept above the previous H4 high and rejected it, "
                 "closing within the previous range")
    else:
        return _result("crt_4h_15m_v1", name, "NO_SIGNAL",
                       "no CRT sweep/reclaim of the previous H4 range")

    # key-level proxy: sweep extreme within 1 M15 ATR(14) of the boundary
    if abs(sweep_extreme - boundary) > m15_atr:
        return _result("crt_4h_15m_v1", name, "NO_SIGNAL",
                       f"sweep extreme {round(sweep_extreme, 5):g} is more than "
                       f"1 M15 ATR({round(m15_atr, 5):g}) from the prior H4 "
                       "boundary - key-level proxy fails")

    # M15 confirmation: close beyond the previous five-bar structure + direction
    j = len(m) - 1
    mc = float(m["close"].iloc[j])
    mo = float(m["open"].iloc[j])
    if direction == "LONG":
        struct = float(m["high"].iloc[j - 5:j].max())
        confirmed = mc > struct and mc > mo
    else:
        struct = float(m["low"].iloc[j - 5:j].min())
        confirmed = mc < struct and mc < mo
    if not confirmed:
        return _result("crt_4h_15m_v1", name, "NO_SIGNAL",
                       f"{setup} - awaiting M15 close beyond the prior 5-bar "
                       f"structure ({round(struct, 5):g}) with matching direction")

    entry = mc
    stop = sweep_extreme
    risk = _r(level=stop, entry=entry, stop=stop)
    sign = 1.0 if direction == "LONG" else -1.0
    if risk <= 0:
        return _result("crt_4h_15m_v1", name, "SKIP_RISK",
                       "H4 sweep extreme is not beyond entry - invalid geometry",
                       direction=direction, entry=round(entry, 5), sl=round(stop, 5))
    sizing = _sizing(entry, stop)
    if sizing["fail"]:
        return _result("crt_4h_15m_v1", name, "SKIP_RISK",
                       f"sizing fail-closed: {sizing['fail']}",
                       direction=direction, entry=round(entry, 5), sl=round(stop, 5),
                       tp=round(entry + sign * 1.0 * risk, 5), r_multiple=1.0,
                       signal_time=m.index[j].isoformat(), sizing=sizing)
    return _result("crt_4h_15m_v1", name, "RESEARCH_CANDIDATE",
                   f"{setup}; M15 confirmed beyond the prior 5-bar structure",
                   direction=direction, entry=round(entry, 5), sl=round(stop, 5),
                   tp=round(entry + sign * 1.0 * risk, 5), r_multiple=1.0,
                   be_r=0.5, signal_time=m.index[j].isoformat(), sizing=sizing)


# --------------------------------------------------------------------------
# evaluator - the ONLY entry point the API uses
# --------------------------------------------------------------------------
def evaluate_all(h1: Any, m5: Any, m15: Any, h4: Any) -> Dict[str, Any]:
    """Evaluate all four candidates on COMPLETED candles. Pure research:
    imports nothing from app.execution, writes nothing, returns
    executionEnabled False on every candidate and on the envelope."""
    return {
        "market": "XAUUSD",
        "label": "RESEARCH ONLY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executionEnabled": False,
        "note": ("Research candidates only - never sent to the broker. "
                 "Sizing uses spec defaults (balance $500, risk 1%, contract "
                 "100); verify broker XAUUSD contract size, tick value, "
                 "commissions, spread and slippage before ever using sizing."),
        "candidates": [
            h1_breakout(h1),
            opening_range(m5),
            liquidity_sweep(m15),
            crt_4h_15m(h4, m15),
        ],
    }
