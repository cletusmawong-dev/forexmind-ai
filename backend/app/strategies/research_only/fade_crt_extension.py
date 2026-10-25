"""PINSTRIPE V6 - Strategy 2: Fade_CRT_Extension (Primary Strategy).

Owner spec, locked rules - implemented EXACTLY as written; no extra
filters, no changed values. Sequence on the completed-bar series:

  A. 4H CRT candle: same definition as Strategy 1 (body >= 0.65 AND
     range > 0.85 x 20-avg range). The hybrid validity filter of
     Strategy 1 B is NOT part of Strategy 2.
  B. Extension: price moves beyond the CRT range by >= 15% of the CRT
     size (bullish CRT -> High > CRT High + 0.15 x range; bearish
     mirror). The FURTHEST extreme made during the extension is tracked.
  C. Reversal: after the extension, a 1H candle CLOSES back inside the
     original CRT range (CRT Low <= close <= CRT High).
  D. Confirmation & entry: a strong candle (body >= 0.55) OPPOSITE the
     original CRT direction, then entry on the 1H CLOSE beyond its
     extreme (bullish CRT -> SELL on close below the confirmation low;
     bearish CRT -> BUY on close above the confirmation high).
  E. SL beyond the extension extreme (the furthest high/low made during
     the extension; the spec names no buffer).
  F. TP1 = CRT midpoint, TP2 = opposite side of the CRT range.
  Common: 50% partial at TP1, SL to breakeven, remainder exits at TP2
  (deterministic trade-manager block). Entry ONLY on 1H closes; the
  live signal fires only when the trigger bar is the latest closed bar.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from . import pinstripe_common as pc

SPEC: Dict[str, Any] = {
    "name": "Fade_CRT_Extension",
    "role": "primary",
    "spec": "PINSTRIPE V6",
    "htf": "4H", "ltf": "1H", "entry_tf": "1H",
    "crt_body_min": 0.65, "crt_range_mult": 0.85, "crt_avg_len": 20,
    "conf_body_min": 0.55, "extension_frac": 0.15,
    "partial_fraction": 0.5, "breakeven_after_partial": True,
    # No instrument list in the spec: instrument-agnostic (markets=None,
    # the owner's Settings selection rules).
}

SIGNAL = pc.SIGNAL
NO_SIGNAL = pc.NO_SIGNAL
INSUFFICIENT = pc.INSUFFICIENT


def _sequence(h1: pd.DataFrame, crt: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic replay: extension -> reversal -> confirmation -> trigger.

    The furthest extension extreme is tracked CONTINUOUSLY (every bar whose
    extreme exceeds the extension threshold updates it - "the furthest
    high/low made during the extension"). A bar that extends intrabar and
    closes back inside completes both the extension and the reversal at its
    own close. The confirmation and trigger candles are searched from the
    following bar; a close that RESUMES the extension (beyond the range on
    the extension side) re-arms the reversal state (the same rules apply
    again, per spec)."""
    frac = SPEC["extension_frac"]
    rng = crt["range"]
    want = crt["direction"]                       # original CRT direction
    start = h1.index.searchsorted(crt["ts_close"])
    ext_thr = (crt["high"] + frac * rng) if want == 1 else (crt["low"] - frac * rng)
    extreme = None                                 # furthest extension price
    conf = trigger = None
    state = "SEEK_EXT"
    for j in range(start, len(h1)):
        o = float(h1["open"].iloc[j]); c = float(h1["close"].iloc[j])
        hi = float(h1["high"].iloc[j]); lo = float(h1["low"].iloc[j])
        extended = (hi > ext_thr) if want == 1 else (lo < ext_thr)
        if extended:
            if extreme is None:
                extreme = hi if want == 1 else lo
            else:
                extreme = max(extreme, hi) if want == 1 else min(extreme, lo)
        close_inside = crt["low"] <= c <= crt["high"]
        resumed = (c > crt["high"]) if want == 1 else (c < crt["low"])
        if state == "SEEK_EXT":
            if extended:
                state = "SEEK_REV"
                if close_inside:                   # extended + back inside in one bar
                    state = "SEEK_CONF"
            continue
        if state == "SEEK_REV":
            if close_inside:
                state = "SEEK_CONF"
            continue
        if state == "SEEK_CONF":
            if resumed:                            # extension leg resumed
                state = "SEEK_REV"
            else:
                body = pc.body_ratio(o, hi, lo, c)
                candle_dir = 1 if c > o else (-1 if c < o else 0)
                if body >= SPEC["conf_body_min"] and candle_dir == -want:
                    conf = {"bar": j, "high": hi, "low": lo}
                    state = "SEEK_TRIG"
            continue
        if state == "SEEK_TRIG":
            if resumed:                            # same rules apply again
                state = "SEEK_REV"
                conf = None
            elif (c < conf["low"]) if want == 1 else (c > conf["high"]):
                trigger = {"bar": j, "close": c}
                state = "DONE"
    return {"state": state, "extreme": extreme, "conf": conf,
            "trigger": trigger, "start": start, "last_bar": len(h1) - 1}


