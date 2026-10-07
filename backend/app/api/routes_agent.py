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
    """Conversational AI (master upgrade §5): deterministic answers from
    stored data first; free-form questions fall back to the AI provider
    (XKiro) grounded on the caller's OWN context + current chart. The model
    can answer and explain - it can NEVER execute, change strategies or
    cross to another user's data (the system prompt + context scoping
    enforce this by construction)."""
    from ..core.plain_text import to_plain
    result = chat_answer(body.message, user_id)
    reply = result.get("reply") or result.get("answer") or ""
    unmatched = ("don't have stored data matching" in reply
                 or "CAPABILITIES" in reply or reply.startswith("I can answer from"))
    if unmatched:
        answer, provider = _llm_chat(body, user_id)
        if answer:
            result = {"reply": answer, "provider": provider}
    if isinstance(result.get("answer"), str):
        result["answer"] = to_plain(result["answer"])
    if isinstance(result.get("reply"), str):
        result["reply"] = to_plain(result["reply"])
    result["ai"] = ai_status()
    return result


def _llm_chat(body: ChatIn, user_id: str):
    """Free-form fallback: grounded on the caller's own data only."""
    from ..db.store import get_store
    from datetime import datetime, timezone
    store = get_store()
    ctx: dict = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    # chart context - what the user is looking at right now
    if body.chart_symbol:
        sym = body.chart_symbol.upper()
        tf = (body.chart_tf or "15M").upper()
        entry = {"symbol": sym, "timeframe": tf}
        try:
            from .routes_charts import _render_s2_state
            st = _render_s2_state(sym, "15M")
            entry["strategy2_state"] = st.get("state")
            entry["levels"] = st.get("levels")
        except Exception:
            pass
        mine = [d for d in store.list("signals", filters={"userId": user_id}, limit=100)
                if str(d.get("market", "")).upper() == sym]
        mine.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
        if mine:
            last = mine[0]
            entry["last_own_signal"] = {
                "signal_id": last.get("signal_id"), "direction": last.get("direction"),
                "status": last.get("status"), "outcome": last.get("outcome"),
                "r_multiple": last.get("r_multiple"),
                "reason": (last.get("reason") or "")[:400]}
        ctx["chart"] = entry
    # the caller's own recent performance (never another user's)
    mine = store.list("signals", filters={"userId": user_id, "completed": True}, limit=30)
    ctx["recent_closed_trades"] = [
        {"market": d.get("market"), "strategy_id": d.get("strategy_id"),
         "outcome": d.get("outcome"), "r_multiple": d.get("r_multiple")}
        for d in mine[:10]]
    try:
        from ..agent.ai_provider import get_ai_provider, AIUnavailable
        p = get_ai_provider()
        if getattr(p, "name", "") != "xkiro":
            return None, None  # no external AI configured - keep honest fallback
        out = p.complete(body.message, ctx, timeout=45, max_tokens=520)
        return out, p.name
    except Exception:
        return None, None
