"""MONTE CARLO LAB (3.0 spec section 30).

Resamples the REAL completed-trade R series of a strategy (seeded =>
reproducible). Outputs drawdown / losing-streak / risk-of-ruin DISTRIBUTIONS.
Clearly labeled: these are SIMULATIONS, not predictions.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List


def _pct(sorted_vals: List[float], p: float):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(p * (len(sorted_vals) - 1))))
    return round(sorted_vals[i], 2)


def simulate(r_series: List[float], n_sims: int = 1000, horizon: int = 100,
             ruin_r: float = -20.0, seed: int = 42) -> dict:
    clean = [float(r) for r in r_series if r is not None]
    if len(clean) < 10:
        return {"status": "INSUFFICIENT_DATA", "n": len(clean),
                "note": "need >= 10 completed trades to resample - no simulation invented"}
    rng = random.Random(seed)
    dds, streaks, finals, ruined = [], [], [], 0
    for _ in range(max(1, min(int(n_sims), 5000))):
        cum = peak = 0.0
        dd = streak = worst_streak = 0.0
        blown = False
        for _ in range(horizon):
            r = clean[rng.randrange(len(clean))]
            cum += r
            peak = max(peak, cum)
            dd = min(dd, cum - peak)
            streak = streak + 1 if r < 0 else 0
            worst_streak = max(worst_streak, streak)
            if cum <= ruin_r:
                blown = True
                break
        dds.append(dd)
        streaks.append(worst_streak)
        finals.append(cum)
        ruined += 1 if blown else 0

    dds.sort(); streaks.sort(); finals.sort()
    return {
        "status": "SIMULATION",  # explicitly NOT a prediction
        "n_sims": min(n_sims, 5000), "horizon_trades": horizon,
        "source_trades": len(clean), "seed": seed,
        "drawdown_r": {"p50": _pct(dds, .5), "p90": _pct(dds, .9),
                       "p99": _pct(dds, .99), "worst": dds[-1] if dds else None},
        "max_losing_streak": {"p50": _pct(streaks, .5), "p90": _pct(streaks, .9),
                              "worst": streaks[-1] if streaks else None},
        "final_r": {"p5": _pct(finals, .05), "p50": _pct(finals, .5),
                    "p95": _pct(finals, .95)},
        "risk_of_ruin": {"threshold_r": ruin_r,
                         "probability": round(ruined / max(1, min(n_sims, 5000)), 4)},
        "disclaimer": "randomized resampling of historical R - a simulation, never a prediction",
    }