def evaluate(h1, h4, market: Optional[str] = None) -> Dict[str, Any]:
    try:
        df = pc._frame(h1, "H1")
        h4f = pc._frame(h4, "H4")
    except ValueError as exc:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"], "reason": str(exc)}
    if len(df) < 50 or len(h4f) < pc.COMMON["crt_avg_len"] + 6:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": (f"need >= 50 H1 / {pc.COMMON['crt_avg_len'] + 6} H4 bars, "
                           f"got {len(df)}/{len(h4f)}")}
    h4c = pc.h4_completed_asof(h4f, df.index[-1])
    crt = pc.find_latest_crt(h4c, require_validity=False)   # definition only
    checks: List[Dict[str, Any]] = [
        {"rule": "4H CRT (body>=0.65, range>0.85x20avg)", "ok": crt is not None,
         "detail": (f"CRT {crt['ts']} dir {crt['direction']} body "
                    f"{crt['body']:.2f}" if crt else "none in completed H4")}]
    if crt is None:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "timestamp": str(df.index[-1])}
    seq = _sequence(df, crt)
    fresh = seq["trigger"] is not None and seq["trigger"]["bar"] == seq["last_bar"]
    fade_dir = "SELL" if crt["direction"] == 1 else "BUY"    # OPPOSITE the CRT
    checks.append({"rule": ("extension >=15% -> close back inside -> "
                            "confirm (body>=0.55) -> close beyond conf"),
                   "ok": bool(fresh),
                   "detail": (f"state {seq['state']}, extreme {seq['extreme']}, "
                              f"conf {seq['conf']['bar'] if seq['conf'] else '-'}, "
                              f"trigger {seq['trigger']['bar'] if seq['trigger'] else '-'}")})
    if not fresh:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "crt": crt, "timestamp": str(df.index[-1])}

    want = crt["direction"]
    entry = float(seq["trigger"]["close"])          # entry ON the candle close
    if want == 1:                                   # bullish CRT -> SELL the fade
        sl = seq["extreme"]                         # furthest extension high
        tp1 = (crt["high"] + crt["low"]) / 2.0
        tp2 = crt["low"]
    else:                                           # bearish CRT -> BUY the fade
        sl = seq["extreme"]                         # furthest extension low
        tp1 = (crt["high"] + crt["low"]) / 2.0
        tp2 = crt["high"]
    risk = abs(entry - sl)
    valid = risk > 0 and ((entry > tp2) if want == 1 else (entry < tp2))
    rr2 = abs(tp2 - entry) / risk if risk > 0 else None
    return {"status": SIGNAL, "strategy": SPEC["name"], "direction": fade_dir,
            "market": market, "signal_bar": str(df.index[seq["trigger"]["bar"]]),
            "timestamp": str(df.index[-1]),
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "risk": risk, "rr_primary": (round(rr2, 4) if rr2 else None),
            "geometry_valid": bool(valid),
            "crt": {k2: crt[k2] for k2 in ("ts", "direction", "high", "low", "body")},
            "extreme": seq["extreme"], "conf": dict(seq["conf"]),
            "management": {"partial_fraction": SPEC["partial_fraction"],
                           "breakeven_after_partial": True},
            "checks": checks}


