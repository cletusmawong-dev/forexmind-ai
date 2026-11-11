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


_PUBLIC_FIELDS = (
    "id", "label", "broker", "server", "login", "mode",
    "is_active", "is_default", "trading_enabled", "connected",
    "currency", "createdAt",
    "symbol_suffix", "strategy_ids", "risk_per_trade_usd",
    "max_daily_loss_usd", "kill_switch", "bridge_slot", "bridge_url", "magic",
    "balance", "equity",
)

# Product ceiling. Not a soft limit.
MAX_ACCOUNTS = 5


def _public(d: dict) -> dict:
    out = {k: d.get(k) for k in _PUBLIC_FIELDS}
    out["strategy_ids"] = list(out.get("strategy_ids") or [])
    out["symbol_suffix"] = out.get("symbol_suffix") or ""
    out["kill_switch"] = bool(out.get("kill_switch"))
    return out


def _strategy_ids(body: dict) -> list:
    """Pick-list. Omitted means none selected. Unknown ids are refused."""
    if "strategy_ids" not in body or body.get("strategy_ids") is None:
        return []
    raw = body.get("strategy_ids")
    if not isinstance(raw, list):
        raise HTTPException(422, "strategy_ids must be a list")
    from ..strategies import OWNER_STRATEGY_IDS
    ids = []
    for item in raw:
        sid = str(item).strip()
        if sid not in OWNER_STRATEGY_IDS:
            raise HTTPException(422, f"unknown strategy '{sid}'")
        if sid not in ids:
            ids.append(sid)
    return ids


def _optional_usd(body: dict, key: str) -> Optional[float]:
    if key not in body or body.get(key) in (None, ""):
        return None
    try:
        value = float(body[key])
    except (TypeError, ValueError):
        raise HTTPException(422, f"{key} must be a number")
    if value <= 0:
        raise HTTPException(422, f"{key} must be a positive number of USD")
    return value


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
    try:
        rows = get_store().list("trading_accounts", filters={"userId": user_id},
                                limit=50)
    except Exception:
        # table not provisioned yet (Supabase DDL needs the owner's SQL editor)
        return {"accounts": [], "note": _PROVISION_NOTE}
    return {"accounts": [_public(d) for d in rows]}


_PROVISION_NOTE = ("accounts table not provisioned yet - run "
                   "backend/scripts/supabase_trading_accounts.sql in the "
                   "Supabase SQL editor")


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
    try:
        existing = store.list("trading_accounts", filters={"userId": user_id}, limit=50)
    except Exception:
        raise HTTPException(503, _PROVISION_NOTE)
    if len(existing) >= MAX_ACCOUNTS:
        raise HTTPException(409, "5 accounts is the hard ceiling")
    doc = store.create("trading_accounts", {
        "userId": user_id,
        "label": label,
        "broker": str(body.get("broker") or "").strip(),
        "server": str(body.get("server") or "").strip(),
        "login": str(body.get("login") or "").strip(),
        "mode": mode,
        "is_active": not existing,          # first account becomes the view
        "is_default": not existing,         # and the default view
        "trading_enabled": False,           # born safe: enable explicitly
        "connected": False,
        "currency": str(body.get("currency") or "USD"),
        "symbol_suffix": str(body.get("symbol_suffix") or "").strip(),
        "strategy_ids": _strategy_ids(body),   # none selected until chosen
        "risk_per_trade_usd": _optional_usd(body, "risk_per_trade_usd"),
        "max_daily_loss_usd": _optional_usd(body, "max_daily_loss_usd"),
        "kill_switch": False,
        "bridge_slot": None,
        "bridge_url": "",
        "magic": None,
        "createdAt": _now(),
    })
    if doc is None:
        raise HTTPException(503, _PROVISION_NOTE)
    return {"account": _public(doc)}


@router.patch("/accounts/{account_id}")
def update_account(account_id: str, body: dict, user_id: str = Depends(get_user_id)):
    """Owner fields for a slot: where its bridge is, what it trades, its risk.

    Does not turn trading on. That stays on POST /accounts/{id}/trading.
    """
    doc = _mine(user_id, account_id)
    if not doc:
        raise HTTPException(404, "Account not found")
    patch = {}
    if "label" in body:
        label = str(body.get("label") or "").strip()
        if not label:
            raise HTTPException(422, "label is required")
        patch["label"] = label
    if "symbol_suffix" in body:
        patch["symbol_suffix"] = str(body.get("symbol_suffix") or "").strip()
    if "strategy_ids" in body:
        patch["strategy_ids"] = _strategy_ids(body)
    if "risk_per_trade_usd" in body:
        patch["risk_per_trade_usd"] = _optional_usd(body, "risk_per_trade_usd")
    if "max_daily_loss_usd" in body:
        patch["max_daily_loss_usd"] = _optional_usd(body, "max_daily_loss_usd")
    if "bridge_url" in body or "bridge_slot" in body:
        if user_id != settings.owner_user_id:
            raise HTTPException(403, "VPS bridge routing is reserved for the owner")
        if "bridge_url" in body:
            patch["bridge_url"] = str(body.get("bridge_url") or "").strip()
        if "bridge_slot" in body and body.get("bridge_slot") not in (None, ""):
            try:
                slot = int(body.get("bridge_slot"))
            except (TypeError, ValueError):
                raise HTTPException(422, "bridge_slot must be 1-5")
            if slot < 1 or slot > 5:
                raise HTTPException(422, "bridge_slot must be 1-5")
            patch["bridge_slot"] = slot
    if not patch:
        raise HTTPException(422, "no fields to update")
    updated = get_store().update("trading_accounts", account_id, patch)
    return {"account": _public(updated or {})}


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
