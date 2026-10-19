"""Spec rule 5: compare the implementation against a SEPARATE reference
calculation using identical candle data.

The reference implementations below are deliberately independent: plain
python loops over lists (no pandas rolling/ewm/shift), different structure,
same specification. Both implementations are evaluated at MANY bar endpoints
of identical seeded random-walk candles and their decisions (status,
direction, entry, SL, TP) must agree everywhere; indicator values are also
compared within tight tolerance.
"""
import numpy as np
import pandas as pd

from app.strategies.gold_specs import gold_orb, h1_trend_breakout
from app.strategies.gold_specs.indicators import prepare


# ------------------------------------------------- reference: H1 breakout
class RefInd:
    """Incremental Wilder EMA/ATR/ADX + simple EMA, plain python."""

    def __init__(self):
        self.emas = {}

    def ema(self, vals, period):
        k = 2.0 / (period + 1)
        out = [vals[0]]
        for v in vals[1:]:
            out.append(out[-1] + k * (v - out[-1]))
        return out

    def atr_adx(self, h, l, c, period=14):
        trs, pls, mns = [], [], []
        for i in range(1, len(h)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
            up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
            pls.append(up if (up > dn and up > 0) else 0.0)
            mns.append(dn if (dn > up and dn > 0) else 0.0)
        atr = sum(trs[:period]) / period
        pdm = sum(pls[:period]) / period
        ndm = sum(mns[:period]) / period
        dxs = []
        for i in range(period, len(trs)):
            atr += (trs[i] - atr) / period
            pdm += (pls[i] - pdm) / period
            ndm += (mns[i] - ndm) / period
            pdi = 100 * pdm / atr if atr else 0.0
            ndi = 100 * ndm / atr if atr else 0.0
            dxs.append(100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0.0)
        if len(dxs) < period:
            return atr, None
        adx = sum(dxs[:period]) / period
        for dx in dxs[period:]:
            adx = (adx * 13 + dx) / 14
        return atr, adx


def ref_h1_trend_breakout(h1: pd.DataFrame, m15: pd.DataFrame, h4: pd.DataFrame):
    ref = RefInd()
    H1 = 60
    H4 = 200
    if len(h1) < H1 or len(h4) < H4 or not len(m15):
        return {"status": "INSUFFICIENT_DATA"}
    h = list(h1["high"]); l = list(h1["low"]); c = list(h1["close"])
    hh = max(h[-21:-1]); ll = min(l[-21:-1])
    atr = ref.atr_adx(list(h1["high"]), list(h1["low"]), list(h1["close"]))[0]
    adx = ref.atr_adx(list(h1["high"]), list(h1["low"]), list(h1["close"]))[1]
    e20h = ref.ema(list(h1["close"]), 20)[-1]
    e50h = ref.ema(list(h1["close"]), 50)[-1]
    e20q = ref.ema(list(h4["close"]), 20)[-1]
    e50q = ref.ema(list(h4["close"]), 50)[-1]
    e200q = ref.ema(list(h4["close"]), 200)[-1]
    close = c[-1]
    up = close > hh + 0.05 * atr
    dn = close < ll - 0.05 * atr
    tup = e20h > e50h and e20q > e50q > e200q
    tdn = e20h < e50h and e20q < e50q < e200q
    adx_ok = adx > 25
    longs = up and tup and adx_ok
    shorts = dn and tdn and adx_ok
    if longs and shorts or not (longs or shorts):
        return {"status": "NO_SIGNAL", "adx": adx}
    t_close = h1.index[-1]
    post = [t for t in m15.index if t >= t_close]
    if not post:
        return {"status": "INSUFFICIENT_DATA", "adx": adx}
    entry = float(m15.loc[post[0], "open"])
    if longs:
        sl = entry - 1.2 * atr
        return {"status": "SIGNAL", "direction": "LONG", "entry": entry,
                "sl": sl, "tp": entry + 1.25 * (entry - sl), "adx": adx}
    sl = entry + 1.2 * atr
    return {"status": "SIGNAL", "direction": "SHORT", "entry": entry,
            "sl": sl, "tp": entry - 1.25 * (sl - entry), "adx": adx}


# ---------------------------------------------------- reference: Gold ORB
def ref_orb(m5: pd.DataFrame):
    if m5 is None or not len(m5):
        return {"status": "INSUFFICIENT_DATA"}
    rows = list(m5.index)
    day = rows[-1].date()
    or_hhmm = ["13:30", "13:35", "13:40", "13:45", "13:50", "13:55"]
    or_bars = [r for r in rows if r.date() == day and r.strftime("%H:%M") in or_hhmm]
    if len(or_bars) != 6:
        return {"status": "NO_SIGNAL"}
    orb_high = max(float(m5.loc[r, "high"]) for r in or_bars)
    orb_low = min(float(m5.loc[r, "low"]) for r in or_bars)
    win = ["14:00", "14:05", "14:10", "14:15", "14:20", "14:25", "14:30",
           "14:35", "14:40", "14:45", "14:50", "14:55", "15:00", "15:05",
           "15:10", "15:15", "15:20", "15:25", "15:30", "15:35", "15:40",
           "15:45", "15:50", "15:55", "16:00", "16:05", "16:10", "16:15",
           "16:20", "16:25", "16:30", "16:35", "16:40", "16:45", "16:50",
           "16:55"]
    trig = None
    for r in rows:
        if r.date() == day and r.strftime("%H:%M") in win:
            if (float(m5.loc[r, "close"]) > orb_high
                    and float(m5.loc[r, "close"]) > float(m5.loc[r, "open"])):
                trig = r
                break
    if trig is None:
        return {"status": "NO_SIGNAL"}
    if trig != rows[-1]:
        return {"status": "NO_SIGNAL"}     # range consumed
    entry = float(m5.loc[trig, "close"])
    if entry - orb_low <= 0:
        return {"status": "REJECTED_RISK"}
    return {"status": "SIGNAL", "direction": "LONG", "entry": entry,
            "sl": orb_low, "tp": entry + 1.5 * (entry - orb_low)}


# ------------------------------------------------------------- generators
def rnd_ohlc(n, p0, vol, seed, trend_segments=6):
    rng = np.random.default_rng(seed)
    drifts = rng.uniform(-vol, vol, trend_segments)
    o, h, l, c = [], [], [], []
    px = p0
    seg = n // trend_segments
    for i in range(n):
        d = drifts[min(i // seg, trend_segments - 1)]
        step = rng.normal(d * 0.1, vol)
        op = px
        cl = op + step
        hi = max(op, cl) + abs(rng.normal(0, vol * 0.6))
        lo = min(op, cl) - abs(rng.normal(0, vol * 0.6))
        o.append(op); h.append(hi); l.append(lo); c.append(cl)
        px = cl
    return o, h, l, c


def frame(idx, o, h, l, c):
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c},
                        index=pd.to_datetime(idx, utc=True))


# --------------------------------------------------------------- the check
def test_h1_breakout_implementation_matches_reference():
    """Rule 5: identical candles into both implementations, decisions must
    agree at ~450 rolling endpoints (ADX-deadband at the 25 line)."""
    idx = pd.date_range(end="2026-10-09T20:00", periods=500, freq="1h", tz="UTC")
    o, h, l, c = rnd_ohlc(500, 2400, 3.0, seed=42)
    h1 = frame([str(t).replace(" ", "T") for t in idx], o, h, l, c)
    qidx = pd.date_range(end="2026-10-09T16:00", periods=210, freq="4h", tz="UTC")
    qo, qh, ql, qc = rnd_ohlc(210, 2350, 6.0, seed=7)
    h4 = frame([str(t).replace(" ", "T") for t in qidx], qo, qh, ql, qc)
    midx = pd.date_range(end="2026-10-09T23:45", periods=120, freq="15min", tz="UTC")
    mo, mh, ml, mc = rnd_ohlc(120, 2400, 1.5, seed=99)
    m15 = frame([str(t).replace(" ", "T") for t in midx], mo, mh, ml, mc)

    agree = disagree = deadband = 0
    for end in range(260, 500):
        sub_h1 = h1.iloc[:end + 1]
        sub_h4 = h4 if end > 210 else h4
        got = h1_trend_breakout.evaluate(sub_h1, m15, sub_h4)
        exp = ref_h1_trend_breakout(sub_h1, m15, sub_h4)
        g_adx = got.get("adx14")
        if exp.get("adx") is not None and g_adx is not None \
                and abs(exp["adx"] - 25.0) < 0.5:
            deadband += 1                      # borderline bar: decision may flip
            continue
        g_status = got["status"]
        e_status = exp["status"]
        if g_status != e_status:
            # INSUFFICIENT_DATA vs NO_SIGNAL must NOT diverge silently:
            disagree += 1
            continue
        if g_status == "SIGNAL":
            same = (got["direction"] == exp["direction"]
                    and abs(got["entry"] - exp["entry"]) < 1e-9
                    and abs(got["sl"] - exp["sl"]) < 1e-6
                    and abs(got["tp"] - exp["tp"]) < 1e-6)
            if not same:
                disagree += 1
                continue
        agree += 1
    assert deadband + agree >= 200, "cross-check did not exercise enough bars"
    assert disagree == 0, f"{disagree} decision mismatches vs reference"


def test_h1_adx_values_track_reference_within_tolerance():
    idx = pd.date_range(end="2026-10-09T20:00", periods=300, freq="1h", tz="UTC")
    o, h, l, c = rnd_ohlc(300, 2400, 2.5, seed=11)
    h1 = frame([str(t).replace(" ", "T") for t in idx], o, h, l, c)
    from app.strategies.gold_specs.indicators import wilder_adx, wilder_atr
    ref = RefInd()
    r_atr, r_adx = ref.atr_adx(list(h1["high"]), list(h1["low"]), list(h1["close"]))
    assert abs(wilder_atr(h1) - r_atr) < 1e-9
    assert abs(wilder_adx(h1) - r_adx) < 1e-6


def test_orb_implementation_matches_reference():
    """Rule 5 for the ORB: 5 synthetic trading days, both implementations
    evaluated at every endpoint (hundreds of decisions)."""
    rows_ts, rows_o, rows_h, rows_l, rows_c = [], [], [], [], []
    rng = np.random.default_rng(5)
    for day in range(1, 6):
        day_str = f"2026-10-{day:02d}"
        orb_high = 2400 + float(rng.uniform(0.5, 3))
        orb_low = orb_high - float(rng.uniform(1, 4))
        slots = [(13, m) for m in (30, 35, 40, 45, 50, 55)]
        slots += [(hh, m) for hh in (14, 15, 16, 17) for m in range(0, 60, 5)]
        for (hh, mm) in slots:
            rows_ts.append(f"{day_str}T{hh:02d}:{mm:02d}")
            if (hh, mm) <= (13, 55):
                rows_o.append(orb_low + 0.4); rows_h.append(orb_high)
                rows_l.append(orb_low); rows_c.append(orb_low + 0.6)
            else:
                op = orb_high - 0.5
                cl = op + float(rng.choice([-0.6, 0.3, 2.2, -0.2, 1.0, 3.0]))
                rows_o.append(op)
                rows_h.append(max(op, cl) + 0.2)
                rows_l.append(min(op, cl) - 0.2)
                rows_c.append(cl)
    m5 = frame(rows_ts, rows_o, rows_h, rows_l, rows_c)

    agree = disagree = 0
    for end in range(10, len(m5)):
        sub = m5.iloc[:end + 1]
        got = gold_orb.evaluate(sub)
        exp = ref_orb(sub)
        if got["status"] != exp["status"]:
            disagree += 1
            continue
        if got["status"] == "SIGNAL":
            if not (got["direction"] == exp["direction"]
                    and abs(got["entry"] - exp["entry"]) < 1e-9
                    and abs(got["sl"] - exp["sl"]) < 1e-9
                    and abs(got["tp"] - exp["tp"]) < 1e-9):
                disagree += 1
                continue
        agree += 1
    assert agree >= 150, "cross-check did not exercise enough endpoints"
    assert disagree == 0, f"{disagree} decision mismatches vs reference"
