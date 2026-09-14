"""Agent routes - status, activity, scan trigger, chat (SPEC §29, §33, §34)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..agent import core as agent_core
from ..agent.chat import answer as chat_answer
from ..agent.ai_provider import ai_status
from ..config import INITIAL_MARKETS
from ..models.schemas import ChatIn
from ..state import State
from .deps import get_user_id

router = APIRouter(tags=["agent"])


@router.get("/agent/status")
def status(user_id: str = Depends(get_user_id)):
    st = agent_core.agent_status(user_id)
    st["ai"] = ai_status()
    st["market_data"] = {"provider": State.provider.name, "demo": State.provider.is_demo}
    return st


@router.get("/agent/activity")
def activity(user_id: str = Depends(get_user_id), limit: int = Query(40, le=200)):
    store = State.store
    docs = store.list("agent_activity", limit=400)
    def sort_key(d):
        return d.get("ts_override") or d.get("createdAt") or ""
    docs.sort(key=sort_key, reverse=True)
    try:  # replay progress only exists on the demo provider - live mode reports None
        progress = State.provider.replay_progress("XAUUSD")
    except AttributeError:
        progress = None
    return {"activity": docs[:limit],
            "replay": {"demo": getattr(State.provider, "is_demo", False),
                       "progress": progress}}


@router.post("/agent/scan")
def scan_now(user_id: str = Depends(get_user_id)):
    """Manual observe->analyze->check->validate->generate pass."""
    created = []
    for market in INITIAL_MARKETS:
        for tf in ("15M", "1H"):
            created += State.engine.scan(user_id, market, tf, log_activity=True)
    if not created:
        agent_core.log("No qualifying setup. Capital protected.", kind="NO_SETUP")
    return {"created": len(created), "signals": created}


@router.post("/chat")
def chat(body: ChatIn, user_id: str = Depends(get_user_id)):
    result = chat_answer(body.message, user_id)
    result["ai"] = ai_status()
    return result
