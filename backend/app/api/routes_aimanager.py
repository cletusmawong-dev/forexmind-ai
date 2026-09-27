"""AI-manager dashboard endpoints (master prompt SS40-SS41).

One honest status surface for the phone: models, manager state, daily walls,
recent AI decisions. Nothing here exposes keys - only model IDs and states.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from ..agent.router import get_router
from ..api.deps import get_user_id
from ..db.store import get_store
from ..engine.daily import daily_state

router = APIRouter(prefix="/aimanager", tags=["aimanager"])


@router.get("/status")
def status(user_id: str = Depends(get_user_id)) -> Dict[str, Any]:
    from ..aimanager.engine import get_manager

    store = get_store()
    mgr = get_manager()
    decisions: List[dict] = []
    try:
        for d in store.list("ai_decisions", filters={"userId": user_id}, limit=8):
            decisions.append({
                "trigger": d.get("trigger"),
                "model": d.get("model"),
                "layer": d.get("layer"),
                "action": (d.get("decision") or {}).get("action"),
                "confidence": (d.get("decision") or {}).get("confidence"),
                "reason_codes": (d.get("decision") or {}).get("reason_codes") or [],
                "gate_verdict": d.get("gate_verdict"),
                "error": d.get("error"),
                "symbol": d.get("symbol"),
                "createdAt": d.get("createdAt"),
            })
    except Exception:
        decisions = []

    try:
        daily = daily_state(user_id)
    except Exception:
        daily = {}

    try:
        router_view = get_router().status()
    except Exception:
        router_view = {}

    return {
        "router": router_view,
        "manager": mgr.status(),
        "daily": {k: daily.get(k) for k in
                  ("day", "tz", "realized_usd", "floating_usd", "total_usd",
                   "daily_profit_target_usd", "daily_loss_limit_usd",
                   "remaining_target_usd", "remaining_loss_usd",
                   "hit_target", "hit_loss", "status",
                   "realized_basis", "floating_source")},
        "recent_decisions": decisions,
        "note": ("The manager acts on OPEN positions only. It never opens, "
                 "sizes or vetoes entries."),
    }


# ===========================================================================
# Brain 2.0 (Stage 2): world model + reasoning pipeline surfaces.
# These endpoints NEVER execute anything - the manager tick is the only
# executor, and it always passes the deterministic risk gate first.
# ===========================================================================
from fastapi import HTTPException
from pydantic import BaseModel


@router.get("/brain/world")
def brain_world(market: str, user_id: str = Depends(get_user_id)) -> Dict[str, Any]:
    """Structured world-model snapshot (no AI call, no execution)."""
    from ..ai.world_model import build_world_model
    from ..state import State
    try:
        world = build_world_model(user_id, market, provider=State.provider)
    except Exception as exc:
        raise HTTPException(503, f"world model unavailable: {type(exc).__name__}")
    return {"world_model": world,
            "note": "Pure data snapshot - freshness-audited, nothing fabricated."}


class BrainReviewIn(BaseModel):
    ticket: int


@router.post("/brain/review")
def brain_review(body: BrainReviewIn,
                 user_id: str = Depends(get_user_id)) -> Dict[str, Any]:
    """Run the full Brain 2.0 pipeline for one open position and return the
    decision record. ADVICE ONLY - nothing is executed here."""
    from ..ai.brain import run_brain
    from ..ai.world_model import build_world_model
    from ..aimanager.engine import get_manager
    from ..engine.daily import daily_state
    from ..state import State

    mgr = get_manager()
    pos = next((p for p in mgr._positions(user_id)
                if int(p.get("ticket") or 0) == int(body.ticket)), None)
    if pos is None:
        raise HTTPException(404, "Position not found on the broker feed")
    sig = mgr._link_signal(user_id, pos)
    try:
        daily = daily_state(user_id)
    except Exception:
        daily = None
    world = build_world_model(user_id, pos.get("app_market") or pos.get("symbol", ""),
                              position=pos, signal=sig, daily=daily,
                              provider=State.provider)
    result = run_brain(user_id, world, escalate=True, signal=sig)
    result["advisory_only"] = True
    return {"brain": result, "world_freshness": world.get("freshness"),
            "note": ("Advice only. Execution happens exclusively through the "
                     "manager tick + deterministic risk gate.")}
