"""Strategy Degradation Engine (3.0 spec §14, Stage 3).

Compares HISTORICAL performance vs RECENT performance per strategy and
classifies: INSUFFICIENT / HEALTHY / WATCH / DEGRADING / INVESTIGATION.

REPORTING ONLY: this module never pauses, never mutates strategy docs, never
touches execution. Hard safety triggers stay explicitly configured elsewhere
(spec §14: do not auto-pause on normal statistical variation).
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..db.store import get_store


def _series(rows: List[dict]) -> List[dict]:
    rows.sort(key=lambda s: str(s.get("completed_at") or s.get("candle_time") or ""))
    return rows


def strategy_health(user_id: str, strategy_id: str,
                    recent_n: int = 20) -> Dict[str, Any]:
    store = get_store()
    rows = _series([s for s in store.list("signals", filters={"userId": user_id}, limit=2000)
                    if s.get("completed") and s.get("strategy_id") == strategy_id])
    n = len(rows)

    def wr_rs(rs):
        if not rs:
            return None, None
        wins = sum(1 for s in rs if s.get("outcome") == "WIN")
        r = [float(s.get("r_multiple") or 0.0) for s in rs]
        return round(100.0 * wins / len(rs), 1), round(sum(r) / len(r), 3)

    overall_wr, overall_avg = wr_rs(rows)
    recent = rows[-recent_n:]
    recent_wr, recent_avg = wr_rs(recent)

    streak = 0
    for s in reversed(rows):
        if s.get("outcome") == "LOSS":
            streak += 1
        else:
            break

    cum = peak = 0.0
    dd = 0.0
    for s in rows:
        cum += float(s.get("r_multiple") or 0.0)
        peak = max(peak, cum)
        dd = min(dd, cum - peak)

    tp_dist: Dict[str, int] = {"tp1": 0, "tp2": 0, "tp3": 0, "sl": 0}
    for s in rows:
        hits = int(s.get("tp_hits") or 0)
        if hits >= 3:
            tp_dist["tp3"] += 1
        elif hits == 2:
            tp_dist["tp2"] += 1
        elif hits == 1:
            tp_dist["tp1"] += 1
        elif s.get("outcome") == "LOSS":
            tp_dist["sl"] += 1

    metrics = {
        "n": n, "recent_n": len(recent),
        "wr_overall": overall_wr, "wr_recent": recent_wr,
        "avg_r_overall": overall_avg, "avg_r_recent": recent_avg,
        "wr_drop_pp": (round(overall_wr - recent_wr, 1)
                       if overall_wr is not None and recent_wr is not None else None),
        "current_losing_streak": streak,
        "open_drawdown_r": round(dd, 2),
        "tp_distribution": tp_dist,
    }

    if n < 10 or recent_wr is None:
        state, note = "INSUFFICIENT", "fewer than 10 completed signals - no degradation signal yet"
    elif len(recent) < 5:
        state, note = "HEALTHY", "too few recent trades to judge - historically healthy"
    else:
        drop = (overall_wr or 0) - recent_wr
        if drop >= 22 or streak >= 5 or dd <= -4.0:
            state = "INVESTIGATION"
            note = "material recent deterioration - open a one-variable investigation before changing anything"
        elif drop >= 15 or ((recent_avg or 0) < 0 and drop >= 10):
            state = "DEGRADING"
            note = "recent performance well below historical - monitor closely, experiment may be warranted"
        elif drop >= 8:
            state = "WATCH"
            note = "recent win rate softening - within statistical noise for now"
        else:
            state, note = "HEALTHY", "recent performance consistent with history"

    return {"strategy_id": strategy_id, "state": state, "metrics": metrics,
            "note": note,
            "policy": "reporting only - this engine never pauses or mutates anything (spec §14)"}
