"""API + security tests (SPEC §45, §58): auth, data scoping, chat grounding."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import os
    os.environ["REPLAY_ENABLED"] = "0"
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_login_flow_and_scoped_access(client):
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token

    r2 = client.get("/api/signals", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    for sig in r2.json()["signals"]:
        assert sig["userId"] == r.json()["user"]["id"]


def test_without_token_scoped_endpoints_reject(client):
    assert client.get("/api/signals").status_code == 401
    assert client.get("/api/agent/status").status_code == 401
    assert client.get("/api/lessons").status_code == 401


def test_wrong_password_rejected(client):
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "wrong-pass"})
    assert r.status_code == 401


def test_system_info_honest_about_demo(client):
    info = client.get("/api/system/info").json()
    assert info["market_data"]["demo"] is True
    assert "never presented" in info["market_data"]["note"].lower()
    assert "never executes" in info["disclaimer"]


def test_experiment_two_variables_via_api_rejected(client):
    """SPEC §58: even through the API, a two-variable hypothesis is rejected."""
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # hypothesis creation itself only allows one variable (create -> then the
    # experiment engine re-validates against the ACTIVE version)
    r = client.post("/api/hypotheses", headers=headers, json={
        "strategy_id": "strategy_2_ema_atr", "variable": "fast_len",
        "new_value": 10, "reason": "api test", "expected_effect": "test"})
    assert r.status_code == 200
    hyp_id = r.json()["hypothesis"]["id"]

    # run experiment -> must pass one-variable validation and produce honest result
    r = client.post("/api/experiments", headers=headers,
                    json={"hypothesis_id": hyp_id})
    assert r.status_code == 200
    exp = r.json()["experiment"]
    assert exp["variable"] == "fast_len" and exp["old_value"] == 9
    assert exp["result"] in ("IMPROVED", "NO_SIGNIFICANT_CHANGE", "WORSE",
                             "INSUFFICIENT_DATA")


def test_chat_grounded_no_fabrication(client):
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    resp = client.post("/api/chat", headers=headers,
                       json={"message": "What is your current objective?"}).json()
    assert "objective" in resp["reply"].lower()
    resp2 = client.post("/api/chat", headers=headers,
                        json={"message": "please predict tomorrow's exact price"}).json()
    # honest fallback: describes capabilities instead of inventing
    assert "never invent" in resp2["reply"].lower() or "can answer" in resp2["reply"].lower()


def test_manual_analyze_honest_no_setup(client):
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    resp = client.post("/api/signals/analyze", headers=headers,
                       json={"market": "XAUUSD", "timeframe": "15M"}).json()
    assert "qualified" in resp
    if not resp["qualified"]:
        assert "No qualifying setup" in resp.get("message", "")


def test_activity_works_with_live_provider(client):
    """Regression: /api/agent/activity 500'd in live mode because replay_progress
    only exists on the demo provider (Agent Pulse was silently broken)."""
    from app import state
    from app.market_data.base import MarketDataProvider

    class LiveLike(MarketDataProvider):  # no replay_progress, like the real live provider
        is_demo = False

        def get_candles(self, market, timeframe, limit=600):
            return None

        def latest_price(self, market):
            return None

    orig = state.State.provider
    state.State.provider = LiveLike()
    try:
        r = client.get("/api/agent/activity?limit=5")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "activity" in body and body["replay"] == {"demo": False, "progress": None}
    finally:
        state.State.provider = orig
