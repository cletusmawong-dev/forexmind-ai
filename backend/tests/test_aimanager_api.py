"""Phase 10: /api/aimanager/status + /api/daily surface (honest, key-free)."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.db.store import LocalStore

MAGIC = 20260914


@pytest.fixture()
def world_aim(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True,
                                 "execution_mode": "vps"})
    store.create("settings", {"userId": "u1", "kind": "risk", "session_tz": "UTC",
                              "risk_per_trade_pct": 1.0})
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.api.deps import get_user_id
    from app.main import app
    from app.state import State
    monkeypatch.setattr(State, "store", store, raising=False)
    app.dependency_overrides[get_user_id] = lambda: "u1"
    with TestClient(app) as c:
        yield store, c
    app.dependency_overrides.pop(get_user_id, None)


def test_status_shape(world_aim):
    store, client = world_aim
    r = client.get("/api/aimanager/status")
    assert r.status_code == 200
    d = r.json()
    for k in ("router", "manager", "daily", "recent_decisions", "note"):
        assert k in d
    assert "primary_model" in d["router"]
    assert "managed_positions" in d["manager"]
    assert d["daily"].get("status") in (None, "NORMAL", "TARGET_HIT", "LOSS_LIMIT_HIT",
                                        "TARGET_AND_LIMIT_HIT")


def test_status_lists_recent_decisions(world_aim):
    store, client = world_aim
    store.create("ai_decisions", {"userId": "u1", "symbol": "XAUUSD", "trigger": "tp1_hit",
                                  "model": "qwen", "layer": "primary",
                                  "decision": {"action": "PROTECT", "confidence": 0.8,
                                               "reason_codes": ["M15_STRONG"]},
                                  "gate_verdict": "OK_PROTECT", "execution": {}})
    r = client.get("/api/aimanager/status")
    dec = r.json()["recent_decisions"]
    assert dec and dec[0]["action"] == "PROTECT" and dec[0]["gate_verdict"] == "OK_PROTECT"


def test_daily_endpoint(world_aim):
    store, client = world_aim
    r = client.get("/api/daily")
    assert r.status_code == 200
    d = r.json()
    for k in ("realized_usd", "total_usd", "hit_target", "hit_loss", "status"):
        assert k in d
