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
