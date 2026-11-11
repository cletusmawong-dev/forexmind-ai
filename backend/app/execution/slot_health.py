"""Poll bridge slots 2-5. Never sends an order. Never opens port 8700
unless an account row explicitly points at it (slot 1 is not polled here).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..config import settings
from ..db.store import get_store


def _endpoint(account: dict) -> str:
    return str(account.get("bridge_url") or "").rstrip("/")


def poll_extra_slots() -> int:
    """Update connected/equity for slots 2-5. Returns how many were checked."""
    if not settings.bridge_token:
        return 0
    try:
        rows = get_store().list("trading_accounts", limit=50)
    except Exception:
        return 0
    checked = 0
    for account in rows:
        try:
            slot = int(account.get("bridge_slot") or 0)
        except (TypeError, ValueError):
            continue
        if slot < 2 or slot > 5:
            continue
        url = _endpoint(account)
        if not url:
            continue
        checked += 1
        patch = {"last_health_at": datetime.now(timezone.utc).isoformat()}
        try:
            response = requests.get(
                f"{url}/health",
                headers={"X-Bridge-Token": settings.bridge_token},
                timeout=4,
            )
            body = response.json() if response.status_code == 200 else {}
        except Exception:
            body = {}
        acct = (body or {}).get("account") or {}
        reported = str(acct.get("login") or "")
        expected = str(account.get("login") or "")
        match = bool(reported) and (not expected or reported == expected)
        patch["connected"] = bool(body.get("ok")) and match
        if acct.get("balance") is not None:
            patch["balance"] = acct.get("balance")
        if acct.get("equity") is not None:
            patch["equity"] = acct.get("equity")
        if reported and not expected:
            patch["login"] = reported
        try:
            get_store().update("trading_accounts", account["id"], patch)
        except Exception:
            continue
    return checked
