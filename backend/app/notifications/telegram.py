"""Telegram delivery + account linking (phone notifications).

Linking flow (Master Upgrade P13 - one developer bot, one-time tokens):
  1. The ONE developer bot is configured via TELEGRAM_BOT_TOKEN
     (+ TELEGRAM_BOT_USERNAME for deep links).
  2. Webhook is registered once:  /api/telegram/webhook/<TELEGRAM_WEBHOOK_SECRET>
  3. In the app, Settings -> "Link Telegram" creates a ONE-TIME token
     (short-lived, single-use) and shows the deep link
         https://t.me/<bot>?start=<token>
  4. The bot receives /start <token>; the token is consumed (single-use) and
     telegram_chat_id is stored on that user's doc. Email-based linking was
     REMOVED: knowing someone's email must never grant notification access.
From then on every notify() (signals, TP/SL hits, results, approvals) is
also delivered to that chat.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..config import settings
from ..db.store import get_store


def send_telegram(user_id: str, text: str) -> bool:
    """Best-effort delivery. Returns True only when actually sent."""
    from ..core.plain_text import to_plain
    text = to_plain(text)
    token = settings.telegram_bot_token
    if not token:
        return False
    store = get_store()
    u = store.get("users", user_id) or {}
    # ONLY the user's own linked chat. The old default-chat fallback made every
    # event on ANY account's signal copy ring the owner's phone TWICE (each
    # setup exists once per user by design). A user without their own linked
    # Telegram simply gets in-app notifications.
    chat_id = u.get("telegram_chat_id")
    if not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def linked(user_id: str) -> bool:
    try:
        u = get_store().get("users", user_id) or {}
        return bool(u.get("telegram_chat_id"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# P13: one-time link tokens (short-lived, single-use)
# ---------------------------------------------------------------------------
LINK_TOKEN_TTL_S = 15 * 60

_bot_username_cache: Optional[str] = None


def bot_username() -> Optional[str]:
    """The ONE official bot's @username.

    Env override (TELEGRAM_BOT_USERNAME) wins; otherwise ask Telegram itself
    once (getMe) and cache - the app must never depend on a hand-set env var
    to build the deep link (that silent gap once left users with no button).
    """
    global _bot_username_cache
    if settings.telegram_bot_username:
        return settings.telegram_bot_username
    if _bot_username_cache is not None:
        return _bot_username_cache
    if not settings.telegram_bot_token:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=10)
        _bot_username_cache = (
            (r.json().get("result") or {}).get("username") or None)
    except Exception:
        _bot_username_cache = None
    return _bot_username_cache


def create_link_token(user_id: str, ttl_s: int = LINK_TOKEN_TTL_S) -> Optional[dict]:
    """Mint a single-use link token; previous unused tokens are invalidated."""
    import secrets as _secrets
    from datetime import datetime, timezone
    if not user_id:
        return None
    try:
        store = get_store()
        now = datetime.now(timezone.utc)
        for t in store.list("tg_link_tokens", filters={"userId": user_id,
                                                       "used": False}, limit=20):
            store.update("tg_link_tokens", t["id"], {"used": True,
                                                     "note": "superseded"})
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no ambiguous 0/O/1/I
        token = "".join(_secrets.choice(alphabet) for _ in range(8))
        doc = store.create("tg_link_tokens", {
            "userId": user_id,
            "token": token,
            "used": False,
            "expiresAt": datetime.fromtimestamp(now.timestamp() + ttl_s,
                                                timezone.utc).isoformat(),
            "createdAt": now.isoformat(),
        })
        uname = bot_username()
        deep_link = (f"https://t.me/{uname}"
                     f"?start={token}") if uname else None
        return {"token": token, "expires_at": doc["expiresAt"],
                "deep_link": deep_link,
                "instructions": (f"Open the bot and send: /start {token}"
                                 if not deep_link else
                                 "Tap the link (or send the /start command to "
                                 "the bot) within 15 minutes.")}
    except Exception:
        return None


def _consume_link_token(token: str) -> Optional[str]:
    """Single-use + TTL check. Returns userId or None (honest, no leaks)."""
    from datetime import datetime, timezone
    try:
        store = get_store()
        docs = store.list("tg_link_tokens", filters={"token": str(token).upper()},
                          limit=1)
        if not docs:
            return None
        t = docs[0]
        if t.get("used"):
            return None
        try:
            exp = datetime.fromisoformat(str(t["expiresAt"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                store.update("tg_link_tokens", t["id"],
                             {"used": True, "note": "expired"})
                return None
        except Exception:
            return None
        store.update("tg_link_tokens", t["id"],
                     {"used": True, "usedAt": datetime.now(timezone.utc).isoformat()})
        return t.get("userId")
    except Exception:
        return None


def handle_webhook(payload: dict) -> dict:
    """Telegram update -> /start <email> links the chat to that account."""
    msg = payload.get("message") or payload.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    store = get_store()
    if not settings.telegram_bot_token:
        return {"ok": True}

    def reply(t: str):
        try:
            requests.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": t},
                timeout=10,
            )
        except Exception:
            pass

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    if cmd == "/start":
        token = parts[1].strip().upper() if len(parts) > 1 else ""
        uid = _consume_link_token(token) if token else None
        if not uid:
            reply("Welcome to ForexMind AI 🤖\n\n"
                  "To link this chat, open the app: Settings → Telegram → "
                  "Get link code, then send me:\n/start CODE\n"
                  "(Codes are single-use and expire after 15 minutes.)")
            return {"ok": True}
        u = store.get("users", uid) or {}
        store.update("users", uid, {"telegram_chat_id": chat_id})
        try:
            from ..core.permissions import audit
            audit(uid, "telegram.linked", uid,
                  {"telegram_linked": bool(u.get("telegram_chat_id"))},
                  {"telegram_chat_id": chat_id}, "one-time token consumed")
        except Exception:
            pass
        reply(f"✅ Linked to ForexMind AI ({u.get('email')}).\n\n"
              "You'll now receive: new signals, TP/SL hits, trade results and "
              "strategy approvals. Happy trading! 📈")
    elif cmd == "/status":
        u = store.list("users", filters={"telegram_chat_id": chat_id}, limit=1)
        reply("✅ Linked to ForexMind AI." if u else
              "Not linked yet. Open the app: Settings → Telegram → Get link "
              "code, then send me /start CODE")
    else:
        reply("ForexMind AI bot 🤖\n/start <email> - link your account\n/status - check link")
    return {"ok": True}
