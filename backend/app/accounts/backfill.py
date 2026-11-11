"""Slot 1: the login that already trades on port 8700.

Creates one owner row when none exist, and only when the VPS bridge is
configured. Execution still uses MT5_BRIDGE_URL. The row is born with
trading on and the strategies that are registered today, so the phase-2
account gate does not turn the live login off.

A missing table is ignored. The owner runs the SQL, then restarts the API
container (not the bridge). The next boot inserts the row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..config import settings
from ..db.store import get_store

MAGIC_SLOT1 = 20260914


def backfill_slot1(user_id: Optional[str] = None) -> Optional[dict]:
    if settings.execution_mode != "mt5_bridge" or not settings.bridge_url:
        return None
    user_id = user_id or settings.owner_user_id
    store = get_store()
    try:
        rows = store.list("trading_accounts", filters={"userId": user_id}, limit=10)
    except Exception:
        return None
    if rows:
        return None
    from ..strategies import OWNER_STRATEGY_IDS
    doc = {
        "userId": user_id,
        "label": "Primary MT5",
        "broker": "",
        "server": "",
        "login": "",
        "mode": "vps_bridge",
        "is_active": True,
        "is_default": True,
        "trading_enabled": True,
        "connected": False,
        "currency": "USD",
        "symbol_suffix": "",
        "strategy_ids": sorted(OWNER_STRATEGY_IDS),
        "risk_per_trade_usd": None,
        "max_daily_loss_usd": None,
        "kill_switch": False,
        "bridge_slot": 1,
        "bridge_url": "",
        "magic": MAGIC_SLOT1,
        "credential_ref": "accounts/1.env",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from ..execution.mt5 import bridge_get
        acct = bridge_get("/account") or {}
        login = acct.get("login")
        if login:
            doc["login"] = str(login)
            doc["server"] = str(acct.get("server") or "")
            doc["currency"] = str(acct.get("currency") or "USD")
            doc["label"] = f"MT5 {login}"
            doc["connected"] = True
    except Exception:
        pass
    try:
        return store.create("trading_accounts", doc)
    except Exception:
        return None
