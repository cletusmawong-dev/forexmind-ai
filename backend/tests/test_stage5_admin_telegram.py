"""Stage 5: Telegram one-time-token linking (P13) + Developer Command
Center (P14).

Rules under test:
 - link tokens: single-use, 15-minute TTL, supersede previous unused tokens,
   link the requesting account only; every link is audited
 - EMAIL-BASED LINKING IS GONE: /start <email> must NOT link anything
 - /start <token> consumes the token and stores telegram_chat_id
 - invalid / expired / reused tokens get honest instructions, no leaks
 - command center: admin-only; every section present; sections degrade
   independently (a broken section reports its error, page still builds)
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    _prev = State.store
    State.store = store
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "")
    monkeypatch.setattr(settings, "telegram_bot_token", "TESTTOKEN")
    monkeypatch.setattr(settings, "telegram_bot_username", "forexmind_dev_bot")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "hooksec")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c, store, monkeypatch, settings
    finally:
        State.store = _prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)


def _user(store, uid="boss", **kw):
    doc = {"id": uid, "userId": uid, "email": f"{uid}@x.io", "role": "admin",
           "status": "active"}
    doc.update(kw)
    return store.create("users", doc)


# ===========================================================================
# P13 ONE-TIME TOKEN LINKING
# ===========================================================================
def test_link_token_mint_and_supersede(env):
    c, store, _, _ = env
    _user(store)
    r = c.post("/api/telegram/link-token")
    assert r.status_code == 200
    body = r.json()
    assert len(body["token"]) == 8 and body["token"].isupper()
    assert body["deep_link"] == (f"https://t.me/forexmind_dev_bot"
                                 f"?start={body['token']}")
    docs = store.list("tg_link_tokens", filters={"userId": "boss"}, limit=5)
    assert len(docs) == 1 and docs[0]["used"] is False
    # a second token supersedes the first
    r2 = c.post("/api/telegram/link-token")
    docs = store.list("tg_link_tokens", filters={"userId": "boss"}, limit=5)
    used = [d for d in docs if d.get("used")]
    assert len(docs) == 2 and len(used) == 1      # old one superseded


def test_start_token_links_and_audits(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    replies = []
    import app.notifications.telegram as TG
    monkeypatch.setattr(TG.requests, "post",
                        lambda url, json=None, timeout=10:
                        replies.append(json) or type("R", (), {"status_code": 200})())
    tok = c.post("/api/telegram/link-token").json()["token"]
    out = TG.handle_webhook({"message": {"chat": {"id": "555"},
                                         "text": f"/start {tok}"}})
    assert out["ok"]
    u = store.get("users", "boss")
    assert u["telegram_chat_id"] == "555"
    tdoc = store.list("tg_link_tokens", limit=2)[0]
    assert tdoc["used"] is True
    assert any("Linked" in (m.get("text") or "") for m in replies)
    acts = [a["action"] for a in store.list("audit_log", limit=5)]
    assert "telegram.linked" in acts
    # reuse of the SAME token must not relink a different chat
    TG.handle_webhook({"message": {"chat": {"id": "666"},
                                   "text": f"/start {tok}"}})
    assert store.get("users", "boss")["telegram_chat_id"] == "555"


def test_email_linking_is_gone(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store, "victim", email="victim@x.io")
    replies = []
    import app.notifications.telegram as TG
    monkeypatch.setattr(TG.requests, "post",
                        lambda url, json=None, timeout=10:
                        replies.append(json) or type("R", (), {"status_code": 200})())
    # knowing the email must NOT link anything (old insecure flow removed)
    TG.handle_webhook({"message": {"chat": {"id": "777"},
                                   "text": "/start victim@x.io"}})
    assert store.get("users", "victim").get("telegram_chat_id") is None
    assert any("link code" in (m.get("text") or "").lower() for m in replies)


def test_expired_and_garbage_tokens(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    replies = []
    import app.notifications.telegram as TG
    monkeypatch.setattr(TG.requests, "post",
                        lambda url, json=None, timeout=10:
                        replies.append(json) or type("R", (), {"status_code": 200})())
    # expired token: create then backdate
    tok = c.post("/api/telegram/link-token").json()["token"]
    doc = store.list("tg_link_tokens", limit=1)[0]
    store.update("tg_link_tokens", doc["id"], {
        "expiresAt": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})
    TG.handle_webhook({"message": {"chat": {"id": "888"},
                                   "text": f"/start {tok}"}})
    assert store.get("users", "boss").get("telegram_chat_id") is None
    tdoc = store.list("tg_link_tokens", limit=1)[0]
    assert tdoc["used"] is True          # expired tokens get consumed
    # garbage token: honest instructions, nothing linked
    TG.handle_webhook({"message": {"chat": {"id": "888"},
                                   "text": "/start NOPE1234"}})
    assert store.get("users", "boss").get("telegram_chat_id") is None
    assert any("single-use" in (m.get("text") or "").lower() for m in replies)


def test_status_command(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store, telegram_chat_id="555")
    replies = []
    import app.notifications.telegram as TG
    monkeypatch.setattr(TG.requests, "post",
                        lambda url, json=None, timeout=10:
                        replies.append(json) or type("R", (), {"status_code": 200})())
    TG.handle_webhook({"message": {"chat": {"id": "555"}, "text": "/status"}})
    assert any("Linked" in (m.get("text") or "") for m in replies)


# ===========================================================================
# P14 DEVELOPER COMMAND CENTER
# ===========================================================================
def test_command_center_admin_only_and_complete(env):
    c, store, monkeypatch, settings = env
    _user(store)
    r = c.get("/api/admin/command-center")
    assert r.status_code == 200
    body = r.json()
    for sec in ("users", "daily_walls", "executions", "tp_audit", "learning",
                "strategies", "regimes", "regime_events", "audit_tail",
                "ai_router", "trade_manager", "health"):
        assert sec in body, f"missing section {sec}"
    assert body["users"]["total"] == 1
    assert isinstance(body["regimes"], dict)
    # strategies section reflects the doc
    store.create("strategies", {"id": "strat1", "name": "S1", "status": "ACTIVE",
                                "active_version": "1.1",
                                "sessions": ["London"]})
    body2 = c.get("/api/admin/command-center").json()
    mine = [x for x in body2["strategies"]["strategies"]
            if x["id"] == "strat1"][0]
    assert mine["sessions"] == ["London"] and mine["active_version"] == "1.1"


def test_command_center_rejects_non_admin(env):
    c, store, monkeypatch, settings = env
    _user(store)
    _user(store, "mallory", role="user")
    from app.api.deps import get_user_id
    from app.main import app
    app.dependency_overrides[get_user_id] = lambda: "mallory"
    try:
        assert c.get("/api/admin/command-center").status_code == 403
    finally:
        app.dependency_overrides[get_user_id] = lambda: "boss"


def test_command_center_degrades_gracefully(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    # break one section's dependency; the page must still build
    import app.agent.router as R
    def boom():
        raise RuntimeError("router down")
    monkeypatch.setattr(R.get_router().__class__, "status", boom)
    r = c.get("/api/admin/command-center")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body["ai_router"]
    assert body["users"]["total"] == 1          # the rest still works
