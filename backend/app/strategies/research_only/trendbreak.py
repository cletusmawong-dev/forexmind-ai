"""Strategy 1 (research candidate): "TrendBreak" - H1 breakout in the H4
trend direction. Owner spec 2026-10-24, implemented verbatim. Entry/exit
rules only - no sizing, no account risk (the app handles that if promoted).

Spec (verbatim source of truth):
  Markets EURUSD, GBPUSD, USDJPY, XAUUSD (XAGUSD excluded - lost in
  testing). H1 entries, H4 context.
  Indicators: ATR(14) on H1 as the SIMPLE ROLLING MEAN of True Range.
  H4 trend from COMPLETED H4 bars only (an H1 bar sees only H4 bars that
  closed at or before its own close): UP = EMA50 > EMA200 AND H4 close >
  EMA200; DOWN = mirror; otherwise no trade. EMAs: span n, adjust=False.
  Channel: HH = highest HIGH of the PREVIOUS 120 H1 bars (excluding the
  current bar); LL = lowest LOW of the same window.
  Entry (evaluated when an H1 bar closes): LONG if H4 UP and close > HH and
  the PREVIOUS H1 close was NOT already > its own HH (first breakout bar
  only); SHORT mirrored. Enter at the OPEN of the next H1 bar (market).
  One open position per symbol; ignore new signals on that symbol while a
  trade is open. Skip if the stop distance is outside 0.5-3.5 ATR (with the
  2.5 ATR stop this never triggers - the guard is kept as specified).
  Exit: SL = entry -/+ 2.5 x ATR(14) of the SIGNAL bar, never moved. TP =
  entry +/- 3 x stop distance (1:3). Time exit at the close of the 96th H1
  bar. If a bar touches both stop and target, COUNT THE STOP (worst case).
  Stop gaps fill at the bar open when the open is already beyond the stop.
  Builder note: N=240 channel was tested weaker on EURUSD/GBPUSD; 120 is
  the default. Do not re-optimise.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

SPEC = {
    "name": "TrendBreak",
    "entry_tf": "1H",
    "context_tf": "4H",
    "markets": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "excluded_markets": ["XAGUSD"],
    "atr_period": 14,
    "channel_n": 120,
    "stop_atr": 2.5,
    "tp_multiple": 3.0,
    "time_exit_bars": 96,
    "stop_distance_bounds_atr": (0.5, 3.5),
    "reference": ("owner backtest (costs in): win ~32%, avg win +2.4R, "
                  "avg loss -1.0R, PF 1.1-1.2, expectancy +0.05R..+0.12R, "
                  "~120-175 trades/yr across markets"),
}

SIGNAL = "SIGNAL"
NO_SIGNAL = "NO_SIGNAL"
INSUFFICIENT = "INSUFFICIENT_DATA"
OUT_OF_SPEC_MARKET = "OUT_OF_SPEC_MARKET"


# ---------------------------------------------------------------------------
# frame handling + as-of context (no lookahead)
# ---------------------------------------------------------------------------
def _frame(candles, name: str) -> pd.DataFrame:
    if candles is None:
        raise ValueError(f"{name} candle data is missing (None)")
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(list(candles))
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{name} frame missing column '{col}'")
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    elif "ts" in df.columns:
        idx = pd.to_datetime(df["ts"], unit="s", utc=True)
    elif "time" in df.columns:
        idx = pd.to_datetime(df["time"], utc=True)
    else:
        raise ValueError(f"{name} frame needs a UTC datetime index or ts/time column")
    idx = pd.DatetimeIndex(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = df[["open", "high", "low", "close"]].astype(float)
    out.index = idx
    return out.sort_index()


def _atr_simple(close: pd.Series, high: pd.Series, low: pd.Series,
                period: int) -> pd.Series:
    """True Range with the simple rolling mean (the spec's exact ATR)."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _trend_state(h4: pd.DataFrame) -> pd.DataFrame:
    """H4 EMA50/EMA200 trend per completed H4 bar: +1 UP, -1 DOWN, 0 no-trade."""
    ema50 = h4["close"].ewm(span=50, adjust=False).mean()
    ema200 = h4["close"].ewm(span=200, adjust=False).mean()
    up = (ema50 > ema200) & (h4["close"] > ema200)
    down = (ema50 < ema200) & (h4["close"] < ema200)
    return pd.DataFrame({"ema50": ema50, "ema200": ema200,
                         "trend": up.astype(int) - down.astype(int)})


def _trend_asof(trend_df: pd.DataFrame, t: pd.Timestamp) -> int:
    """Trend of the last H4 bar CLOSED at or before t (as-of read)."""
    sub = trend_df.loc[trend_df.index <= t]
    if sub.empty:
        return 0
    return int(sub["trend"].iloc[-1])


def check_market(market: Optional[str]) -> Optional[str]:
    if market is None:
        return None
    m = market.upper()
    if m in SPEC["excluded_markets"]:
        return OUT_OF_SPEC_MARKET
    if m not in SPEC["markets"]:
        return OUT_OF_SPEC_MARKET
    return None


# ---------------------------------------------------------------------------
# signal evaluation on the latest CLOSED H1 bar
# ---------------------------------------------------------------------------
def evaluate(h1, h4, market: Optional[str] = None,
             current_time_utc=None) -> Dict[str, Any]:
    """Evaluate the latest closed H1 bar for a first-bar breakout.

    Returns a structured dict. A SIGNAL carries the signal-bar ATR and the
    direction; the FINAL entry/SL/TP attach at the next bar's open via
    finalize() (the spec's next-open market entry), or directly in
    backtest()."""
    bad = check_market(market)
    if bad:
        return {"status": bad, "strategy": SPEC["name"],
                "reason": f"{market} is not in the spec's market list"}
    try:
        df = _frame(h1, "H1")
        h4f = _frame(h4, "H4")
    except ValueError as exc:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"], "reason": str(exc)}

    n = len(df)
    need = SPEC["channel_n"] + 2
    if n < need or len(h4f) < 200:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": (f"need >= {need} H1 bars and >= 200 H4 bars, "
                           f"got {n}/{len(h4f)}")}

    atr = _atr_simple(df["close"], df["high"], df["low"], SPEC["atr_period"])
    hh = df["high"].shift(1).rolling(SPEC["channel_n"]).max()
    ll = df["low"].shift(1).rolling(SPEC["channel_n"]).min()
    trend_df = _trend_state(h4f)

    i = n - 1
    t_close = df.index[i]
    eval_ts = pd.Timestamp(current_time_utc) if current_time_utc is not None else t_close
    if eval_ts.tzinfo is None:
        eval_ts = eval_ts.tz_localize("UTC")

    trend = _trend_asof(trend_df, t_close)
    a = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
    hh_i, ll_i = hh.iloc[i], ll.iloc[i]
    c, c_prev = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])
    hh_prev, ll_prev = hh.iloc[i - 1], ll.iloc[i - 1]
    checks: List[Dict[str, Any]] = [
        {"rule": "H4 trend", "ok": trend != 0,
         "detail": f"trend {'UP' if trend == 1 else 'DOWN' if trend == -1 else 'NONE'} "
                   f"as of {t_close} (completed H4 bars only)"},
        {"rule": "channel", "ok": bool(pd.notna(hh_i) and pd.notna(ll_i)),
         "detail": f"HH={hh_i} LL={ll_i} (previous {SPEC['channel_n']} bars)"}]

    long_break = pd.notna(hh_i) and c > float(hh_i)
    short_break = pd.notna(ll_i) and c < float(ll_i)
    first_long = long_break and pd.notna(hh_prev) and not (c_prev > float(hh_prev))
    first_short = short_break and pd.notna(ll_prev) and not (c_prev < float(ll_prev))

    direction = None
    if trend == 1 and first_long:
        direction = "BUY"
    elif trend == -1 and first_short:
        direction = "SELL"
    checks.append({"rule": "first-bar breakout", "ok": direction is not None,
                   "detail": f"close {c} vs HH {hh_i} / LL {ll_i}; prev close "
                             f"{c_prev} already beyond: "
                             f"{bool(pd.notna(hh_prev) and c_prev > float(hh_prev)) if long_break else bool(pd.notna(ll_prev) and c_prev < float(ll_prev)) if short_break else 'n/a'}"})
    if direction is None:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "timestamp": str(t_close)}

    if a <= 0:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": "ATR not available at the signal bar"}
    lo, hi = SPEC["stop_distance_bounds_atr"]
    # The stop is DEFINED as 2.5 ATR; the guard is the spec's own no-op
    # safety (kept verbatim) - it can only fire if the definition changed.
    stop_atr = SPEC["stop_atr"]
    if not (lo <= stop_atr <= hi):
        return {"status": NO_SIGNAL, "strategy": SPEC["name"],
                "reason": f"stop distance {stop_atr} ATR outside the spec "
                          f"bounds {lo}-{hi} ATR"}

    return {"status": SIGNAL, "strategy": SPEC["name"], "direction": direction,
            "market": market, "entry_tf": SPEC["entry_tf"],
            "signal_bar": str(t_close), "timestamp": str(t_close),
            "atr": a, "stop_atr_multiple": stop_atr,
            "tp_multiple": SPEC["tp_multiple"],
            "entry_rule": "NEXT_BAR_OPEN",
            "time_exit_bars": SPEC["time_exit_bars"],
            "checks": checks}


