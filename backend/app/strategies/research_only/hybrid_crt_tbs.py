"""PINSTRIPE V6 - Strategy 1: Hybrid_CRT_TBS (Secondary Strategy).

Owner spec, locked rules - implemented EXACTLY as written; no extra
filters, no changed values. Sequence on the completed-bar series:

  A. 4H CRT candle: body ratio >= 0.65 AND range > 0.85 x 20-avg range
     (definition shared with Strategy 2 - pinstripe_common).
  B. Validity (hybrid rule): bearish CRT with High > highest High of the
     previous 5 H4 candles, OR bullish CRT with Low < lowest Low of the
     previous 5 H4 candles, OR body ratio >= 0.75.
  C. 1H (after the valid CRT closes): liquidity sweep (TBS) of a recent
     1H swing, then a Model #1 candle (body >= 0.58, direction = CRT
     direction), then entry on the 1H CLOSE beyond the Model #1 extreme
     (sell: close < M1 low / buy: close > M1 high).
  D. SL beyond BOTH structure extremes: the Model #1 extreme and the
     sweep extreme (the further one; the spec names no buffer).
  E. TP1 = 50% level of the 4H CRT range (CRT midpoint); TP2 = opposite
     side of the CRT range (sell -> CRT Low, buy -> CRT High).
  Common: 50% partial at TP1, SL to breakeven after the partial, the
  remaining 50% targets TP2 (deterministic trade-manager block).

Entry happens ONLY on 1H candle close (spec rule 4) and the live signal
fires only when the trigger bar is the LATEST closed 1H bar (restart-safe,
no stale entries).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from . import pinstripe_common as pc

SPEC: Dict[str, Any] = {
    "name": "Hybrid_CRT_TBS",
    "role": "secondary",
    "spec": "PINSTRIPE V6",
    "htf": "4H", "ltf": "1H", "entry_tf": "1H",
    "crt_body_min": 0.65, "crt_range_mult": 0.85, "crt_avg_len": 20,
    "strong_body": 0.75, "validity_lookback": 5,
    "m1_body_min": 0.58,
    "partial_fraction": 0.5, "breakeven_after_partial": True,
    # No instrument list in the spec: instrument-agnostic - the strategy
    # evaluates every market the owner selected in Settings (markets=None,
    # same rule as TPC-15).
}

SIGNAL = pc.SIGNAL
NO_SIGNAL = pc.NO_SIGNAL
INSUFFICIENT = pc.INSUFFICIENT


def _sweep_at(df: pd.DataFrame, j: int, want_dir: int,
              phi: Dict[int, float], plo: Dict[int, float],
              k: int) -> Optional[Dict[str, Any]]:
    """TBS liquidity sweep of a recent confirmed 1H swing at bar j.
    Sell context (want_dir=-1): a confirmed swing HIGH is taken
    (high[j] > swing) and the bar closes back below it. Buy mirrors."""
    if want_dir == -1:
        for p, level in phi.items():
            if p + k >= j:            # pivot must be confirmed before bar j
                continue
            if float(df["high"].iloc[j]) > level > float(df["close"].iloc[j]):
                return {"bar": j, "pivot_bar": p, "level": level,
                        "extreme": float(df["high"].iloc[j])}
    else:
        for p, level in plo.items():
            if p + k >= j:
                continue
            if float(df["low"].iloc[j]) < level < float(df["close"].iloc[j]):
                return {"bar": j, "pivot_bar": p, "level": level,
                        "extreme": float(df["low"].iloc[j])}
    return None


def _sequence(h1: pd.DataFrame, crt: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic replay of the 1H sweep -> M1 -> trigger sequence after
    the CRT close. Returns the latest state + any trigger on the final bar."""
    k = pc.COMMON["swing_k"]
    want = crt["direction"]                     # trade rides the CRT direction
    phi = pc.pivot_highs(h1["high"], k)
    plo = pc.pivot_lows(h1["low"], k)
    start = h1.index.searchsorted(crt["ts_close"])
    sweep = m1 = trigger = None
    state = "SEEK_SWEEP"
    for j in range(start, len(h1)):
        o = float(h1["open"].iloc[j]); c = float(h1["close"].iloc[j])
        hi = float(h1["high"].iloc[j]); lo = float(h1["low"].iloc[j])
        if state == "SEEK_SWEEP":
            s = _sweep_at(h1, j, want, phi, plo, k)
            if s:
                sweep = s
                state = "SEEK_M1"
        elif state == "SEEK_M1":
            s = _sweep_at(h1, j, want, phi, plo, k)
            if s:                               # the newest sweep governs
                sweep = s
                continue
            br = pc.body_ratio(o, hi, lo, c)
            candle_dir = 1 if c > o else (-1 if c < o else 0)
            if br >= SPEC["m1_body_min"] and candle_dir == want:
                m1 = {"bar": j, "high": hi, "low": lo}
                state = "SEEK_TRIGGER"
        elif state == "SEEK_TRIGGER":
            s = _sweep_at(h1, j, want, phi, plo, k)
            if s:                               # a fresh sweep restarts the M1 search
                sweep = s
                m1 = None
                state = "SEEK_M1"
            elif (c < m1["low"]) if want == -1 else (c > m1["high"]):
                trigger = {"bar": j, "close": c}
                state = "DONE"
    return {"state": state, "sweep": sweep, "m1": m1, "trigger": trigger,
            "start": start, "last_bar": len(h1) - 1}