def backtest(h1, h4, market: str) -> Dict[str, Any]:
    """Exact chronological replay: every CRT's 1H sequence runs on the bars
    after its close (the window ends where the NEXT CRT closes - a new CRT
    is a context switch); entries on 1H closes; the shared common-settings
    management (stop-first per bar, gap fills at the open, 50% partial at
    TP1 + SL to exact breakeven, remainder exits at TP2). Setups whose
    geometry cannot produce a valid order (entry beyond TP2) are recorded
    as GEOMETRY_INVALID - live, the pre-trade validator rejects them and
    nothing is repaired."""
    try:
        df = pc._frame(h1, "H1")
        h4f = pc._frame(h4, "H4")
    except ValueError as exc:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"], "reason": str(exc)}
    h4c = pc.h4_completed_asof(h4f, df.index[-1])
    crts = pc.all_valid_crts(h4c, require_validity=False)
    trades: List[Dict[str, Any]] = []
    n = len(df)
    resume = 0
    for k, crt in enumerate(crts):
        start = df.index.searchsorted(crt["ts_close"])
        if start < resume:
            continue
        end = (df.index.searchsorted(crts[k + 1]["ts_close"])
               if k + 1 < len(crts) else n)
        sub = df.iloc[:max(end, start)]
        seq = _sequence(sub, crt)
        trig = seq["trigger"]
        if trig is None:
            continue
        t = trig["bar"]
        want = crt["direction"]
        entry = float(trig["close"])
        if want == 1:
            sl, tp1, tp2 = seq["extreme"], (crt["high"] + crt["low"]) / 2.0, crt["low"]
            direction = "SELL"
        else:
            sl, tp1, tp2 = seq["extreme"], (crt["high"] + crt["low"]) / 2.0, crt["high"]
            direction = "BUY"
        risk = abs(entry - sl)
        rec = {"market": market, "direction": direction,
               "signal_bar": str(df.index[t]), "entry": round(entry, 6),
               "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6),
               "spec": "PINSTRIPE V6 Fade_CRT_Extension"}
        if risk <= 0 or not ((entry > tp2) if want == 1 else (entry < tp2)):
            rec.update({"outcome": "GEOMETRY_INVALID", "r_multiple": None,
                        "exit_reason": "UNTRADEABLE_GEOMETRY"})
            trades.append(rec)
            resume = t + 1
            continue
        exit_reason, exit_px, j, r1, r2, partial_done = pc.manage_trade(
            df, t, entry, sl, tp1, tp2)
        # a gap straight through TP1 to TP2 closes the FULL position at TP2
        total = (0.5 * r1 + 0.5 * r2) if partial_done else r2
        rec.update({"exit_bar": str(df.index[j]),
                    "exit_reason": exit_reason, "exit": round(float(exit_px), 6),
                    "r_tp1": (round(r1, 4) if r1 is not None else None),
                    "r_remainder": round(r2, 4),
                    "r_multiple": round(total, 4),
                    "partial_50_at_tp1": True, "breakeven_after_partial": True})
        trades.append(rec)
        resume = j + 1
    rs = [x["r_multiple"] for x in trades if isinstance(x.get("r_multiple"), (int, float))]
    wins = [r for r in rs if r > 0]
    return {"status": "OK", "strategy": SPEC["name"], "trades": trades,
            "summary": {"n": len(trades), "wins": len(wins),
                        "total_r": round(sum(rs), 4) if rs else 0.0},
            "reference": "PINSTRIPE V6 locked rules; owner backtest numbers "
                         "remain owner claims to compare live results against"}
