"""Notifications, settings/goals, system info routes (SPEC §14, §30, §35-§37, §45)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..agent import core as agent_core
from ..agent.ai_provider import ai_status
from ..config import INITIAL_MARKETS, settings
from ..models.schemas import GoalSettings, RiskSettings
from ..state import State
from .deps import get_user_id

router = APIRouter(tags=["misc"])


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(user_id: str = Depends(get_user_id), limit: int = Query(50, le=200)):
    items = State.store.list("notifications", filters={"userId": user_id}, limit=limit)
    unread = sum(1 for n in items if not n.get("read"))
    return {"notifications": items, "unread": unread}


@router.post("/notifications/read")
def mark_read(body: dict, user_id: str = Depends(get_user_id)):
    store = State.store
    ids = body.get("ids")
    for n in store.list("notifications", filters={"userId": user_id, "read": False}, limit=300):
        if ids is None or n["id"] in ids:
            store.update("notifications", n["id"], {"read": True})
    return {"ok": True}


# ---------------------------------------------------------------- goals & risk
@router.get("/goals")
def get_goals(user_id: str = Depends(get_user_id)):
    return agent_core.get_goals(user_id)


@router.patch("/goals")
def patch_goals(body: GoalSettings, user_id: str = Depends(get_user_id)):
    return agent_core.patch_goals(user_id, body.model_dump(exclude_none=True))


@router.get("/settings")
def get_settings(user_id: str = Depends(get_user_id)):
    return agent_core.get_risk(user_id)


@router.patch("/settings")
def patch_settings(body: RiskSettings, user_id: str = Depends(get_user_id)):
    return agent_core.patch_risk(user_id, body.model_dump(exclude_none=True))


# ---------------------------------------------------------------- system info
@router.get("/calendar")
def calendar():
    """Upcoming high-impact economic events (next 48h)."""
    from ..market_data.calendar import high_impact
    evs = high_impact(hours=48)
    return {"events": [{"title": e["title"], "country": e["country"],
                        "impact": e["impact"], "time": e["time"]} for e in evs[:12]]}


@router.get("/agent/brief")
def morning_brief(user_id: str = Depends(get_user_id)):
    """Today's AI morning brief (built once, cached per day)."""
    from ..agent import brief as brief_mod
    return {"brief": brief_mod.build(user_id), "date": brief_mod.today_key()}


@router.get("/system/info")
def system_info():
    return {
        "app": settings.app_name,
        "version": "0.1.0",
        "market_data": {
            "provider": State.provider.name,
            "demo": State.provider.is_demo,
            "note": ("DEMO / HISTORICAL data - replayed stored datasets. Never presented "
                     "as live prices. Plug a real provider via the MarketDataProvider "
                     "interface.") if State.provider.is_demo else "Live provider connected.",
        },
        "ai": ai_status(),
        "database": {
            "store": type(State.store).__name__,
            "firestore_active": type(State.store).__name__ == "FirestoreStore",
            "note": "Set FIREBASE_PROJECT_ID (+ credentials) to activate live Firestore.",
            "init_error": getattr(__import__("app.db.store", fromlist=["store_init_error"]), "store_init_error", None),
        },
        "notifications": {"fcm": settings.fcm_enabled,
                          "telegram": {"configured": bool(settings.telegram_bot_token),
                                       "bot_username": settings.telegram_bot_username}},
        "markets": INITIAL_MARKETS,
        "disclaimer": ("ForexMind AI is a research & signal agent. It never executes "
                       "trades and never guarantees profits."),
    }