def evaluate(h1, h4, market: Optional[str] = None) -> Dict[str, Any]:
    """Signal evaluation on the latest CLOSED 1H bar (entry on candle close)."""
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
    crt = pc.find_latest_crt(h4c, require_validity=True)
    checks: List[Dict[str, Any]] = [
        {"rule": "valid 4H CRT (body>=0.65, range>0.85x20avg, hybrid rule)",
         "ok": crt is not None,
         "detail": (f"CRT {crt['ts']} dir {crt['direction']} body "
                    f"{crt['body']:.2f}" if crt else "none in completed H4")}]
    if crt is None:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "timestamp": str(df.index[-1])}
    seq = _sequence(df, crt)
    fresh = seq["trigger"] is not None and seq["trigger"]["bar"] == seq["last_bar"]
    direction = "SELL" if crt["direction"] == -1 else "BUY"
    checks.append({"rule": "1H sweep -> Model#1 (body>=0.58) -> close beyond M1",
                   "ok": bool(fresh),
                   "detail": (f"state {seq['state']}, sweep "
                              f"{seq['sweep']['bar'] if seq['sweep'] else '-'}, "
                              f"M1 {seq['m1']['bar'] if seq['m1'] else '-'}, "
                              f"trigger {seq['trigger']['bar'] if seq['trigger'] else '-'}")})
    if not fresh:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "crt": crt, "timestamp": str(df.index[-1])}

    want = crt["direction"]
    entry = float(seq["trigger"]["close"])          # entry ON the candle close
    if want == -1:                                  # SELL
        sl = max(seq["m1"]["high"], seq["sweep"]["extreme"])
        tp1 = (crt["high"] + crt["low"]) / 2.0      # CRT midpoint
        tp2 = crt["low"]                            # opposite side
    else:                                           # BUY
        sl = min(seq["m1"]["low"], seq["sweep"]["extreme"])
        tp1 = (crt["high"] + crt["low"]) / 2.0
        tp2 = crt["high"]
    risk = abs(entry - sl)
    valid = risk > 0 and ((entry > tp2) if want == -1 else (entry < tp2))
    rr2 = abs(tp2 - entry) / risk if risk > 0 else None
    return {"status": SIGNAL, "strategy": SPEC["name"], "direction": direction,
            "market": market, "signal_bar": str(df.index[seq["trigger"]["bar"]]),
            "timestamp": str(df.index[-1]),
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "risk": risk, "rr_primary": (round(rr2, 4) if rr2 else None),
            "geometry_valid": bool(valid),
            "crt": {k2: crt[k2] for k2 in ("ts", "direction", "high", "low", "body")},
            "sweep": {k2: seq["sweep"][k2] for k2 in ("bar", "level", "extreme")},
            "m1": dict(seq["m1"]),
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
    crts = pc.all_valid_crts(h4c, require_validity=True)
    trades: List[Dict[str, Any]] = []
    n = len(df)
    resume = 0                                   # first 1H bar we may use
    for k, crt in enumerate(crts):
        start = df.index.searchsorted(crt["ts_close"])
        if start < resume:
            continue                             # CRT consumed earlier
        # the sequence window ends where the NEXT CRT closes (context switch)
        end = (df.index.searchsorted(crts[k + 1]["ts_close"])
               if k + 1 < len(crts) else n)
        sub = df.iloc[:max(end, start)]
        seq = _sequence(sub, crt)
        trig = seq["trigger"]
        if trig is None:
            continue
        t = trig["bar"]                          # absolute within sub == df
        want = crt["direction"]
        entry = float(trig["close"])
        if want == -1:
            sl = max(seq["m1"]["high"], seq["sweep"]["extreme"])
            tp1, tp2 = (crt["high"] + crt["low"]) / 2.0, crt["low"]
            direction = "SELL"
        else:
            sl = min(seq["m1"]["low"], seq["sweep"]["extreme"])
            tp1, tp2 = (crt["high"] + crt["low"]) / 2.0, crt["high"]
            direction = "BUY"
        risk = abs(entry - sl)
        rec = {"market": market, "direction": direction,
               "signal_bar": str(df.index[t]), "entry": round(entry, 6),
               "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6),
               "spec": "PINSTRIPE V6 Hybrid_CRT_TBS"}
        if risk <= 0 or not ((entry > tp2) if want == -1 else (entry < tp2)):
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
        resume = j + 1                           # one position at a time
    rs = [x["r_multiple"] for x in trades if isinstance(x.get("r_multiple"), (int, float))]
    wins = [r for r in rs if r > 0]
    return {"status": "OK", "strategy": SPEC["name"], "trades": trades,
            "summary": {"n": len(trades), "wins": len(wins),
                        "total_r": round(sum(rs), 4) if rs else 0.0},
            "reference": "PINSTRIPE V6 locked rules; owner backtest numbers "
                         "remain owner claims to compare live results against"}
