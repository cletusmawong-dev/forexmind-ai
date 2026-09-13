"""Telegram delivery + account linking (phone notifications).

Flow:
  1. Owner creates a bot with @BotFather, pastes the token as TELEGRAM_BOT_TOKEN.
  2. Webhook is registered once:  /api/telegram/webhook/<TELEGRAM_WEBHOOK_SECRET>
  3. In the app, Settings shows the exact message to send the bot:
        /start <account email>
  4. The webhook stores telegram_chat_id on the user doc. From then on every
     notify() (new signal, TP/SL hit, trade completed, approvals) is also
     delivered to that chat.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..config import settings
from ..db.store import get_store


def send_telegram(user_id: str, text: str) -> bool:
    """Best-effort delivery. Returns True only when actually sent."""
    token = settings.telegram_bot_token
    if not token:
        return False
    store = get_store()
    u = store.get("users", user_id) or {}
    chat_id = u.get("telegram_chat_id") or settings.telegram_default_chat_id
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
        email = parts[1].lower() if len(parts) > 1 else ""
        u = store.list("users", filters={"email": email}, limit=1) if email else []
        if not u:
            reply("Welcome to ForexMind AI 🤖\n\nSend:  /start your-account-email\n"
                  "(use the email you log into the app with) and I'll deliver "
                  "signals, TP/SL hits and approvals right here.")
            return {"ok": True}
        store.update("users", u[0]["id"], {"telegram_chat_id": chat_id})
        reply(f"✅ Linked to ForexMind AI ({u[0]['email']}).\n\n"
              "You'll now receive: new signals, TP/SL hits, trade results and "
              "strategy approvals. Happy trading! 📈")
    elif cmd == "/status":
        u = store.list("users", filters={"telegram_chat_id": chat_id}, limit=1)
        reply("✅ Linked to ForexMind AI." if u else "Not linked yet. Send:  /start your-account-email")
    else:
        reply("ForexMind AI bot 🤖\n/start <email> — link your account\n/status — check link")
    return {"ok": True}
