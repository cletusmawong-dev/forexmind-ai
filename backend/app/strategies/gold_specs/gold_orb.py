"""FOREXMIND - Gold ORB (owner spec 2026-10-10, SOURCE OF TRUTH).

XAUUSD, M5, UTC (rule 3: session times are UTC, never broker time).
Opening range: the SIX completed M5 candles 13:30-13:55.
Entry window: candle OPEN times 14:00-16:59 (M5 grid: 14:00..16:55) - the
Pine ta session convention is bar-open-in-window; documented here because
the spec text does not spell out open-vs-close semantics (rule 8 note).
LONG ONLY: first completed M5 candle in the window that CLOSES above the OR
high with close > open. Entry = that candle's close. Stop = OR low.
Target = 1.5 x risk. BE marker at +1R. One trade per opening range
(noDuplicateTradePerOpeningRange): only the FIRST qualifying candle signals.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .indicators import prepare, result

STRATEGY_ID = "strategy_gold_orb"
OR_START = (13, 30)
OR_END = (13, 55)          # last M5 bar of the range (completes at 14:00)
OR_BARS = 6
WIN_START = (14, 0)
WIN_END = (16, 55)         # last M5 open inside "14:00-16:59"
TP_R = 1.5
BE_MARKER_R = 1.0


def evaluate(m5: Optional[pd.DataFrame]) -> Dict[str, Any]:
    d = prepare(m5)
    if d is None or d.empty:
        return result("INSUFFICIENT_DATA", STRATEGY_ID, [],
                      "no completed M5 candles")

    last = d.index[-1]
    day = last.date()

    def on_grid(ts, hm) -> bool:
        return (ts.hour, ts.minute) == hm

    or_mask = [(ts.date() == day and on_grid(ts, OR_START))
               or (ts.date() == day and OR_START <= (ts.hour, ts.minute) <= OR_END)
               for ts in d.index]
    or_bars = d[or_mask]
    if len(or_bars) != OR_BARS:
        return result("NO_SIGNAL", STRATEGY_ID,
                      [{"label": "opening range complete (6 bars)", "ok": False,
                        "detail": f"found {len(or_bars)} of {OR_BARS} M5 bars "
                                  f"13:30-13:55 UTC for {day.isoformat()}"}],
                      "no completed opening range for the current UTC day "
                      "(outside 13:30-14:00, market closed, or data missing)")

    orb_high = float(or_bars["high"].max())
    orb_low = float(or_bars["low"].min())

    def in_window(ts) -> bool:
        t = (ts.hour, ts.minute)
        return ts.date() == day and WIN_START <= t <= WIN_END

    eligible = d[[in_window(ts) for ts in d.index]]
    trigger = None
    for ts, row in eligible.iterrows():
        if float(row["close"]) > orb_high and float(row["close"]) > float(row["open"]):
            trigger = (ts, row)
            break

    def chk(label, ok, detail):
        return {"label": label, "ok": bool(ok), "detail": detail}

    latest = d.index[-1]
    checks = [
        chk("opening range complete (6 bars)", True,
            f"high {orb_high:.5g} / low {orb_low:.5g} ({day.isoformat()} 13:30-13:55 UTC)"),
        chk("M5 candle closes above OR high (in 14:00-16:59 UTC)", trigger is not None,
            f"OR high {orb_high:.5g}; first trigger "
            f"{trigger[0].strftime('%H:%M') if trigger else 'none yet in window'}"),
        chk("trigger candle is bullish (close > open)",
            trigger is not None and float(trigger[1]["close"]) > float(trigger[1]["open"]),
            "close above open" if trigger else "no trigger candle"),
        chk("first trigger of this opening range (no duplicates)",
            trigger is not None and trigger[0] == latest,
            "latest completed candle IS the first trigger" if trigger and trigger[0] == latest
            else (f"trigger happened at {trigger[0].strftime('%H:%M')} - this opening "
                  "range is consumed" if trigger else "no trigger yet"),
        ),
    ]

    if trigger is None:
        return result("NO_SIGNAL", STRATEGY_ID, checks,
                      f"no M5 close above {orb_high:.5g} in the 14:00-16:59 UTC "
                      "window so far")
    if trigger[0] != latest:
        return result("NO_SIGNAL", STRATEGY_ID, checks,
                      "this opening range already produced its single trigger "
                      f"at {trigger[0].strftime('%H:%M')} UTC - no duplicates "
                      "per range")

    entry = float(trigger[1]["close"])
    sl = orb_low
    risk = entry - sl
    if risk <= 0:
        return result("REJECTED_RISK", STRATEGY_ID, checks,
                      f"invalid risk (entry {entry:.5g} <= OR low {sl:.5g}) "
                      "- rejected per spec", direction="LONG")
    tp = entry + TP_R * risk
    checks.append(chk("risk valid (entry > OR low)", True,
                      f"risk {risk:.5g} = entry - OR low"))
    return result("SIGNAL", STRATEGY_ID, checks,
                  f"LONG: M5 {latest.strftime('%H:%M')} UTC closed {entry:.5g} "
                  f"above OR high {orb_high:.5g}; stop at OR low {sl:.5g}, "
                  f"target 1.5R",
                  direction="LONG", entry=entry, sl=sl, tp=tp, risk=risk,
                  signal_time=latest.isoformat(), entry_time=latest.isoformat(),
                  be_marker_r=BE_MARKER_R, orb_high=orb_high, orb_low=orb_low)
