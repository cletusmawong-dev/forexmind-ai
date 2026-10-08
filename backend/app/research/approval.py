"""HUMAN APPROVAL bridge (owner brief 2026-10-08, section 14 - "no exceptions").

READY_FOR_REVIEW is the research engine's ceiling. Only an explicit human
decision moves a candidate forward, and even then the result is:

    approved  -> strategies doc (status DISABLED = NOT live) + an IMMUTABLE
                 version v1.0 snapshot (frozen rules + evidence report)
    rejected  -> REJECTED with the owner's reason recorded in memory

Nothing here activates trading. Controlled deployment (flipping such a
strategy live) remains a separate, explicit owner action - and until a live
interpreter for research.rule_interp.v1 programs exists, the frozen snapshot
is evidence, not a tradable strategy. The live-trading architecture is
untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ..db.store import get_store

STRATEGY_ID_PREFIX = "research_"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_store():
    from .engine import COLLECTION
    return get_store(), COLLECTION


def get_candidate(candidate_id: str) -> Dict[str, Any]:
    store, COLL = candidate_store()
    rows = store.list(COLL, filters={"id": candidate_id}, limit=1)
    if not rows:
        raise ValueError("candidate not found")
    return rows[0]


def submit_for_approval(candidate_id: str) -> Dict[str, Any]:
    """Owner-visible queueing (can also happen automatically at READY_FOR_REVIEW)."""
    store, COLL = candidate_store()
    cand = get_candidate(candidate_id)
    if cand.get("stage") != "READY_FOR_REVIEW":
        raise ValueError(f"candidate stage is {cand.get('stage')}, "
                         "not READY_FOR_REVIEW")
    if not cand.get("approval_requested"):
        cand.setdefault("events", []).append(
            {"stage": "READY_FOR_REVIEW", "at": _now(),
             "detail": "queued for HUMAN approval"})
        store.update(COLL, candidate_id,
                     {"approval_requested": True, "updatedAt": _now()})
    return {"candidate_id": candidate_id, "approval_requested": True}


def approve_candidate(user_id: str, candidate_id: str, note: str = "") -> Dict[str, Any]:
    """EXPLICIT human approval -> immutable version snapshot. Never auto-live."""
    store, COLL = candidate_store()
    cand = get_candidate(candidate_id)
    if cand.get("stage") not in ("READY_FOR_REVIEW", "APPROVED_AWAITING_DEPLOYMENT"):
        raise ValueError(f"only READY_FOR_REVIEW candidates can be approved "
                         f"(stage is {cand.get('stage')})")
    if cand.get("stage") == "APPROVED_AWAITING_DEPLOYMENT":
        raise ValueError("candidate already approved")
    strategy_id = f"{STRATEGY_ID_PREFIX}{candidate_id}"
    report = cand.get("report") or {}
    version_doc = store.create("strategy_versions", {
        "strategy_id": strategy_id,
        "version": "1.0",
        "params": cand.get("implemented") or {},      # frozen program
        "changes": [{"variable": "candidate",
                     "old_value": "RESEARCH_PIPELINE",
                     "new_value": strategy_id}],
        "candidate_id": candidate_id,
        "immutable": True,                            # never auto-mutated
        "active": False,                              # NOT live - controlled
        "evidence_report": report,                    # what the human approved
        "approved_by": user_id,
        "note": note or "Approved from Research Pipeline evidence report",
    })
    store.create("strategies", {
        "id": strategy_id,
        "name": cand.get("name"),
        "market": cand.get("market"), "timeframe": cand.get("timeframe"),
        "status": "DISABLED",                         # explicitly NOT live
        "source": "research_pipeline",
        "engine": "research.rule_interp.v1",
        "active_version": "1.0",
        "candidate_id": candidate_id,
        "evidence_level": report.get("evidence_level"),
        "createdAt": _now(),
    })
    cand.setdefault("events", []).append(
        {"stage": "APPROVED_AWAITING_DEPLOYMENT", "at": _now(),
         "detail": f"HUMAN APPROVAL by {user_id} -> immutable v1.0 "
                   f"({strategy_id}); deployment stays a separate explicit step"})
    store.update(COLL, candidate_id,
                 {"stage": "APPROVED_AWAITING_DEPLOYMENT",
                  "strategy_id": strategy_id, "version": "1.0",
                  "approved_by": user_id, "updatedAt": _now()})
    from . import memory
    memory.remember_candidate(store, cand, cand.get("implemented") or {},
                              "APPROVED_AWAITING_DEPLOYMENT",
                              note or "human-approved from evidence report",
                              report_id=candidate_id)
    return {"strategy_id": strategy_id, "version": "1.0",
            "status": "APPROVED_AWAITING_DEPLOYMENT",
            "note": "snapshot stored; the strategy is NOT live"}


def reject_candidate(user_id: str, candidate_id: str, reason: str) -> Dict[str, Any]:
    """Explicit human rejection - recorded in research memory with the reason."""
    store, COLL = candidate_store()
    cand = get_candidate(candidate_id)
    if cand.get("stage") == "APPROVED_AWAITING_DEPLOYMENT":
        raise ValueError("candidate already approved")
    cand.setdefault("events", []).append(
        {"stage": "REJECTED", "at": _now(),
         "detail": f"HUMAN REJECTION by {user_id}: {reason}"})
    store.update(COLL, candidate_id, {"stage": "REJECTED",
                                      "rejected_by": user_id,
                                      "human_rejection_reason": reason,
                                      "updatedAt": _now()})
    from . import memory
    memory.remember_candidate(store, cand, cand.get("implemented") or {},
                              "REJECT", f"human rejection: {reason}",
                              report_id=candidate_id)
    return {"candidate_id": candidate_id, "stage": "REJECTED"}
