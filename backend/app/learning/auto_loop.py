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


def genuinely_better(exp_doc: dict) -> bool:
    """Owner directive (2026-10-03): the system may research constantly, but it
    may only surface a REVIEW recommendation (and Telegram) when it has found
    a BETTER version proven on accurate data. Strict bar - ALL must hold:
      - verdict is IMPROVED on the same-dataset A/B
      - enough trades (min_trades_for_experiment) - i.e. not INSUFFICIENT_DATA
      - no overfitting risk (train/validation split stayed consistent)
      - no small-sample caveat
      - walk-forward consistent across time folds (when measurable)
    Everything else stays in the Learning Lab as recorded research - silent."""
    d = exp_doc or {}
    if d.get("result") != "IMPROVED":
        return False
    if d.get("overfitting_risk") or d.get("small_sample_warning"):
        return False
    wf = d.get("walk_forward") or {}
    if wf.get("consistent") is False:
        return False
    return True


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
    # 2b) EVIDENCE refresh (3.0 spec Stage 1): deterministic recompute of every
    # strategy x market subject - classification only, never mutates anything.
    try:
        from ..evidence import engine as ev_engine
        out["evidence_refreshed"] = len(ev_engine.refresh_all(user_id) or [])
    except Exception:
        pass
    # 2c) INCIDENT sweep (stage 7): cheap detectors, deduped, advisory.
    try:
        from ..system.incidents import sweep as inc_sweep
        out["incidents_raised"] = inc_sweep(user_id).get("raised", 0)
    except Exception:
        pass
    # 2d) SHADOW cycle (stage 5): no-op unless a strategy is flagged shadow.
    try:
        from ..research.shadow import run_shadow_cycle
        sh = run_shadow_cycle(user_id) or {}
        out["shadow"] = {k: sh.get(k) for k in ("detected", "evaluated")
                         if sh.get(k) is not None}
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
        # ONLY when the experiment is genuinely better (see genuinely_better):
        # failed / inconclusive / risky experiments are recorded for the
        # Learning Lab but NEVER ping the owner.
        try:
            full_exp = store.get("experiments", out["experiment"]["id"]) or {}
            if not genuinely_better(full_exp):
                out["experiment"]["surfaced"] = False
                return out
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
                out["experiment"]["surfaced"] = True
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
