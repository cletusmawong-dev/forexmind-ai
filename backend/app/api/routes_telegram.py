"""Telegram webhook + test endpoint (phone notifications)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..config import settings
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


@router.post("/notifications/test")
def test_notification(user_id: str = Depends(get_user_id)):
    sent = send_telegram(user_id, "✅ ForexMind AI test - if you see this on your phone, alerts are live.")
    if not sent:
        notify(user_id, "TELEGRAM_TEST", "Telegram not linked yet",
               "Open Settings in the app and follow the two steps to link your Telegram.")
    return {"sent": sent}
