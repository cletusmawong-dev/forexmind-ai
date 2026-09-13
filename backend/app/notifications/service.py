"""Notifications (SPEC §30).

Every notification is persisted (frontend pulls via /api/notifications).
When FCM credentials are configured, the same record is also delivered via
Firebase Cloud Messaging. Until then nothing is faked - records simply stay
in-app.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import settings
from ..db.store import get_store


def notify(user_id: Optional[str], type_: str, title: str, body: str,
           signal_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> dict:
    store = get_store()
    doc = store.create("notifications", {
        "userId": user_id,
        "type": type_,
        "title": title,
        "body": body,
        "signal_id": signal_id,
        "meta": meta or {},
        "read": False,
    })
    # Phone delivery (Telegram): best-effort, never blocks the caller.
    if settings.telegram_bot_token and user_id:
        try:
            from .telegram import send_telegram
            text = f"🤖 {title}"
            if body:
                text += f"\n{body}"
            send_telegram(user_id, text)
        except Exception:
            pass
    if settings.fcm_enabled:
        try:  # pragma: no cover - requires FCM credentials
            from firebase_admin import messaging
            messaging.send(multicast=messaging.MulticastMessage(
                tokens=_user_tokens(user_id),
                notification=messaging.Notification(title=title, body=body)))
        except Exception:
            pass
    return doc


def _user_tokens(user_id):  # pragma: no cover - requires FCM credentials
    store = get_store()
    return [d["token"] for d in store.list("users", filters={"id": user_id}, limit=1)
            if d.get("fcm_token")]
