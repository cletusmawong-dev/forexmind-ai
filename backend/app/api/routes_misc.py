"""Notifications, settings/goals, system info routes (SPEC §14, §30, §35-§37, §45)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

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
    """Upcoming high-impact economic events (next 48h) + honest feed status."""
    from ..market_data.calendar import high_impact, feed_status
    evs = high_impact(hours=48)
    return {"events": [{"title": e["title"], "country": e["country"],
                        "impact": e["impact"], "time": e["time"]} for e in evs[:12]],
            "feed": feed_status()}


@router.get("/candles")
def candles_storage(user_id: str = Depends(get_user_id)):
    """Candle storage stats (real recorded history per market)."""
    from ..market_data import candle_store
    return candle_store.stats()


@router.get("/candles/{market}")
def candles_history(market: str, user_id: str = Depends(get_user_id),
                    limit: int = Query(300, le=600)):
    """Stored 15M candle history (oldest -> newest)."""
    from ..market_data import candle_store
    m = market.upper()
    if m not in candle_store.MARKETS:
        raise HTTPException(status_code=404, detail=f"Unknown market {m}")
    return {"market": m, "tf": candle_store.TF,
            "candles": candle_store.history(m, limit=limit)}


@router.get("/execution/status")
def execution_status(user_id: str = Depends(get_user_id)):
    """Honest MT5 execution status (bridge, account, kill switch, counters)."""
    from ..execution.mt5 import status
    return status(user_id)


@router.post("/execution/mode")
def execution_mode(body: dict, user_id: str = Depends(get_user_id)):
    """Choose how orders are placed: off | manual (your MT5 PC) | vps bridge."""
    from ..execution.mt5 import set_mode, user_mode
    try:
        set_mode(user_id, str(body.get("mode", "off")))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"mode": user_mode(user_id)}


def _connector_user(x_pairing: Optional[str] = None) -> str:
    from ..execution.mt5 import user_for_pairing
    uid = user_for_pairing(x_pairing or "")
    if not uid:
        raise HTTPException(status_code=401, detail="unknown pairing code")
    return uid


@router.post("/execution/connector/hello")
def connector_hello(body: dict, x_pairing: Optional[str] = Header(None)):
    """Connector announces itself (PC on, MT5 running)."""
    from ..execution.mt5 import _touch_connector
    uid = _connector_user(x_pairing)
    _touch_connector(uid, machine=body.get("machine"), account=body.get("account"))
    return {"ok": True, "poll_seconds": 20}


@router.get("/execution/connector/pull")
def connector_pull(x_pairing: Optional[str] = Header(None)):
    """Connector heartbeat + pick up queued orders."""
    from ..execution.mt5 import _touch_connector, pull_commands
    uid = _connector_user(x_pairing)
    _touch_connector(uid)
    return {"commands": pull_commands(uid)}


@router.post("/execution/connector/ack")
def connector_ack(body: dict, x_pairing: Optional[str] = Header(None)):
    """Connector reports an order result."""
    from ..execution.mt5 import ack_command
    uid = _connector_user(x_pairing)
    cmd = ack_command(uid, str(body.get("command_id") or ""), bool(body.get("ok")), body)
    if cmd is None:
        raise HTTPException(status_code=404, detail="unknown command")
    return {"ok": True}


@router.post("/execution/connector/deals")
def connector_deals(body: dict, x_pairing: Optional[str] = Header(None)):
    """Connector pushes fresh MT5 deal history for real W/L confirmation."""
    from ..execution.mt5 import connector_push_deals
    uid = _connector_user(x_pairing)
    return {"confirmed": connector_push_deals(uid, body.get("deals") or [])}


@router.post("/execution/toggle")
def execution_toggle(body: dict, user_id: str = Depends(get_user_id)):
    """Kill switch: enable/disable auto-execution for this account."""
    from ..execution.mt5 import set_execution_enabled, execution_enabled
    enabled = bool(body.get("enabled"))
    set_execution_enabled(user_id, enabled)
    return {"enabled": execution_enabled(user_id)}


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
