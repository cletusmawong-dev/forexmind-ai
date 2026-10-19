"""FOREXMIND - H1 Trend Breakout (owner spec 2026-10-10, SOURCE OF TRUTH).

XAUUSD. Signal timeframe H1 (completed candles, UTC). Entry reference: the
open of the first M15 candle after H1 confirmation. Indicators: EMA20/50/200,
ATR14, ADX14 - exactly as specified, nothing added or reinterpreted.

LONG : close > highest(high[1], 20) + ATR14*0.05
       AND EMA20_H1 > EMA50_H1 AND EMA20_H4 > EMA50_H4 AND EMA50_H4 > EMA200_H4
       AND ADX14_H1 > 25
SHORT: close < lowest(low[1], 20) - ATR14*0.05
       AND EMA20_H1 < EMA50_H1 AND EMA20_H4 < EMA50_H4 AND EMA50_H4 < EMA200_H4
       AND ADX14_H1 > 25
Stop : entry -/+ 1.2 * ATR14_H1.  Target: 1.25 x risk.  BE marker: +0.5R.
No session filter exists in this spec - none is applied (rule 1).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .indicators import ema, prepare, result, wilder_adx, wilder_atr

STRATEGY_ID = "strategy_h1_trend_breakout"
ATR_STOP_MULT = 1.2
TP_R = 1.25
BE_MARKER_R = 0.5
MIN_H1 = 60          # 20-lookback + ADX/ATR/EMA warmup headroom
MIN_H4 = 200         # EMA200_H4 needs 200 completed H4 bars
ADX_MIN = 25.0
BREAK_BUFFER_MULT = 0.05


def evaluate(h1: Optional[pd.DataFrame], m15: Optional[pd.DataFrame],
             h4: Optional[pd.DataFrame]) -> Dict[str, Any]:
    h = prepare(h1)
    m = prepare(m15)
    q = prepare(h4)
    if h is None or len(h) < MIN_H1:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, [],
                      f"need >= {MIN_H1} completed H1 bars, got {0 if h is None else len(h)}")
    if q is None or len(q) < MIN_H4:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, [],
                      f"need >= {MIN_H4} completed H4 bars for EMA200_H4, got {0 if q is None else len(q)}")
    if m is None or m.empty:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, [],
                      "no completed M15 bars for the entry reference")

    sig = h.iloc[-1]
    prior = h.iloc[-21:-1]                       # high[1]..high[20]: bars BEFORE the signal bar
    hh = float(prior["high"].max())
    ll = float(prior["low"].min())
    atr = wilder_atr(h, 14)
    adx = wilder_adx(h, 14)
    if atr is None or adx is None:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, [],
                      "ATR14/ADX14 not computable on the H1 history")
    buf = atr * BREAK_BUFFER_MULT
    e20h, e50h = ema(h["close"], 20), ema(h["close"], 50)
    e20q, e50q, e200q = (ema(q["close"], 20), ema(q["close"], 50),
                         ema(q["close"], 200))
    close = float(sig["close"])

    c_break_up = close > hh + buf
    c_break_dn = close < ll - buf
    c_trend_h1_up = float(e20h.iloc[-1]) > float(e50h.iloc[-1])
    c_trend_h1_dn = float(e20h.iloc[-1]) < float(e50h.iloc[-1])
    c_trend_h4_up = (float(e20q.iloc[-1]) > float(e50q.iloc[-1])
                     and float(e50q.iloc[-1]) > float(e200q.iloc[-1]))
    c_trend_h4_dn = (float(e20q.iloc[-1]) < float(e50q.iloc[-1])
                     and float(e50q.iloc[-1]) < float(e200q.iloc[-1]))
    c_adx = adx > ADX_MIN

    def chk(label, ok, detail):
        return {"label": label, "ok": bool(ok), "detail": detail}

    checks = [
        chk("H1 breakout above prior-20 high + 0.05xATR", c_break_up,
            f"close {close:.5g} vs {hh + buf:.5g} (high20 {hh:.5g} + 0.05xATR14 {buf:.5g})"),
        chk("H1 breakdown below prior-20 low - 0.05xATR", c_break_dn,
            f"close {close:.5g} vs {ll - buf:.5g} (low20 {ll:.5g} - {buf:.5g})"),
        chk("H1 trend EMA20 > EMA50", c_trend_h1_up,
            f"{float(e20h.iloc[-1]):.5g} vs {float(e50h.iloc[-1]):.5g}"),
        chk("H1 trend EMA20 < EMA50", c_trend_h1_dn,
            f"{float(e20h.iloc[-1]):.5g} vs {float(e50h.iloc[-1]):.5g}"),
        chk("H4 trend EMA20 > EMA50 > EMA200", c_trend_h4_up,
            f"{float(e20q.iloc[-1]):.5g} > {float(e50q.iloc[-1]):.5g} > {float(e200q.iloc[-1]):.5g}"),
        chk("H4 trend EMA20 < EMA50 < EMA200", c_trend_h4_dn,
            f"{float(e20q.iloc[-1]):.5g} < {float(e50q.iloc[-1]):.5g} < {float(e200q.iloc[-1]):.5g}"),
        chk("ADX14_H1 > 25", c_adx, f"ADX14 {adx:.2f}"),
    ]

    long_signal = c_break_up and c_trend_h1_up and c_trend_h4_up and c_adx
    short_signal = c_break_dn and c_trend_h1_dn and c_trend_h4_dn and c_adx
    if long_signal and short_signal:
        # mathematically impossible on one bar; guard anyway instead of guessing
        return result("NO_SIGNAL", STRATEGY_ID, checks,
                      "contradictory bar: both long and short conditions true - rejected")

    if not long_signal and not short_signal:
        failed = [c["label"] for c in checks if not c["ok"]]
        return result("NO_SIGNAL", STRATEGY_ID, checks,
                      "conditions not met: " + ("; ".join(failed[:3]) if failed else "n/a"),
                      direction=None)

    direction = "LONG" if long_signal else "SHORT"
    # entry = open of the FIRST completed M15 candle at/after the H1 close
    h1_close_time = h.index[-1]
    post = m[m.index >= h1_close_time]
    if post.empty:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, checks,
                      "the M15 frame does not yet contain the candle that opens "
                      f"at/after H1 confirmation {h1_close_time.isoformat()}")
    entry = float(post["open"].iloc[0])
    entry_time = post.index[0]

    sl = entry - atr * ATR_STOP_MULT if direction == "LONG" \
        else entry + atr * ATR_STOP_MULT
    risk = (entry - sl) if direction == "LONG" else (sl - entry)
    if risk <= 0:
        return result("REJECTED_RISK", STRATEGY_ID, checks,
                      f"invalid risk ({risk:.5g}) - rejected per spec",
                      direction=direction)
    tp = entry + TP_R * risk if direction == "LONG" else entry - TP_R * risk

    return result("SIGNAL", STRATEGY_ID, checks,
                  f"{direction}: H1 close {close:.5g} breaks the prior-20 "
                  f"{'high' if direction == 'LONG' else 'low'} with EMA-trend "
                  f"alignment and ADX14 {adx:.1f}; entry at the next M15 open "
                  f"{entry_time.strftime('%H:%M')} UTC",
                  direction=direction, entry=entry, sl=sl, tp=tp,
                  risk=risk, entry_time=entry_time.isoformat(),
                  signal_time=h1_close_time.isoformat(),
                  be_marker_r=BE_MARKER_R, atr14=atr, adx14=adx)
