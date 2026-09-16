"""Per-user entry-timeframe setting (user directive 2026-09-16: 'the bot
should only enter on 15min - add a place where the user can set the
timeframe he wants to enter on').

Default: 15M only. The live scan loop reads this per user; existing
signals always finish tracking regardless of the setting.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client, fresh_store):
    from app.seed import _ensure_demo_user
    _ensure_demo_user()
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    assert r.status_code == 200
    return {"token": r.json()["token"], "store": fresh_store}


def A(auth):
    return {"Authorization": f"Bearer {auth['token']}"}


def test_default_is_15m_only():
    from app.models.schemas import RiskSettings
    assert RiskSettings().signal_timeframes == ["15M"]


def test_patch_rejects_5m_and_empty(client, auth):
    r = client.patch("/api/settings", headers=A(auth),
                     json={"signal_timeframes": ["5M"]})
    assert r.status_code == 422          # quota protection: 5M not offered
    r2 = client.patch("/api/settings", headers=A(auth),
                      json={"signal_timeframes": []})
    assert r2.status_code == 422


def test_patch_accepts_valid_sets_and_persists(client, auth):
    r = client.patch("/api/settings", headers=A(auth),
                     json={"signal_timeframes": ["15M", "1H"]})
    assert r.status_code == 200
    got = client.get("/api/settings", headers=A(auth)).json()
    assert got["signal_timeframes"] == ["15M", "1H"]
    # back to the directive default
    r2 = client.patch("/api/settings", headers=A(auth),
                      json={"signal_timeframes": ["15M"]})
    assert r2.status_code == 200


def test_helper_reads_user_setting_with_safe_fallback(auth):
    from app.agent.core import user_signal_timeframes
    # user with no risk doc at all -> 15M only (never crashes the loop)
    assert user_signal_timeframes("user-without-docs") == ["15M"]
    # seeded user follows the stored setting
    from app.agent.core import ensure_user_docs
    ensure_user_docs("demo-user")
    assert user_signal_timeframes("demo-user") == ["15M"]
    auth["store"].update(
        "settings",
        auth["store"].list("settings", filters={"userId": "demo-user", "kind": "risk"},
                           limit=1)[0]["id"],
        {"signal_timeframes": ["1H", "4H"]})
    assert user_signal_timeframes("demo-user") == ["1H", "4H"]
    # corrupted doc -> safe fallback
    sid = auth["store"].list("settings", filters={"userId": "demo-user", "kind": "risk"},
                             limit=1)[0]["id"]
    auth["store"].update("settings", sid, {"signal_timeframes": ["weird"]})
    assert user_signal_timeframes("demo-user") == ["15M"]
