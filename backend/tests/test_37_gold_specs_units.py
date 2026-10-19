"""FOREXMIND H1 Trend Breakout + Gold ORB - spec unit tests (rule 4).

Every entry condition, exit calculation, session boundary and breakeven
trigger from the two owner specs (2026-10-10) is tested individually.
The engines are the source of truth; these tests pin them to the spec.
"""
import pandas as pd
import pytest

from app.strategies.gold_specs import gold_orb, h1_trend_breakout
from app.strategies.gold_specs.indicators import prepare


# ------------------------------------------------------------- builders
def mk(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def h1_trend_frame(bars=80, flat=40, step=2.0, last=None, end="2026-10-09T20:00"):
    """`flat` flat bars then a steady uptrend (ADX14 lands well above 25)."""
    idx = pd.date_range(end=end, periods=bars, freq="1h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = 2400.0
    for i in range(bars):
        if i < flat:
            o.append(px); h.append(px + 0.5); l.append(px - 0.5); c.append(px)
        else:
            px += step
            o.append(px - step); h.append(px + 0.6); l.append(px - step - 0.4); c.append(px)
    if last is not None:
        o[-1], h[-1], l[-1], c[-1] = last
    return mk(ts, o, h, l, c)


def h4_trend_frame(bars=210, up_last=40, step=3.0):
    idx = pd.date_range(end="2026-10-09T16:00", periods=bars, freq="4h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = 2350.0
    for i in range(bars):
        if i >= bars - up_last:
            px += step
        o.append(px - 1.0); h.append(px + 1.2); l.append(px - 2.2); c.append(px)
    return mk(ts, o, h, l, c)


def reflect(df, level=4800.0):
    """Vertical mirror: an uptrend frame becomes a downtrend frame."""
    return mk([str(t).replace(" ", "T") for t in df.index],
              [level - v for v in df["open"]], [level - v for v in df["low"]],
              [level - v for v in df["high"]], [level - v for v in df["close"]])


def m15_after(hour):
    idx = pd.date_range(start=f"2026-10-09T{hour:02d}:00", periods=3,
                        freq="15min", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    n = len(ts)
    return mk(ts, [2440.0] * n, [2441.0] * n, [2439.0] * n, [2440.5] * n)


def orb_day(trigger_at=None, bullish=True, orb_high=2401.0):
    """M5 bars of one UTC day 13:30..trigger time. OR bars 13:30-13:55 sit at
    2399.5-2401.0; the trigger candle (if any) closes orb_high+1.5."""
    ts, o, h, l, c = [], [], [], [], []
    slots = [(13, m) for m in (30, 35, 40, 45, 50, 55)]
    slots += [(hh, m) for hh in (14, 15, 16) for m in (0, 5, 10, 15, 20, 25, 30,
                                                       35, 40, 45, 50, 55)]
    slots += [(17, 0)]
    end = tuple(int(x) for x in trigger_at.split(":")) if trigger_at else (13, 55)
    for (hh, mm) in slots:
        t = f"2026-10-08T{hh:02d}:{mm:02d}"
        if (13, 30) <= (hh, mm) <= (13, 55):
            ts.append(t); o.append(2400.0); h.append(orb_high)
            l.append(2399.5); c.append(2400.2)
        elif (hh, mm) == end and trigger_at:
            ts.append(t); o.append(orb_high - 0.2); h.append(orb_high + 2.0)
            l.append(orb_high - 0.4)
            c.append(orb_high + 1.5 if bullish else orb_high - 0.1)
        else:
            ts.append(t); o.append(2400.3); h.append(2400.8)
            l.append(2399.9); c.append(2400.5)
        if (hh, mm) == end:
            break
    return mk(ts, o, h, l, c)


# ============================== H1 TREND BREAKOUT =========================
def test_long_signal_full_geometry():
    h1 = h1_trend_frame()
    m15 = m15_after(21)                     # H1 bar 20:00 closes at 21:00 UTC
    res = h1_trend_breakout.evaluate(h1, m15, h4_trend_frame())
    assert res["status"] == "SIGNAL" and res["direction"] == "LONG"
    entry = float(m15["open"].iloc[0])                 # next M15 open rule
    atr = res["atr14"]
    assert res["entry"] == entry
    assert res["sl"] == pytest.approx(entry - 1.2 * atr)      # spec stop
    assert res["tp"] == pytest.approx(entry + 1.25 * (entry - res["sl"]))
    assert res["be_marker_r"] == 0.5                          # breakeven trigger
    assert pd.Timestamp(res["signal_time"]) == h1.index[-1]
    assert res["entry_time"] >= res["signal_time"]


def test_short_signal_mirror():
    h1 = reflect(h1_trend_frame())
    res = h1_trend_breakout.evaluate(h1, m15_after(21), reflect(h4_trend_frame()))
    assert res["status"] == "SIGNAL" and res["direction"] == "SHORT"
    entry, sl, tp = res["entry"], res["sl"], res["tp"]
    assert sl == pytest.approx(entry + 1.2 * res["atr14"])
    assert tp == pytest.approx(entry - 1.25 * (sl - entry))
    assert res["be_marker_r"] == 0.5


def test_no_breakout_no_signal():
    res = h1_trend_breakout.evaluate(
        h1_trend_frame(last=(2400.0, 2400.5, 2399.5, 2400.0)),
        m15_after(21), h4_trend_frame())
    assert res["status"] == "NO_SIGNAL"
    assert any(not c["ok"] for c in res["checks"])


def test_breakout_without_trend_alignment_rejected():
    res = h1_trend_breakout.evaluate(h1_trend_frame(), m15_after(21),
                                     reflect(h4_trend_frame()))
    assert res["status"] == "NO_SIGNAL"


def test_adx_threshold_is_strictly_greater_than_25(monkeypatch):
    h1, m15, h4 = h1_trend_frame(), m15_after(21), h4_trend_frame()
    monkeypatch.setattr(h1_trend_breakout, "wilder_adx", lambda df, p=14: 25.0)
    assert h1_trend_breakout.evaluate(h1, m15, h4)["status"] == "NO_SIGNAL"
    monkeypatch.setattr(h1_trend_breakout, "wilder_adx", lambda df, p=14: 25.0001)
    assert h1_trend_breakout.evaluate(h1, m15, h4)["status"] == "SIGNAL"


def test_insufficient_h4_for_ema200():
    res = h1_trend_breakout.evaluate(h1_trend_frame(), m15_after(21),
                                     h4_trend_frame(bars=150))
    assert res["status"] == "INSUFFICIENT_DATA"
    assert "H4" in res["summary"]


def test_missing_m15_entry_reference():
    # M15 frame ends before the H1 bar closes -> honest refusal, no invented entry
    res = h1_trend_breakout.evaluate(h1_trend_frame(), m15_after(7), h4_trend_frame())
    assert res["status"] == "INSUFFICIENT_DATA"
    assert "M15" in res["summary"]


def test_checks_log_every_condition():
    res = h1_trend_breakout.evaluate(h1_trend_frame(), m15_after(21), h4_trend_frame())
    labels = " | ".join(c["label"] for c in res["checks"])
    assert "breakout above prior-20 high" in labels
    assert "EMA20 > EMA50" in labels
    assert "H4" in labels
    assert "ADX" in labels


# ================================= GOLD ORB ===============================
def test_orb_full_signal_geometry():
    res = gold_orb.evaluate(orb_day(trigger_at="14:10"))
    assert res["status"] == "SIGNAL" and res["direction"] == "LONG"
    entry, orb_high, orb_low = res["entry"], res["orb_high"], res["orb_low"]
    assert entry == pytest.approx(orb_high + 1.5)       # entry = trigger close
    assert res["sl"] == pytest.approx(orb_low)          # stop = OR low
    assert res["tp"] == pytest.approx(entry + 1.5 * (entry - res["sl"]))
    assert res["be_marker_r"] == 1.0                    # breakeven at +1R


def test_orb_range_is_exactly_six_bars_1330_to_1355():
    m5 = prepare(orb_day(trigger_at="14:10"))
    hhmm = m5.index.strftime("%H:%M")
    or_rows = m5[(hhmm >= "13:30") & (hhmm <= "13:55")]
    assert len(or_rows) == 6
    assert or_rows.index[0].strftime("%H:%M") == "13:30"
    assert or_rows.index[-1].strftime("%H:%M") == "13:55"
    res = gold_orb.evaluate(orb_day(trigger_at="14:10"))
    assert res["orb_high"] == pytest.approx(float(or_rows["high"].max()))
    assert res["orb_low"] == pytest.approx(float(or_rows["low"].min()))


def test_orb_1325_bar_is_not_part_of_range():
    m5 = prepare(orb_day(trigger_at="14:10"))
    # a 13:25 bar (closes 13:30) must not join the six-bar range
    extra = mk(["2026-10-08T13:25"], [2399.0], [2500.0], [2398.0], [2399.2])
    res = gold_orb.evaluate(pd.concat([m5, extra]))
    assert res["orb_high"] == pytest.approx(2401.0)     # unchanged by the 13:25 bar


def test_orb_incomplete_range_is_no_signal():
    m5 = prepare(orb_day(trigger_at="14:10"))
    m5 = m5.drop(m5.index[m5.index.strftime("%H:%M") == "13:45"])
    res = gold_orb.evaluate(m5)
    assert res["status"] == "NO_SIGNAL"
    assert any("opening range complete" in c["label"] and not c["ok"]
               for c in res["checks"])


def test_orb_window_start_boundary():
    assert gold_orb.evaluate(orb_day(trigger_at="14:00"))["status"] == "SIGNAL"


def test_orb_window_end_boundary():
    # 16:55 = last eligible M5 open inside "14:00-16:59"
    assert gold_orb.evaluate(orb_day(trigger_at="16:55"))["status"] == "SIGNAL"
    # a 17:00-open candle (completes 17:05) is OUTSIDE the window
    m5 = orb_day(trigger_at=None)                        # ends 13:55, no trigger
    extra = mk(["2026-10-08T17:00"], [2400.3], [2404.0], [2399.9], [2403.5])
    res = gold_orb.evaluate(pd.concat([m5, extra]))
    assert res["status"] == "NO_SIGNAL"


def test_orb_bearish_trigger_candle_ignored():
    res = gold_orb.evaluate(orb_day(trigger_at="14:10", bullish=False))
    assert res["status"] == "NO_SIGNAL"                 # close > open required


def test_orb_no_duplicate_per_range():
    res1 = gold_orb.evaluate(orb_day(trigger_at="14:10"))
    assert res1["status"] == "SIGNAL"
    m5 = orb_day(trigger_at="14:10")
    extra = mk(["2026-10-08T14:15", "2026-10-08T14:20"],
               [2403.0, 2403.2], [2404.0, 2404.2], [2402.6, 2402.8], [2403.6, 2403.9])
    res2 = gold_orb.evaluate(pd.concat([m5, extra]))
    assert res2["status"] == "NO_SIGNAL"
    assert any("consumed" in c["detail"] for c in res2["checks"])


def test_orb_uses_only_current_utc_day():
    day1 = orb_day(trigger_at="14:10")
    day2 = mk(["2026-10-09T12:00", "2026-10-09T12:05"],
              [2400.0, 2400.0], [2400.6, 2400.6], [2399.8, 2399.8],
              [2400.2, 2400.2])
    res = gold_orb.evaluate(pd.concat([day1, day2]))
    assert res["status"] == "NO_SIGNAL"                 # day 2 has no OR yet


def test_orb_condition_logging():
    res = gold_orb.evaluate(orb_day(trigger_at="14:10"))
    labels = " | ".join(c["label"] for c in res["checks"])
    assert "opening range complete" in labels
    assert "closes above OR high" in labels
    assert "bullish" in labels
    assert "no duplicates" in labels
    assert "risk valid" in labels
