"""Spec §7: OBSERVE -> GENERATE -> CHALLENGE -> EVIDENCE -> DECIDE.

attach_evidence() runs AFTER the pipeline produced its answer and BEFORE the
caller consumes it, on EVERY path (action, non-action, hold). It is additive:
it never modifies the decision, only annotates it with the deterministic
Evidence Engine view + epistemic type. Never raises (brain contract).
"""
from __future__ import annotations
from typing import Optional


def attach_evidence(user_id: str, world: dict, signal: Optional[dict], out: dict) -> None:
    try:
        from ..learning.matrix import build_matrix
        from . import engine
        strategy_id = (signal or {}).get("strategy_id")
        if not strategy_id:
            stats = ((world.get("historical") or {}).get("strategy_stats") or {})
            strategy_id = stats.get("strategy_id")
        market = world.get("market")
        if not strategy_id or not market:
            out["evidence"] = None
            out["epistemic"] = {"type": "OBSERVATION",
                                "note": "no strategy/market subject to ground evidence"}
            return
        ev = engine.build_evidence(user_id, strategy_id, market, persist=False)
        out["evidence"] = {
            "subject_id": ev["subject_id"], "state": ev["state"],
            "score": ev["score"], "components": ev["score_components"],
            "sample_size": ev["sample_size"],
            "historically_supported": ev["historically_supported"],
            "currently_supported": ev["currently_supported"],
            "contradictions": ev["contradictions"][:3],
            "conclusion": ev["conclusion"],
            "note": "evidence is independent of model confidence - evidence wins (spec §60)",
        }
        answer = str(out.get("answer", ""))
        out["epistemic"] = {
            "type": "DECISION" if answer in ("ACTION", "NO_ACTION") else "OBSERVATION",
            "note": ("gated, deterministic decision" if answer == "ACTION" else
                     "honest non-action; nothing executable"),
        }
    except Exception as exc:                      # NEVER break the brain
        out["evidence"] = None
        out["evidence_error"] = type(exc).__name__
