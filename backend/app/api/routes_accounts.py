"""Multi-account trading (final master build section 18-19).

A user may register multiple trading accounts (e.g. Exness Demo, Exness
Live, another broker). Each account is an ISOLATED execution context:

  - ownership: every account doc carries userId; backend auth decides
    identity - a frontend-supplied user id is never trusted
  - selection: exactly one account is the user's ACTIVE account at a time;
    setting another active account deactivates the previous one
  - default: one account may be flagged default (survives as the fallback)
  - execution: execute_signal resolves the user's active account, stamps
    account_id on the signal, and refuses to run when that account is
    disabled/disconnected - so a signal can never land in an account the
    user did not select (section 19)
  - the shared VPS bridge remains OWNER-ONLY (multi-user privacy,
    2026-10-01): non-owner accounts execute via their OWN PC connector
    (manual mode), never via someone else's broker login

This module is registry + selection + isolation gates. It does NOT create
a second trading engine: the existing execution transports are reused.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..db.store import get_store
from .deps import get_user_id

router = APIRouter(tags=["accounts"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mine(user_id: str, account_id: str) -> Optional[dict]:
    """Ownership-enforced fetch: the doc must exist AND belong to caller."""
    doc = get_store().get("trading_accounts", account_id)
    if not doc or doc.get("userId") != user_id:
        return None
    return doc


def _public(d: dict) -> dict:
    return {k: d.get(k) for k in
            ("id", "label", "broker", "server", "login", "mode",
             "is_active", "is_default", "trading_enabled", "connected",
             "currency", "createdAt")}


def active_account(user_id: str) -> Optional[dict]:
    """The user's currently selected trading account (is_active=True).
    Falls back to the default account, then to None (advisory-only)."""
    store = get_store()
    rows = [d for d in store.list("trading_accounts",
                                  filters={"userId": user_id}, limit=50)
            if d.get("is_active")]
    if rows:
        return rows[0]
    rows = [d for d in store.list("trading_accounts",
                                  filters={"userId": user_id}, limit=50)
            if d.get("is_default")]
    return rows[0] if rows else None


# ----------------------------------------------------------------- routes
@router.get("/accounts")
def list_accounts(user_id: str = Depends(get_user_id)):
    """Only the caller's accounts - never anyone else's (section 17)."""
    rows = get_store().list("trading_accounts", filters={"userId": user_id},
                            limit=50)
    return {"accounts": [_public(d) for d in rows]}


@router.post("/accounts")
def add_account(body: dict, user_id: str = Depends(get_user_id)):
    label = str(body.get("label") or "").strip()
    if not label:
        raise HTTPException(422, "label is required")
    mode = body.get("mode") or "signals_only"
    if mode not in ("signals_only", "own_pc", "vps_bridge"):
        raise HTTPException(422, "mode must be signals_only | own_pc | vps_bridge")
    if mode == "vps_bridge" and user_id != settings.owner_user_id:
        # multi-user privacy rule: the shared VPS bridge is the owner's
        raise HTTPException(403, "VPS bridge execution is reserved for the owner")
    store = get_store()
    existing = store.list("trading_accounts", filters={"userId": user_id}, limit=50)
    doc = store.create("trading_accounts", {
        "userId": user_id,
        "label": label,
        "broker": str(body.get("broker") or "").strip(),
        "server": str(body.get("server") or "").strip(),
        "login": str(body.get("login") or "").strip(),
        "mode": mode,
        "is_active": not existing,          # first account becomes active
        "is_default": not existing,         # and the default
        "trading_enabled": False,           # born safe: enable explicitly
        "connected": False,
        "currency": str(body.get("currency") or "USD"),
        "createdAt": _now(),
    })
    return {"account": _public(doc)}


@router.post("/accounts/{account_id}/select")
def select_account(account_id: str, user_id: str = Depends(get_user_id)):
    """Switch the ENTIRE app context to this account (section 43)."""
    doc = _mine(user_id, account_id)
    if not doc:
        raise HTTPException(404, "Account not found")
    store = get_store()
    for d in store.list("trading_accounts", filters={"userId": user_id}, limit=50):
        if d.get("is_active"):
            store.update("trading_accounts", d["id"], {"is_active": False})
    store.update("trading_accounts", account_id, {"is_active": True})
    return {"account": _public(store.get("trading_accounts", account_id))}


@router.post("/accounts/{account_id}/default")
def set_default(account_id: str, user_id: str = Depends(get_user_id)):
    doc = _mine(user_id, account_id)
    if not doc:
        raise HTTPException(404, "Account not found")
    store = get_store()
    for d in store.list("trading_accounts", filters={"userId": user_id}, limit=50):
        if d.get("is_default"):
            store.update("trading_accounts", d["id"], {"is_default": False})
    store.update("trading_accounts", account_id, {"is_default": True})
    return {"account": _public(store.get("trading_accounts", account_id))}


@router.post("/accounts/{account_id}/trading")
def set_trading(account_id: str, body: dict, user_id: str = Depends(get_user_id)):
    """Enable/disable trading PER ACCOUNT (LOCKED -> SETUP -> ENABLED)."""
    doc = _mine(user_id, account_id)
    if not doc:
        raise HTTPException(404, "Account not found")
    want = bool(body.get("enabled"))
    if want:
        from ..core.permissions import trading_allowed
        allowed, why = trading_allowed(user_id)
        if not allowed:
            raise HTTPException(403, f"Trading not authorized: {why}")
    doc = get_store().update("trading_accounts", account_id,
                             {"trading_enabled": want})
    return {"account": _public(doc or {})}


@router.delete("/accounts/{account_id}")
def disconnect_account(account_id: str, user_id: str = Depends(get_user_id)):
    """Disconnect = stop all execution for this account and remove it.
    History/signals are untouched; only the account link is removed."""
    doc = _mine(user_id, account_id)
    if not doc:
        raise HTTPException(404, "Account not found")
    store = get_store()
    store.delete("trading_accounts", account_id)
    was_active = bool(doc.get("is_active"))
    if was_active:
        rest = store.list("trading_accounts", filters={"userId": user_id}, limit=50)
        if rest:
            store.update("trading_accounts", rest[0]["id"], {"is_active": True})
    return {"ok": True}
