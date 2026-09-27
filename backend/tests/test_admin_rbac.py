"""Stage 1 safety foundations: roles, admin API, auto-trading permission
states, structured audit log (Master Upgrade Phase 11/12/17).

Rules under test:
 - every new user: role=user, status=active, trading_permission=locked (SIGNAL ONLY)
 - settings.owner_user_id is implicitly admin; nobody can self-claim admin
 - non-admins get 403 on /api/admin/*
 - suspended users fail EVERY authenticated route
 - the executor resolves trading permission from the DATABASE per order:
   owner fast-path; non-owner needs trading_permission=enabled;
   LOCKED/SETUP users are skipped with SKIPPED_NOT_AUTHORIZED + TG notice
 - enabling auto-trading requires: admin approval (setup) + mt5_verified
   + account setup + risk configured + execution service ready
 - every admin mutation writes an append-only audit_log entry
"""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def world(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)   # restored at teardown
    from app.state import State
    _prev_state_store = State.store
    State.store = store
    yield_reset = lambda: None
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: os.environ.get("_UID", "boss")
    try:
        with TestClient(app) as c:
            yield c, store, monkeypatch
    finally:
        State.store = _prev_state_store   # never leak the test store
        State.ready = False               # next startup re-inits on its own store
        app.dependency_overrides.pop(get_user_id, None)


def _mk_user(store, uid, **kw):
    doc = {"id": uid, "userId": uid, "email": f"{uid}@x.io", "role": "user",
           "status": "active", "trading_permission": "locked"}
    doc.update(kw)
    return store.create("users", doc)


def _as(world, uid):
    c, store, monkeypatch = world
    monkeypatch.setenv("_UID", uid)
    c.headers.update({})


# ---------------------------------------------------------------------------
def test_register_defaults_signal_only_locked(world):
    c, store, _ = world
    r = c.post("/api/auth/register", json={"email": "New@x.io", "password": "pw123456",
                                           "display_name": "New"})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["role"] == "user" and u["trading_permission"] == "locked"
    assert u["status"] == "active"


def test_owner_is_implicit_admin_and_non_admin_gets_403(world):
    c, store, _ = world
    _mk_user(store, "mallory")
    assert c.get("/api/admin/users").status_code == 200          # owner (boss)
    _as(world, "mallory")
    assert c.get("/api/admin/users").status_code == 403
    # even a user doc with a forged role cannot reach admin routes unless the
    # DB says so AND they are not the owner
    _mk_user(store, "sneaky", role="admin")
    _as(world, "sneaky")
    assert c.get("/api/admin/users").status_code == 200          # real admin role works
    _as(world, "boss")


def test_suspend_blocks_every_authenticated_route(world):
    """Exercises the REAL get_user_id dependency (real HMAC token, no
    override) - a suspended user must fail every authenticated route."""
    c, store, monkeypatch = world
    from app.core.security import issue_token
    from app.api.deps import get_user_id
    from app.main import app as application
    _mk_user(store, "u2")
    _mk_user(store, "boss", role="admin")   # real auth path requires the doc
    tok = issue_token("u2")
    boss_tok = issue_token("boss")
    application.dependency_overrides.pop(get_user_id, None)   # real auth path
    admin_h = {"Authorization": f"Bearer {boss_tok}"}
    try:
        assert c.post("/api/admin/users/u2/status", headers=admin_h,
                      json={"status": "suspended", "reason": "test"}).status_code == 200
        r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403 and "suspended" in r.json()["detail"].lower()
        assert c.post("/api/admin/users/u2/status", headers=admin_h,
                      json={"status": "active", "reason": "restore"}).status_code == 200
        assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    finally:
        application.dependency_overrides[get_user_id] = lambda: os.environ.get("_UID", "boss")


