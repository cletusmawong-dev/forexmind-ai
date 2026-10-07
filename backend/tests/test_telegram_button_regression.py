"""The 'Open in Telegram' button must NEVER silently disappear.

Regression: TELEGRAM_BOT_USERNAME was unset in production, so deep_link was
None and new users saw a code but no button (and a bare /start then looked
like nothing happened). The backend now falls back to the Bot API getMe,
so the official bot's username is resolved even without the env var.
"""
import os

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
    State.store = store
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "")
    monkeypatch.setattr(settings, "telegram_bot_token", "TESTTOKEN")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "hooksec")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c, monkeypatch
    finally:
        app.dependency_overrides.clear()


def test_status_and_deep_link_work_without_env_username(env, monkeypatch):
    """TELEGRAM_BOT_USERNAME empty -> getMe fallback supplies the username."""
    client, mp = env
    from app.config import settings
    mp.setattr(settings, "telegram_bot_username", "")  # the production gap

    import app.notifications.telegram as tg
    tg._bot_username_cache = None  # reset module cache
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"username": "Clerus_bot"}}

    def fake_get(url, *a, **k):
        calls.append(url)
        assert url.endswith("/getMe")
        return _Resp()

    monkeypatch.setattr(tg.requests, "get", fake_get)

    r = client.get("/api/telegram/status").json()
    assert r["bot_username"] == "Clerus_bot"

    tok = client.post("/api/telegram/link-token").json()
    assert tok["deep_link"] == "https://t.me/Clerus_bot?start=" + tok["token"]
    assert len(calls) == 1  # getMe called once, then cached


def test_env_override_still_wins(env):
    """If TELEGRAM_BOT_USERNAME IS set, no getMe call is made."""
    client, mp = env
    from app.config import settings
    mp.setattr(settings, "telegram_bot_username", "forexmind_dev_bot")
    import app.notifications.telegram as tg
    tg._bot_username_cache = None

    monkeypatch = mp

    def boom(*a, **k):
        raise AssertionError("getMe must not be called when env is set")

    monkeypatch.setattr(tg.requests, "get", boom)
    r = client.get("/api/telegram/status").json()
    assert r["bot_username"] == "forexmind_dev_bot"
