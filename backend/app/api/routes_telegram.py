"""Telegram webhook + test endpoint (phone notifications)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..config import settings
from ..db.store import get_store
from ..notifications.service import notify
from ..notifications.telegram import handle_webhook, send_telegram
from .deps import get_user_id

router = APIRouter(tags=["telegram"])


@router.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != settings.telegram_webhook_secret:
        return {"ok": True}
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}
    try:
        return handle_webhook(payload)
    except Exception:
        # e.g. Firestore daily quota reached - still answer the user honestly
        try:
            import requests as _rq
            msg = payload.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id:
                _rq.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": chat_id,
                          "text": "⏳ ForexMind is in its daily maintenance window (database quota resets ~07:00 GMT). "
                                  "Alerts resume automatically after that - nothing is lost."},
                    timeout=10,
                )
        except Exception:
            pass
        return {"ok": True}


@router.post("/telegram/link-token")
def link_token(user_id: str = Depends(get_user_id)):
    """P13: mint a ONE-TIME, 15-minute token for linking the developer bot
    to THIS account. The old email-based linking is removed: knowing an
    email must never grant notification access."""
    from ..notifications.telegram import create_link_token
    from ..config import settings as _s
    out = create_link_token(user_id)
    if not out:
        from fastapi import HTTPException
        raise HTTPException(503, "could not create link token - try again")
    return {**out, "bot_configured": bool(_s.telegram_bot_token),
            "bot_username": _s.telegram_bot_username or None}


@router.post("/telegram/disconnect")
def disconnect(user_id: str = Depends(get_user_id)):
    """Unlink this user's Telegram (final build section 25): clears the chat
    mapping so nothing can ever be delivered, records when it happened.
    Reconnecting later requires a FRESH one-time token - old tokens and old
    chat mappings are never reused."""
    from ..config import settings as _s
    store = get_store()
    u = store.get("users", user_id) or {}
    if not u.get("telegram_chat_id"):
        return {"ok": True, "linked": False, "note": "Telegram was not connected"}
    from datetime import datetime, timezone
    prev = str(u.get("telegram_chat_id"))
    store.update("users", user_id, {"telegram_chat_id": None,
                                    "telegram_disconnected_at":
                                        datetime.now(timezone.utc).isoformat()})
    store.create("agent_activity", {
        "userId": user_id, "kind": "TELEGRAM",
        "message": f"Telegram disconnected (chat ••••{prev[-4:]}) - no further delivery"})
    return {"ok": True, "linked": False,
            "note": "Telegram disconnected - reconnect anytime with a new code"}


@router.get("/telegram/status")
def status(user_id: str = Depends(get_user_id)):
    """Connection state for the app UI: linked, masked identity, when."""
    from ..config import settings as _s
    store = get_store()
    u = store.get("users", user_id) or {}
    chat = u.get("telegram_chat_id")
    return {"linked": bool(chat),
            "chat_hint": (f"••••{str(chat)[-4:]}" if chat else None),
            "linked_at": u.get("telegram_linked_at"),
            "disconnected_at": u.get("telegram_disconnected_at"),
            "bot_username": _s.telegram_bot_username or None,
            "bot_configured": bool(_s.telegram_bot_token)}


@router.post("/notifications/test")
def test_notification(user_id: str = Depends(get_user_id)):
    sent = send_telegram(user_id, "✅ ForexMind AI test - if you see this on your phone, alerts are live.")
    if not sent:
        notify(user_id, "TELEGRAM_TEST", "Telegram not linked yet",
               "Open Settings in the app and follow the two steps to link your Telegram.")
    return {"sent": sent}