def test_enable_requires_full_setup(world):
    c, store, _ = world
    _mk_user(store, "u3")
    # not even admin can skip requirements
    r = c.post("/api/admin/users/u3/trading", json={"permission": "enabled", "reason": "fast"})
    assert r.status_code == 409 and "missing" in r.json()["detail"]
    # admin pre-approval -> SETUP
    assert c.post("/api/admin/users/u3/trading",
                  json={"permission": "setup", "reason": "vetted"}).status_code == 200
    req = c.get("/api/admin/users/u3/requirements").json()
    assert req["requirements"]["admin_approved"] is True
    assert req["requirements"]["mt5_verified"] is False
    # verify MT5 + setup + risk -> enable succeeds
    g = store.create("agent_goals", {"userId": "u3", "execution_mode": "vps",
                                     "mt5_verified": True, "mt5_account": {"login": 1}})
    store.create("settings", {"userId": "u3", "kind": "risk"})
    assert c.post("/api/admin/users/u3/trading",
                  json={"permission": "enabled", "reason": "complete"}).status_code == 200
    assert c.get("/api/admin/users/u3/requirements").json()["can_enable"][0] is True


def test_executor_respects_db_permission(world):
    c, store, _ = world
    from app.execution import mt5 as X
    X._CONFIRMED_POSITIONS.clear()
    _mk_user(store, "trader1")
    store.create("agent_goals", {"userId": "trader1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True,
                                 "execution_mode": "vps"})
    SIG = {"id": "s1", "userId": "trader1", "signal_id": "SIG-X", "market": "EURUSD",
           "direction": "SELL", "entry": 1.10, "sl": 1.11, "tp1": 1.09,
           "tp2": 1.08, "tp3": 1.07}
    store.create("signals", dict(SIG))
    sent = []
    X_bridge = {"balance": 1000}
    X_orig_get, X_orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: X_bridge if p == "/account" else None
    X.bridge_post = lambda p, payload, timeout=15: sent.append(payload) or {"ok": True, "ticket": 9}
    try:
        # LOCKED (default) -> never trades
        X.execute_signal(store.get("signals", "s1"), "trader1")
        doc = store.get("signals", "s1")
        assert doc["execution_status"] == "SKIPPED_NOT_AUTHORIZED" and sent == []
        # ENABLED in the DB -> trades
        store.update("users", _uid_id(store, "trader1"), {"trading_permission": "enabled"})
        X.execute_signal(store.get("signals", "s1"), "trader1")
        doc = store.get("signals", "s1")
        assert doc["execution_status"] == "SUBMITTED" and len(sent) == 1
        # revoked afterwards -> the NEXT order is refused (db is the source)
        store.update("users", _uid_id(store, "trader1"), {"trading_permission": "locked"})
        store.create("signals", dict(SIG, id="s2", signal_id="SIG-X2"))
        X.execute_signal(store.get("signals", "s2"), "trader1")
        assert store.get("signals", "s2")["execution_status"] == "SKIPPED_NOT_AUTHORIZED"
    finally:
        X.bridge_get, X.bridge_post = X_orig_get, X_orig_post
        X._CONFIRMED_POSITIONS.clear()


def _uid_id(store, uid):
    return store.list("users", filters={"id": uid}, limit=1)[0]["id"]


def test_emergency_stop_and_audit_trail(world):
    c, store, _ = world
    _mk_user(store, "u4", trading_permission="enabled")
    store.create("agent_goals", {"userId": "u4", "execution_enabled": True,
                                 "execution_mode": "vps"})
    r = c.post("/api/admin/users/u4/emergency-stop")
    assert r.status_code == 200 and r.json()["stopped"] is True
    u = store.get("users", "u4")
    assert u["status"] == "suspended" and u["trading_permission"] == "locked"
    assert store.list("agent_goals", filters={"userId": "u4"})[0]["execution_enabled"] is False
    tail = c.get("/api/admin/overview").json()["audit_tail"]
    actions = [a["action"] for a in tail]
    assert "user.emergency_stop" in actions
    entry = [a for a in tail if a["action"] == "user.emergency_stop"][0]
    assert entry["actor"] == "boss" and entry["prev"]["trading_permission"] == "enabled"
    assert entry["new"]["status"] == "suspended"


def test_admin_cannot_suspend_self_and_revoke_disables_execution(world):
    c, store, _ = world
    _mk_user(store, "boss", role="admin")     # owner's own doc exists too
    assert c.post("/api/admin/users/boss/status",
                  json={"status": "suspended"}).status_code == 422
    _mk_user(store, "u5", trading_permission="enabled")
    store.create("agent_goals", {"userId": "u5", "execution_enabled": True})
    assert c.post("/api/admin/users/u5/trading",
                  json={"permission": "locked", "reason": "revoke"}).status_code == 200
    assert store.list("agent_goals", filters={"userId": "u5"})[0]["execution_enabled"] is False
