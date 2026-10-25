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

# Telegram carries EXACTLY the owner's four messages (directive
# 2026-10-25) - nothing else, and nothing can force its way through:
#   1. MORNING_BRIEF    - the daily 07:00 brief (every day)
#   2. NEWS_ALERT       - high-impact news ~15 minutes ahead
#   3. TRADE_ENTERED    - a REAL broker fill (fired by the executor, never
#                         by signal creation)
#   4. TRADE_COMPLETED  - the trade closed (TP / SL / trail / time exit)
#      + TP{k}_HIT / TP{k}_HIT_LIVE - live TP touches on open positions
# Everything else - signals without an entry, management/stop updates,
# execution skips and failures, approvals, research reviews, regime
# notes, tests - stays IN-APP ONLY (the Notifications screen keeps it
# all). This supersedes the 2026-10-09/10 important-only sets and removes
# the meta force flag and the TELEGRAM_IMPORTANT_TYPES env extension;
# the s.8 commentary denylist (policy.py) still applies on top.
_TELEGRAM_ALLOWED_EXACT = {"MORNING_BRIEF", "NEWS_ALERT", "TRADE_ENTERED",
                           "TRADE_COMPLETED"}


def _telegram_worthy(type_: str, meta: Optional[Dict[str, Any]]) -> bool:
    try:   # STRICT MESSAGING (2026-10-10, s.8): commentary can never be forced
        from .policy import telegram_allowed
        if not telegram_allowed(type_, meta):
            return False
    except Exception:
        pass
    if type_ in _TELEGRAM_ALLOWED_EXACT:
        return True
    # live TP events on real broker tickets (TP1_HIT, TP2_HIT_LIVE, ...)
    return type_.startswith("TP") and type_.endswith(("_HIT", "_HIT_LIVE"))


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
    # Phone delivery (Telegram): the owner's four messages ONLY (directive
    # 2026-10-25 - see _TELEGRAM_ALLOWED_EXACT). Everything else stays
    # in-app (the Notifications screen still has it all).
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
