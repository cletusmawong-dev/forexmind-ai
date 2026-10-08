"""PROBABILITY FROM EVIDENCE (owner brief 2026-10-08, section 9).

Probabilities come from MEASURED HISTORICAL OUTCOMES of the candidate's own
verified trades - never from an AI model's subjective confidence. Every
estimate ships with sample size, period, conditions and a Wilson 95%
interval (calibration information). Below the sample floor the only honest
answer is INSUFFICIENT DATA - percentages are never manufactured.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

MIN_PROB_N = 30          # global floor
MIN_COND_N = 15          # per-condition (regime/session/symbol) floor


def _wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    """Wilson score interval - honest small-sample calibration info."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _p(k: int, n: int, label: str, period: str) -> Dict[str, Any]:
    if n < MIN_PROB_N:
        return {"metric": label, "status": "INSUFFICIENT_DATA",
                "n": n, "note": f"need >= {MIN_PROB_N} verified trades"}
    p = k / n
    return {"metric": label, "probability": round(p, 4),
            "percent": round(100 * p, 1), "n": n, "period": period,
            "wilson95": _wilson(k, n)}


def from_trades(trades: List[dict], conditions: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Probability block from verified backtest/shadow trades.

    Trades carry exit_reason SL/TP + r; TP-level probabilities use mfe_r
    (max favorable excursion in R) against the ladder multiples recorded on
    the trade (tp1_r..tp3_r when the source defined them).
    """
    n = len(trades)
    period = (f"{min(t['entry_time'] for t in trades)[:10]} .. "
              f"{max(t['entry_time'] for t in trades)[:10]}") if n else None
    wins = sum(1 for t in trades if t["r"] > 0)
    sls = sum(1 for t in trades if t["exit_reason"] == "SL")
    tp1 = tp2 = tp3 = 0
    for t in trades:
        mfe = t.get("mfe_r") or 0.0
        if mfe >= 1.0:
            tp1 += 1
        if mfe >= 2.0:
            tp2 += 1
        if mfe >= 3.0:
            tp3 += 1
    avg_r = round(sum(t["r"] for t in trades) / n, 4) if n else None
    out = {
        "basis": "FOREXMIND VERIFIED trades (measured, not AI confidence)",
        "conditions_used": conditions or {},
        "period": period,
        "P_win": _p(wins, n, "P(win)", period),
        "P_SL": _p(sls, n, "P(stop-loss)", period),
        "P_TP1": _p(tp1, n, "P(reach +1R)", period),
        "P_TP2": _p(tp2, n, "P(reach +2R)", period),
        "P_TP3": _p(tp3, n, "P(reach +3R)", period),
        "expected_R": ({"value": avg_r, "n": n, "period": period}
                       if n >= MIN_PROB_N else
                       {"status": "INSUFFICIENT_DATA", "n": n}),
    }
    return out


def conditional(trades: List[dict], key: str, get_value) -> Dict[str, Any]:
    """Conditional probabilities by regime / session / symbol.

    get_value(trade) -> the condition value (e.g. the regime at signal time).
    Only conditions with >= MIN_COND_N verified trades get numbers.
    """
    buckets: Dict[str, List[dict]] = {}
    for t in trades:
        v = get_value(t)
        if v is None:
            continue
        buckets.setdefault(str(v), []).append(t)
    out: Dict[str, Any] = {}
    for v, ts in sorted(buckets.items()):
        n = len(ts)
        if n < MIN_COND_N:
            out[v] = {"status": "INSUFFICIENT_DATA", "n": n}
            continue
        wins = sum(1 for t in ts if t["r"] > 0)
        out[v] = {"P_win": round(wins / n, 4),
                  "percent": round(100 * wins / n, 1),
                  "n": n,
                  "expected_R": round(sum(t["r"] for t in ts) / n, 4),
                  "wilson95": _wilson(wins, n)}
    if not out:
        out["note"] = "no conditioned trades recorded"
    return out
