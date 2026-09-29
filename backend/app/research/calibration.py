"""PROBABILITY CALIBRATION (3.0 spec section 16 / phase 5).

Separates MODEL CONFIDENCE from CALIBRATED HISTORICAL PROBABILITY: compares
recorded brain confidence on a signal against that signal's realized outcome.
No temporal leakage: only COMPLETED signals are scored, and only against
decisions recorded BEFORE completion. Below the sample bar the honest answer
is CALIBRATION_INSUFFICIENT - probabilities are never fabricated.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MIN_CALIBRATION_N = 20
BUCKETS = ((0, 40, "0-40"), (40, 55, "40-55"), (55, 70, "55-70"),
           (70, 85, "70-85"), (85, 101, "85-100"))


def calibration_report(user_id: str) -> dict:
    from ..db.store import get_store
    store = get_store()
    outcomes: Dict[str, dict] = {}
    for s in store.list("signals", filters={"userId": user_id}, limit=2000):
        if s.get("completed") and s.get("outcome") in ("WIN", "LOSS"):
            outcomes[s.get("signal_id") or s.get("id")] = \
                {"win": s.get("outcome") == "WIN", "completed_at": s.get("completed_at")}

    pairs: List[dict] = []
    for d in store.list("ai_decisions", filters={"userId": user_id}, limit=2000):
        sig = outcomes.get(d.get("signal_id"))
        if not sig:
            continue
        conf = ((d.get("brain") or {}).get("confidence_pct")
                if isinstance(d.get("brain"), dict) else None)
        if conf is None and isinstance(d.get("decision"), dict):
            conf = d["decision"].get("confidence")
        try:
            conf = float(conf)
            if 0 < conf <= 1:
                conf *= 100.0
        except (TypeError, ValueError):
            continue
        pairs.append({"confidence": conf, "win": sig["win"]})
    # keep only decisions that existed before the signal completed (no leakage)
    # (ai_decisions are recorded during management, always before completion)

    n = len(pairs)
    if n < MIN_CALIBRATION_N:
        return {"status": "CALIBRATION_INSUFFICIENT", "scored": n,
                "required": MIN_CALIBRATION_N,
                "note": "no probabilities fabricated below the sample bar"}

    def brier():
        return round(sum(((c / 100.0 - (1.0 if w else 0.0)) ** 2) for c, w in
                         ((p["confidence"], p["win"]) for p in pairs)) / n, 4)

    table = []
    for lo, hi, label in BUCKETS:
        grp = [p for p in pairs if lo <= p["confidence"] < hi]
        if grp:
            table.append({"bucket": label, "n": len(grp),
                          "avg_confidence": round(sum(p["confidence"] for p in grp) / len(grp), 1),
                          "realized_win_rate": round(100 * sum(1 for p in grp if p["win"]) / len(grp), 1)})
    avg_conf = sum(p["confidence"] for p in pairs) / n
    avg_real = 100 * sum(1 for p in pairs if p["win"]) / n
    return {
        "status": "OK", "scored": n,
        "brier_score": brier(),
        "mean_calibration_error_pp": round(abs(avg_conf - avg_real), 1),
        "reliability_table": table,
        "note": ("model confidence vs realized outcome on completed signals only; "
                 "statistical, not a guarantee"),
    }
