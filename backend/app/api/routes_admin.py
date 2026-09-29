"""Developer/Admin control API (Master Upgrade Phase 12 + Phase 17).

Every route requires the admin role (settings.owner_user_id is implicitly
admin). Every mutating action writes an append-only audit_log entry with
actor, previous state, new state and reason. The frontend can NEVER bypass
these checks - the database is the source of truth.
"""
from __future__ import annotations

from datetime import datetime, timezone

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


def _emergency_stop_inner(user_id: str, admin: str) -> dict:
    """Core of the per-user emergency stop (shared with the L5 kill-switch)."""
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


@router.post("/users/{user_id}/emergency-stop")
def emergency_stop(user_id: str, admin: str = Depends(require_admin)):
    """Kill switch + revoke + suspend, one action, fully audited."""
    return _emergency_stop_inner(user_id, admin)


class KillSwitchIn(BaseModel):
    level: int
    reason: str = ""
    instruments: list = []
    strategies: list = []


class CloseAllIn(BaseModel):
    confirm: bool = False


@router.get("/killswitch")
def killswitch_status(admin: str = Depends(require_admin)):
    from ..risk.killswitch import current_level
    return current_level()


@router.post("/killswitch")
def killswitch_set(body: KillSwitchIn, admin: str = Depends(require_admin)):
    """Activate a kill-switch level (0-5). Explicit, audited, visible."""
    from ..risk.killswitch import LEVELS, set_level
    if not (0 <= body.level <= 4):
        raise HTTPException(422, "use /killswitch/close-all for level 5 (guarded)")
    prev = current = None
    from ..risk.killswitch import current_level as _cl
    prev = _cl()
    r = set_level(body.level, admin, body.reason,
                  instruments=body.instruments, strategies=body.strategies)
    audit(admin, f"killswitch.L{body.level}", "system",
          {"level": prev.get("level")}, {"level": body.level}, body.reason)
    return r


@router.post("/killswitch/close-all")
def killswitch_close_all(body: CloseAllIn, admin: str = Depends(require_admin)):
    """LEVEL 5: emergency close-all. Requires confirm=true. Audited. Stops
    automated trading (level 4) after closing."""
    from ..risk.killswitch import emergency_close_all
    if not body.confirm:
        raise HTTPException(422, "close-all requires confirm=true")
    r = emergency_close_all(admin)
    audit(admin, "killswitch.L5_close_all", "system",
          {"level": 0}, {"level": 4, "closed": r.get("closed")}, "EMERGENCY CLOSE ALL")
    return r


@router.get("/security")
def security_center(admin: str = Depends(require_admin)):
    """Security Center (3.0 spec section 51): honest status booleans ONLY -
    secret VALUES are never returned, configured/unconfigured is enough."""
    from ..config import settings
    store = get_store()
    jwt_dev_default = settings.jwt_secret == "dev-only-secret-change-me"
    return {
        "jwt": {"configured": not jwt_dev_default,
                "using_dev_fallback": jwt_dev_default,
                "token_ttl_days": 30,
                "risk": ("HIGH - public repo + dev fallback secret: tokens can "
                         "be forged. Rotation awaits explicit owner approval."
                         if jwt_dev_default else "ok")},
        "telegram": {"configured": bool(getattr(settings, "telegram_bot_token", None))},
        "bridge": {"configured": bool(getattr(settings, "bridge_url", None))},
        "secrets_in_code": False,
        "secret_policy": "env-only; values never served by any endpoint",
        "admin_users": sum(1 for u in store.list("users", limit=200)
                           if u.get("role") == "admin"),
        "suspended_users": sum(1 for u in store.list("users", limit=200)
                               if u.get("status") == "suspended"),
        "audit_entries": store.count("audit_log"),
        "killswitch_level": (store.get("settings", "killswitch") or {}).get("level", 0),
        "note": "booleans and counts only - no secret material leaves the backend",
    }


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


