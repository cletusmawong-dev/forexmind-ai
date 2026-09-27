"""Permissions, trading-authorization states and the structured audit log
(Master Upgrade Phase 11/12/17 - Stage 1 safety foundations).

Three primitives, all server-side, all persisted:

ROLE        users.role: "user" (default) | "admin".
            settings.owner_user_id is implicitly admin (env-defined, so the
            bootstrap works even before any Firestore write).

STATUS      users.status: "active" (default) | "suspended".
            Suspended users fail authentication for every authenticated route.

TRADING     users.trading_permission - the auto-trading state machine:
  LOCKED    default for EVERY new user (signals only).
  SETUP     admin pre-approval granted; awaiting verified MT5 + configured risk.
  ENABLED   everything verified - the executor may place orders for this user.
The executor NEVER trusts frontend state: it resolves the permission from the
database on every order (this module).

Every admin action and every permission transition writes an audit_log entry:
{actor, action, target_user, prev, new, reason, at} - append-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from ..config import settings
from ..db.store import get_store

PERMISSIONS = ("locked", "setup", "enabled")


# ---------------------------------------------------------------------------
def is_admin(user_id: str) -> bool:
    if user_id == settings.owner_user_id:
        return True
    u = get_store().get("users", user_id) or {}
    return u.get("role") == "admin"


def trading_allowed(user_id: str) -> Tuple[bool, str]:
    """The executor's gate: may the engine place orders for this user?

    Owner fast-path keeps the production single-account flow unchanged.
    Everyone else needs trading_permission == "enabled" in the DATABASE."""
    if user_id == settings.owner_user_id:
        return True, "owner"
    u = get_store().get("users", user_id) or {}
    if u.get("status") == "suspended":
        return False, "account suspended"
    if u.get("trading_permission") == "enabled":
        return True, "authorized"
    return False, f"auto trading {u.get('trading_permission') or 'locked'}"


def authorization_requirements(user_id: str) -> dict:
    """What is missing before auto-trading can be enabled (admin UI + tests)."""
    store = get_store()
    u = store.get("users", user_id) or {}
    goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    goals = goals[0] if goals else {}
    risk = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
    mode = (goals.get("execution_mode") or "").lower()
    if mode in ("vps", "mt5_bridge"):
        service_ready = bool(settings.bridge_url)      # VPS bridge must exist
    elif mode == "manual":
        service_ready = True                            # PC connector pull mode
    else:
        service_ready = False                           # no mode chosen yet
    return {
        "admin_approved": u.get("trading_permission") in ("setup", "enabled"),
        "mt5_verified": bool(goals.get("mt5_verified")),
        "account_setup": bool(goals.get("mt5_account") or mode),
        "risk_configured": bool(risk),
        "execution_service_ready": service_ready,
    }


def can_enable(user_id: str) -> Tuple[bool, str]:
    req = authorization_requirements(user_id)
    missing = [k for k, v in req.items() if not v]
    return (not missing), (", ".join(missing) if missing else "all requirements met")


# ---------------------------------------------------------------------------
def audit(actor: str, action: str, target_user: Optional[str] = None,
          prev: Optional[dict] = None, new: Optional[dict] = None,
          reason: str = "") -> None:
    """Append-only structured audit entry (Phase 17). Never raises - an audit
    failure must not break the primary action, but it DOES log loudly."""
    try:
        get_store().create("audit_log", {
            "actor": actor, "action": action, "target_user": target_user,
            "prev": prev or {}, "new": new or {}, "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # pragma: no cover - visibility over silence
        print(f"[audit] FAILED to record {action}: {type(exc).__name__}: {exc}",
              flush=True)
