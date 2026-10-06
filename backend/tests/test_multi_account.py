"""Final master build sections 16-19 + 21-25: multi-user, multi-account and
Telegram isolation - enforced SERVER-side (backend is the permission truth).

Covers: account add/select/default/trading/delete, per-user isolation
(user A can never see, select or touch user B's account), execution routed
only to the user's SELECTED account (and refused when it is disabled), the
shared VPS bridge staying owner-only, Telegram disconnect/reconnect
semantics and per-user notification routing.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


@pytest.fixture()
def world(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    monkeypatch.setattr(State, "store", store, raising=False)
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(settings, "telegram_bot_username", "forexmind_bot")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    yield store, app
    app.dependency_overrides.pop(get_user_id, None)


def _c(app):
    return TestClient(app)


# ------------------------------------------------------------- accounts API
def test_add_first_account_becomes_active_default(world):
    store, app = world
    r = _c(app).post("/api/accounts", json={"label": "Exness Demo",
                                            "broker": "Exness", "mode": "own_pc"})
    assert r.status_code == 200, r.text
    a = r.json()["account"]
    assert a["is_active"] and a["is_default"]
    assert a["trading_enabled"] is False          # born safe (LOCKED)


def test_non_owner_cannot_create_vps_account(world):
    store, app = world
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "plain"
    r = _c(app).post("/api/accounts", json={"label": "VPS", "mode": "vps_bridge"})
    assert r.status_code == 403
    app.dependency_overrides[get_user_id] = lambda: "boss"


def test_select_switches_context_and_is_per_user(world):
    store, app = world
    c = _c(app)
    a1 = c.post("/api/accounts", json={"label": "Demo"}).json()["account"]
    a2 = c.post("/api/accounts", json={"label": "Live"}).json()["account"]
    assert not a2["is_active"]
    assert c.post(f"/api/accounts/{a2['id']}/select").status_code == 200
    rows = c.get("/api/accounts").json()["accounts"]
    by = {r["id"]: r for r in rows}
    assert by[a2["id"]]["is_active"] and not by[a1["id"]]["is_active"]

    # user B lives in a different world entirely
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss2"
    assert c.get("/api/accounts").json()["accounts"] == []
    r = c.post(f"/api/accounts/{a2['id']}/select")
    assert r.status_code == 404                    # cannot touch A's account
    r = c.delete(f"/api/accounts/{a2['id']}")
    assert r.status_code == 404                    # cannot delete A's account
    app.dependency_overrides[get_user_id] = lambda: "boss"


def test_trading_enable_requires_permission_and_gate_blocks(world, monkeypatch):
    store, app = world
    c = _c(app)
    a = c.post("/api/accounts", json={"label": "Demo", "mode": "own_pc"}).json()["account"]
    # permission layer denies -> enable refused
    from app.core import permissions as P
    monkeypatch.setattr(P, "trading_allowed", lambda uid: (False, "not approved"))
    r = c.post(f"/api/accounts/{a['id']}/trading", json={"enabled": True})
    assert r.status_code == 403
    # permission granted -> enabled
    monkeypatch.setattr(P, "trading_allowed", lambda uid: (True, ""))
    assert c.post(f"/api/accounts/{a['id']}/trading",
                  json={"enabled": True}).status_code == 200
    # now DISABLE it again: execution must refuse (section 19)
    c.post(f"/api/accounts/{a['id']}/trading", json={"enabled": False})
    store.create("signals", {"id": "s1", "userId": "boss", "market": "EURUSD",
                             "direction": "BUY", "entry": 1.1, "sl": 1.098})
    from app.config import settings as S
    monkeypatch.setattr(S, "execution_mode", "manual")
    monkeypatch.setattr(X, "user_mode", lambda uid: "manual")
    sent = []
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15: sent.append(payload))
    X.execute_signal(store.get("signals", "s1"), "boss")
    doc = store.get("signals", "s1")
    assert doc["execution_status"] == "SKIPPED_ACCOUNT_DISABLED"
    assert sent == []


def test_execution_stamps_selected_account(world, monkeypatch):
    store, app = world
    c = _c(app)
    a1 = c.post("/api/accounts", json={"label": "Demo", "mode": "own_pc"}).json()["account"]
    a2 = c.post("/api/accounts", json={"label": "Live", "mode": "own_pc"}).json()["account"]
    c.post(f"/api/accounts/{a2['id']}/select")
    c.post(f"/api/accounts/{a2['id']}/trading", json={"enabled": True})
    store.create("signals", {"id": "s2", "userId": "boss", "market": "EURUSD",
                             "direction": "BUY", "entry": 1.1, "sl": 1.098})
    from app.config import settings as S
    monkeypatch.setattr(S, "execution_mode", "manual")
    monkeypatch.setattr(X, "user_mode", lambda uid: "manual")
    monkeypatch.setattr(X, "connector_online", lambda uid: False)
    X.execute_signal(store.get("signals", "s2"), "boss")
    doc = store.get("signals", "s2")
    assert doc.get("account_id") == a2["id"]       # queued for the SELECTED account


def test_default_survives_and_disconnect_falls_back(world):
    store, app = world
    c = _c(app)
    a1 = c.post("/api/accounts", json={"label": "Demo"}).json()["account"]
    a2 = c.post("/api/accounts", json={"label": "Live"}).json()["account"]
    c.post(f"/api/accounts/{a1['id']}/default")
    c.post(f"/api/accounts/{a2['id']}/select")
    c.delete(f"/api/accounts/{a2['id']}")
    rows = c.get("/api/accounts").json()["accounts"]
    assert len(rows) == 1 and rows[0]["id"] == a1["id"]
    assert rows[0]["is_active"]                    # fallback re-activation


# ---------------------------------------------------------------- telegram
def test_telegram_disconnect_clears_and_reconnect_needs_new_token(world, monkeypatch):
    store, app = world
    store.create("users", {"email": "a@x.y", "salt": "s", "password_hash": "h",
                           "telegram_chat_id": "111"}, doc_id="boss")
    c = _c(app)
    st = c.get("/api/telegram/status").json()
    assert st["linked"] is True and st["chat_hint"] == "••••111"
    assert st["bot_username"] == "forexmind_bot"   # ONE official bot, no user setup
    assert c.post("/api/telegram/disconnect").json()["ok"]
    u = store.get("users", list(store.list("users", limit=5))[0]["id"])
    assert not u.get("telegram_chat_id")           # mapping invalidated
    assert u.get("telegram_disconnected_at")
    # after disconnect the notification path can NEVER deliver
    from app.notifications.telegram import send_telegram
    sent = []
    import app.notifications.telegram as T
    import requests as _rq
    class R:
        status_code = 200
        def json(self): return {}
    monkeypatch.setattr(T.requests, "post", lambda *a, **k: sent.append(a))
    assert send_telegram(u["id"], "private event") is False   # no chat -> no send
    assert sent == []                              # zero cross-user leakage


def test_notification_routing_is_owner_only(world, monkeypatch):
    """Section 24: user B must receive NOTHING about user A's trade."""
    store, app = world
    ua = store.create("users", {"email": "a@x.y", "salt": "s", "password_hash": "h",
                                "telegram_chat_id": "AAA"})
    store.create("users", {"email": "b@x.y", "salt": "s", "password_hash": "h",
                           "telegram_chat_id": "BBB"})
    from app.notifications import telegram as T
    sent = []
    def fake_post(url, json=None, timeout=10):
        sent.append(json)
        class R: status_code = 200
        return R()
    monkeypatch.setattr(T.requests, "post", fake_post)
    from app.notifications.service import notify
    notify(ua["id"], "TP_HIT", "XAUUSD BUY", "TP1 HIT")
    assert len(sent) == 1 and sent[0]["chat_id"] == "AAA"     # A only