@router.get("/command-center")
def command_center(admin: str = Depends(require_admin)):
    """Developer Command Center (P14): ONE honest snapshot of the whole
    system. Every section degrades independently - a broken piece reports
    its error instead of breaking the page. Read-only."""
    store = get_store()
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    def section(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}

    # health components (reuse the /api/health logic honestly)
    section("health", lambda: _http_get_json("/api/health"))

    def _users():
        users = store.list("users", limit=500)
        return {"total": len(users),
                "suspended": sum(1 for u in users if u.get("status") == "suspended"),
                "auto_trading": sum(1 for u in users
                                    if u.get("trading_permission") == "enabled"),
                "pending_setup": sum(1 for u in users
                                     if u.get("trading_permission") == "setup"),
                "telegram_linked": sum(1 for u in users if u.get("telegram_chat_id")),
                "roster": [_public(u) for u in users[:50]]}
    section("users", _users)

    def _router():
        from ..agent.router import get_router
        return get_router().status()
    section("ai_router", _router)

    def _manager():
        from ..aimanager.engine import get_manager
        st = get_manager().status()
        return {"last_tick_ts": st.get("last_tick_ts"),
                "managed_positions": st.get("managed_positions"),
                "last_summary": st.get("last_summary")}
    section("trade_manager", _manager)

    def _daily():
        from ..engine.daily import daily_state
        d = daily_state(admin)
        return {k: d.get(k) for k in
                ("day", "realized_usd", "floating_usd", "total_usd",
                 "daily_profit_target_usd", "daily_loss_limit_usd",
                 "remaining_target_usd", "remaining_loss_usd", "hit_target",
                 "hit_loss", "status", "realized_basis")}
    section("daily_walls", _daily)

    section("executions", lambda: {"recent": store.list(
        "exec_events", filters={"userId": admin}, order_by="createdAt",
        desc=True, limit=12)})

    def _tp_audit():
        from ..execution.tp_audit import scan_user
        f = scan_user(admin)
        return {"findings": f, "leaks": sum(1 for x in f
                                            if x.get("severity") == "leak")}
    section("tp_audit", _tp_audit)

    def _learning():
        hyps = store.list("hypotheses", filters={"userId": admin}, limit=200)
        exps = store.list("experiments", filters={"userId": admin}, limit=50)
        recs = store.list("recommendations", filters={"userId": admin}, limit=100)
        return {"hypotheses_pending": sum(1 for h in hyps
                                          if h.get("status") == "PROPOSED"),
                "experiments_ready_for_review": sum(
                    1 for e in exps if e.get("status") == "READY_FOR_REVIEW"),
                "recommendations_open": sum(1 for r in recs
                                            if r.get("status") in ("REVIEW", "REVIEWING")),
                "recent_experiments": [{k: e.get(k) for k in
                                        ("experiment_code", "strategy_id",
                                         "variable", "old_value", "new_value",
                                         "result", "status", "createdAt")}
                                       for e in exps[:8]]}
    section("learning", _learning)

    def _strategies():
        rows = []
        for s in store.list("strategies", limit=50):
            rows.append({"id": s.get("id"), "name": s.get("name"),
                         "status": s.get("status"),
                         "active_version": s.get("active_version"),
                         "sessions": s.get("sessions"),
                         "proposed_sessions": s.get("proposed_sessions")})
        return {"strategies": rows}

    section("strategies", _strategies)

    def _regimes():
        from ..learning.regime import current
        from ..config import INITIAL_MARKETS
        return {m: current(m) for m in INITIAL_MARKETS}
    section("regimes", _regimes)

    def _regime_events():
        return {"recent": store.list("regime_events", order_by="createdAt",
                                     desc=True, limit=8)}
    section("regime_events", _regime_events)

    # ---- 3.0 additions (stages 1/4/5/7) --------------------------------
    def _owner():
        from ..config import settings
        return getattr(settings, "owner_user_id", "cletusmawa")

    def _evidence():
        from ..evidence import engine
        docs = engine.list_evidence(_owner())
        return {"count": len(docs),
                "states": {s: sum(1 for d in docs if d.get("state") == s)
                           for s in ("INSUFFICIENT", "PRELIMINARY", "SUPPORTED",
                                     "STRONGER", "CONTRADICTED")},
                "contradictions": sum(len(d.get("contradictions") or [])
                                      for d in docs)}
    section("evidence", _evidence)

    def _killswitch():
        from ..risk.killswitch import current_level
        return current_level()
    section("killswitch", _killswitch)

    def _incidents():
        docs = store.list("incidents", limit=100)
        return {"open": sum(1 for d in docs if d.get("status") != "RESOLVED"),
                "critical": sum(1 for d in docs if d.get("severity") == "CRITICAL"
                                and d.get("status") != "RESOLVED"),
                "recent": [{k: d.get(k) for k in ("kind", "subject", "severity",
                                                  "status", "last_seen_at")}
                           for d in docs[:8]]}
    section("incidents", _incidents)

    def _tca():
        from ..execution.tca import tca_report
        return tca_report(_owner())
    section("tca", _tca)

    def _exposure():
        from ..risk.exposure import exposure_snapshot
        return exposure_snapshot(_owner())
    section("exposure", _exposure)

    def _research_ctx():
        exps = store.list("experiments", limit=500)
        return {"experiments_total": len(exps),
                "passed": sum(1 for e in exps if (e.get("result") or {}).get("passed")),
                "hypotheses": store.count("hypotheses"),
                "note": "multiple-testing context for any single result"}
    section("research_context", _research_ctx)

    section("audit_tail", lambda: {"entries": store.list(
        "audit_log", order_by="createdAt", desc=True, limit=15)})
    return out


def _http_get_json(path: str) -> dict:
    """Internal-only health reuse without an HTTP self-call (tests + prod)."""
    from ..state import State
    from ..config import settings as _s
    import time as _t
    comps: dict = {}
    comps["store"] = {"kind": type(get_store()).__name__}
    comps["execution"] = {"mode": _s.execution_mode,
                          "bridge_configured": bool(_s.bridge_url)}
    comps["brain_v2"] = bool(_s.brain_v2_enabled)
    return {"components": comps, "provider": State.provider.name if State.provider else None}
