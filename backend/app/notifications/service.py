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

# Telegram = important only. TP1_HIT_LIVE / TP2_HIT_LIVE / ... are matched
# by prefix+suffix; extra one-off types can be forced per call via
# meta={"telegram_important": True}; operations can extend the set with
# TELEGRAM_IMPORTANT_TYPES (comma-separated) without a code change.
_TELEGRAM_IMPORTANT = {
    "NEW_SIGNAL", "EXECUTION_FAILED", "TP_AUDIT_LEAK",
    "STRATEGY_VERSION_UPDATED", "APPROVAL_REQUIRED", "RESEARCH_REVIEW",
    "MORNING_BRIEF", "AI_ESCALATION_DOWN", "TELEGRAM_TEST", "REGIME_CHANGE",
    "NEWS_ALERT", "TRADE_COMPLETED",
}


def _telegram_worthy(type_: str, meta: Optional[Dict[str, Any]]) -> bool:
    if meta and meta.get("telegram_important"):
        return True
    if type_ in _TELEGRAM_IMPORTANT:
        return True
    if type_.startswith("TP") and type_.endswith(("_HIT", "_HIT_LIVE")):
        return True          # TP events on real broker tickets
    for extra in (settings.telegram_important_types or "").split(","):
        if extra.strip() and type_ == extra.strip():
            return True
    return False


def notify(user_id: Optional[str], type_: str, title: str, body: str,
           signal_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> dict:
    from ..core.plain_text import to_plain
    title, body = to_plain(title), to_plain(body)
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
    # Phone delivery (Telegram): IMPORTANT messages only (owner request
    # 2026-10-09 - "no random messages"). Signals, TP/SL hits on real
    # tickets, execution failures, approvals, regime changes, the daily
    # brief. Everything else stays in-app (Notifications screen still has
    # it all). meta={"telegram_important": True} forces a one-off through.
    if settings.telegram_bot_token and user_id and _telegram_worthy(type_, meta):
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
