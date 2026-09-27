"""Developer/Admin control API (Master Upgrade Phase 12 + Phase 17).

Every route requires the admin role (settings.owner_user_id is implicitly
admin). Every mutating action writes an append-only audit_log entry with
actor, previous state, new state and reason. The frontend can NEVER bypass
these checks - the database is the source of truth.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.permissions import (audit, authorization_requirements, can_enable,
                                is_admin, PERMISSIONS)
from ..db.store import get_store
from .deps import get_user_id

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(user_id: str = Depends(get_user_id)) -> str:
    if not is_admin(user_id):
        raise HTTPException(403, "Admin only")
    return user_id


def _public(u: dict) -> dict:
    return {"id": u["id"], "email": u.get("email"), "display_name": u.get("display_name", ""),
            "role": u.get("role", "user"), "status": u.get("status", "active"),
            "trading_permission": u.get("trading_permission", "locked"),
            "telegram_linked": bool(u.get("telegram_chat_id")),
            "createdAt": u.get("createdAt")}


class StatusIn(BaseModel):
    status: str            # "active" | "suspended"
    reason: str = ""


class PermissionIn(BaseModel):
    permission: str        # "locked" | "setup" | "enabled"
    reason: str = ""


@router.get("/users")
def list_users(admin: str = Depends(require_admin)):
    store = get_store()
    users = [_public(u) for u in store.list("users", limit=500)]
    return {"users": users, "count": len(users),
            "pending_approval": [u for u in users if u["trading_permission"] == "setup"],
            "auto_trading": [u for u in users if u["trading_permission"] == "enabled"],
            "suspended": [u for u in users if u["status"] == "suspended"]}


@router.get("/users/{user_id}/requirements")
def user_requirements(user_id: str, admin: str = Depends(require_admin)):
    store = get_store()
    if not store.get("users", user_id):
        raise HTTPException(404, "User not found")
    return {"user_id": user_id, "requirements": authorization_requirements(user_id),
            "can_enable": can_enable(user_id)}


@router.post("/users/{user_id}/status")
def set_status(user_id: str, body: StatusIn, admin: str = Depends(require_admin)):
    store = get_store()
    u = store.get("users", user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if body.status not in ("active", "suspended"):
        raise HTTPException(422, "status must be 'active' or 'suspended'")
    if user_id == admin:
        raise HTTPException(422, "Admins cannot suspend themselves")
    prev = {"status": u.get("status", "active")}
    if prev["status"] == body.status:
        return {"user": _public(u), "changed": False}
    store.update("users", user_id, {"status": body.status})
    if body.status == "suspended":   # suspension kills live authority too
        if u.get("trading_permission") not in (None, "locked"):
            store.update("users", user_id, {"trading_permission": "locked"})
        goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
        if goals:
            store.update("agent_goals", goals[0]["id"], {"execution_enabled": False})
    audit(admin, f"user.{body.status}", user_id, prev, {"status": body.status}, body.reason)
    return {"user": _public(store.get("users", user_id)), "changed": True}


@router.post("/users/{user_id}/trading")
def set_trading(user_id: str, body: PermissionIn, admin: str = Depends(require_admin)):
    store = get_store()
    u = store.get("users", user_id)
    if not u:
        raise HTTPException(404, "User not found")
    perm = body.permission.lower().strip()
    if perm not in PERMISSIONS:
        raise HTTPException(422, f"permission must be one of {PERMISSIONS}")
    prev = {"trading_permission": u.get("trading_permission", "locked")}
    if perm == "enabled":
        ok, why = can_enable(user_id)
        if not ok:
            raise HTTPException(409, f"Cannot enable auto trading - missing: {why}")
    if (u.get("status") == "suspended") and perm != "locked":
        raise HTTPException(409, "Suspended users stay locked")
    store.update("users", user_id, {"trading_permission": perm})
    if perm == "locked":   # revoke == stop anything live immediately
        goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
        if goals:
            store.update("agent_goals", goals[0]["id"], {"execution_enabled": False})
    audit(admin, "trading_permission." + ("granted" if perm != "locked" else "revoked"),
          user_id, prev, {"trading_permission": perm}, body.reason)
    return {"user": _public(store.get("users", user_id)), "changed": True}


@router.post("/users/{user_id}/emergency-stop")
def emergency_stop(user_id: str, admin: str = Depends(require_admin)):
    """Kill switch + revoke + suspend, one action, fully audited."""
    store = get_store()
    u = store.get("users", user_id)
    if not u:
        raise HTTPException(404, "User not found")
    prev = {"status": u.get("status", "active"),
            "trading_permission": u.get("trading_permission", "locked")}
    store.update("users", user_id, {"status": "suspended", "trading_permission": "locked"})
    goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    if goals:
        store.update("agent_goals", goals[0]["id"], {"execution_enabled": False})
    audit(admin, "user.emergency_stop", user_id, prev,
          {"status": "suspended", "trading_permission": "locked",
           "execution_enabled": False}, "EMERGENCY STOP")
    return {"user": _public(store.get("users", user_id)), "stopped": True}


@router.get("/overview")
def overview(admin: str = Depends(require_admin)):
    """Command-center counts (Phase 14 foundation)."""
    store = get_store()
    users = store.list("users", limit=500)
    return {
        "users": {"total": len(users),
                  "suspended": sum(1 for u in users if u.get("status") == "suspended"),
                  "auto_trading": sum(1 for u in users if u.get("trading_permission") == "enabled"),
                  "pending": sum(1 for u in users if u.get("trading_permission") == "setup")},
        "audit_tail": store.list("audit_log", limit=25),
    }