def finalize(signal: Dict[str, Any], next_open: float,
             next_open_time=None) -> Dict[str, Any]:
    """Attach the NEXT-BAR-OPEN entry and compute SL/TP from it (levels are
    defined FROM the entry, so the fill price moves the levels - by design)."""
    if not signal or signal.get("status") != SIGNAL:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"],
                "reason": "no signal to finalize"}
    try:
        entry = float(next_open)
    except (TypeError, ValueError):
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": "next bar open missing - entry cannot be computed"}
    a = float(signal["atr"])
    risk = SPEC["stop_atr"] * a
    sign = 1.0 if signal["direction"] == "BUY" else -1.0
    sl = entry - sign * risk
    tp = entry + sign * risk * SPEC["tp_multiple"]
    out = dict(signal)
    out.update({"status": SIGNAL, "final": True, "entry": entry,
                "entry_time": str(next_open_time) if next_open_time is not None else None,
                "stop_loss": sl, "take_profit": tp, "risk_distance": risk,
                "risk_reward": f"1:{SPEC['tp_multiple']:g}"})
    return out


# ---------------------------------------------------------------------------
# backtest simulator - entry/exit rules ONLY, 1 unit, R multiples
# ---------------------------------------------------------------------------
def backtest(h1, h4, market: str) -> Dict[str, Any]:
    """Replay the spec on closed H1 bars. One open position per symbol;
    new signals on that symbol are ignored while a trade is open."""
    bad = check_market(market)
    if bad:
        return {"status": bad, "strategy": SPEC["name"], "trades": [],
                "summary": {"n": 0}}
    df = _frame(h1, "H1")
    h4f = _frame(h4, "H4")
    atr = _atr_simple(df["close"], df["high"], df["low"], SPEC["atr_period"])
    hh = df["high"].shift(1).rolling(SPEC["channel_n"]).max()
    ll = df["low"].shift(1).rolling(SPEC["channel_n"]).min()
    trend_df = _trend_state(h4f)

    trades: List[Dict[str, Any]] = []
    i = max(SPEC["channel_n"] + 1, 1)
    n = len(df)
    while i < n - 1:                       # need the next bar to enter
        t = df.index[i]
        trend = _trend_asof(trend_df, t)
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0 or trend == 0:
            i += 1
            continue
        c, c_prev = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])
        hh_i, ll_i = hh.iloc[i], ll.iloc[i]
        direction = None
        if trend == 1 and pd.notna(hh_i) and c > float(hh_i):
            hp = hh.iloc[i - 1]
            if pd.isna(hp) or not (c_prev > float(hp)):
                direction = "BUY"
        elif trend == -1 and pd.notna(ll_i) and c < float(ll_i):
            lp = ll.iloc[i - 1]
            if pd.isna(lp) or not (c_prev < float(lp)):
                direction = "SELL"
        if direction is None:
            i += 1
            continue

        # entries/exits use bars i+1 .. i+96 (the next bar and the 96 after it)
        end = min(n - 1, i + SPEC["time_exit_bars"])
        j = i + 1
        entry = float(df["open"].iloc[j])
        risk = SPEC["stop_atr"] * float(a)
        sign = 1.0 if direction == "BUY" else -1.0
        sl = entry - sign * risk
        tp = entry + sign * risk * SPEC["tp_multiple"]
        exit_px = exit_reason = None
        exit_t = None
        while j <= end:
            bar = df.iloc[j]
            o, hi_, lo_, cl = (float(bar["open"]), float(bar["high"]),
                               float(bar["low"]), float(bar["close"]))
            hit_stop = (o <= sl) if direction == "BUY" else (o >= sl)
            if hit_stop:                   # gap through the stop fills at the open
                exit_px, exit_reason = o, "STOP_GAP_OPEN"
            elif (lo_ <= sl) if direction == "BUY" else (hi_ >= sl):
                exit_px, exit_reason = sl, "STOP"
            elif (hi_ >= tp) if direction == "BUY" else (lo_ <= tp):
                exit_px, exit_reason = tp, "TAKE_PROFIT"
            elif j == end:
                exit_px, exit_reason = cl, "TIME_EXIT_96"
            if exit_px is not None:
                exit_t = df.index[j]
                break
            j += 1
        r = ((exit_px - entry) / risk) if direction == "BUY" \
            else ((entry - exit_px) / risk)
        trades.append({
            "market": market, "direction": direction,
            "signal_bar": str(t), "entry_time": str(df.index[i + 1]),
            "entry": round(entry, 6), "stop_loss": round(sl, 6),
            "take_profit": round(tp, 6), "exit_time": str(exit_t),
            "exit": round(exit_px, 6), "exit_reason": exit_reason,
            "r_multiple": round(r, 4), "bars_held": j - (i + 1) + 1})
        i = j + 1                          # one open position per symbol

    return {"status": "OK", "strategy": SPEC["name"], "trades": trades,
            "summary": _summarize(trades), "reference": SPEC["reference"]}


def _summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0}
    rs = [t["r_multiple"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    return {"n": len(rs),
            "win_rate": round(len(wins) / len(rs), 4),
            "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else 0.0,
            "profit_factor": round(gross_w / gross_l, 4) if gross_l else None,
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "total_r": round(sum(rs), 4)}
