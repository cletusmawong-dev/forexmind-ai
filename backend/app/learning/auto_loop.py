"""Automated research loop (Master Upgrade Phase 8 / Stage 4).

One throttled cycle (called from the main learning pass, ~6h):

    1. DISCOVER   research.discover()  -> segment divergences (bounded)
    2. PROPOSE    hypotheses.propose_from_lessons (already wired elsewhere)
    3. EXPERIMENT run the experiment for at most ONE pending hypothesis per
                  cycle (one variable, same dataset, walk-forward + OOS
                  validation inside ExperimentEngine)
    4. RECOMMEND  a READY_FOR_REVIEW experiment becomes a [REVIEW]
                  recommendation - the human decides; NOTHING is ever
                  applied automatically (versions.apply is approval-only).

Every step is bounded, audited and never raises.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def run_research_cycle(user_id: str, max_discoveries: int = 3,
                       max_experiments: int = 1) -> Dict[str, Any]:
    from ..db.store import get_store
    from . import research
    store = get_store()
    out: Dict[str, Any] = {"discovered": 0, "experiment": None,
                           "recommended": 0, "skipped": None}

    # 1) DISCOVER (divergences + best-session suggestions) ---------------
    try:
        made = research.discover(user_id)
        out["discovered"] = len(made or [])
    except Exception as exc:
        out["skipped"] = f"discover: {type(exc).__name__}"
    try:
        from .matrix import suggest_best_sessions
        out["session_suggestions"] = len(suggest_best_sessions(user_id) or [])
    except Exception:
        pass

    # 3) EXPERIMENT (max ONE per cycle) -----------------------------------
    hyp = None
    try:
        pending = store.list("hypotheses", filters={"userId": user_id,
                                                    "status": "PROPOSED"},
                             order_by="createdAt", desc=True, limit=10)
        exps = store.list("experiments", filters={"userId": user_id}, limit=200)
        tested = {e.get("hypothesis_id") for e in exps}
        hyp = next((h for h in pending if h["id"] not in tested), None)
    except Exception as exc:
        out["skipped"] = f"pending-lookup: {type(exc).__name__}"

    if hyp is not None:
        try:
            from ..state import State
            engine = State.experiments
            exp = engine.run_from_hypothesis(user_id, hyp)
            out["experiment"] = {"id": exp["id"], "code": exp.get("experiment_code"),
                                 "result": exp.get("result")}
        except Exception as exc:
            out["skipped"] = f"experiment: {type(exc).__name__}"
            return out

        # 4) RECOMMEND (REVIEW - human decides) ---------------------------
        try:
            fresh = store.list("recommendations",
                               filters={"userId": user_id,
                                        "dedupe_key": f"EXPERIMENT|{hyp['id']}"},
                               limit=1)
            if not fresh:
                store.create("recommendations", {
                    "userId": user_id,
                    "type": "EXPERIMENT_REVIEW",
                    "status": "REVIEW",
                    "strategy_id": hyp.get("strategy_id"),
                    "strategy_name": hyp.get("strategy_name"),
                    "hypothesis_id": hyp["id"],
                    "experiment_id": out["experiment"]["id"],
                    "claim": (f"Automated one-variable experiment for "
                              f"{hyp.get('hypothesis_id')} "
                              f"({hyp.get('variable')} {hyp.get('old_value')} -> "
                              f"{hyp.get('new_value')}) finished: "
                              f"{out['experiment']['result']}. Review and "
                              f"approve or reject - nothing was applied."),
                    "never_auto_applied": True,
                    "dedupe_key": f"EXPERIMENT|{hyp['id']}",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                })
                out["recommended"] = 1
                try:
                    from ..notifications.service import notify
                    notify(user_id, "RESEARCH_REVIEW",
                           f"Experiment ready for review - {hyp.get('hypothesis_id')}",
                           f"{hyp.get('variable')} {hyp.get('old_value')} -> "
                           f"{hyp.get('new_value')}: {out['experiment']['result']}. "
                           "Open Learning Lab to approve or reject.",
                           meta={"hypothesis_id": hyp["id"]})
                except Exception:
                    pass
        except Exception:
            pass
    return out
