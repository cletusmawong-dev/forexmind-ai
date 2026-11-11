"""Phase 3: account fields, the cap of 5, slot-1 backfill.

Order routing stays on MT5_BRIDGE_URL. A per-account dollar risk stored
on the row does not change the lot that is sent.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.store import LocalStore
from app.strategies import OWNER_STRATEGY_IDS


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-P3-001",
       "market": "EURUSD", "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    monkeypatch.setattr(State, "store", store, raising=False)
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    yield store, app
    app.dependency_overrides.pop(get_user_id, None)


def test_new_account_defaults_to_no_strategies_and_no_suffix(api):
    store, app = api
    r = TestClient(app).post("/api/accounts", json={"label": "Challenge", "broker": "Exness"})
    assert r.status_code == 200, r.text
    account = r.json()["account"]
    assert account["strategy_ids"] == []
    assert account["symbol_suffix"] == ""
    assert account["trading_enabled"] is False
    assert account["risk_per_trade_usd"] is None
    assert account["max_daily_loss_usd"] is None
    assert account["kill_switch"] is False


def test_account_stores_its_own_risk_and_strategy_pick(api):
    _store, app = api
    sid = sorted(OWNER_STRATEGY_IDS)[0]
    r = TestClient(app).post("/api/accounts", json={
        "label": "Live",
        "symbol_suffix": "",
        "strategy_ids": [sid],
        "risk_per_trade_usd": 25,
        "max_daily_loss_usd": 100,
    })
    assert r.status_code == 200, r.text
    account = r.json()["account"]
    assert account["strategy_ids"] == [sid]
    assert account["risk_per_trade_usd"] == 25
    assert account["max_daily_loss_usd"] == 100


def test_unknown_strategy_is_refused(api):
    _store, app = api
    r = TestClient(app).post("/api/accounts", json={
        "label": "Live", "strategy_ids": ["not-a-strategy"]})
    assert r.status_code == 422


def test_sixth_account_is_rejected(api):
    _store, app = api
    c = TestClient(app)
    for i in range(5):
        r = c.post("/api/accounts", json={"label": f"A{i}"})
        assert r.status_code == 200, r.text
    r = c.post("/api/accounts", json={"label": "A5"})
    assert r.status_code == 409


def test_combined_gold_cap_is_off_by_default(api):
    from app.risk.book import combined_gold_cap_usd
    assert combined_gold_cap_usd() is None


def test_backfill_slot1_once_and_live_send_ignores_account_risk(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    store.create("signals", dict(SIG))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(settings, "owner_user_id", "u1")
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    import app.market_data.calendar as cal
    monkeypatch.setattr(cal, "is_blackout", lambda m: (False, None))

    from app.execution import mt5 as X

    def get(path, timeout=8):
        if path.startswith("/account"):
            return {"balance": 1000, "equity": 1000, "login": 998877,
                    "server": "Exness-MT5Real", "currency": "USD"}
        if path.startswith("/positions"):
            return {"positions": []}
        if path.startswith("/deals"):
            return {"deals": []}
        if path.startswith("/rates"):
            return {"bars": [{"c": 1.1000}]}
        return None

    posts = []

    def post(path, payload, timeout=15):
        posts.append((path, payload))
        return {"ok": True, "ticket": 55, "volume": payload.get("lots"), "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", post)
    monkeypatch.setattr(X, "get_store", lambda: store)

    from app.accounts.backfill import backfill_slot1
    row = backfill_slot1("u1")
    assert row is not None
    assert row["trading_enabled"] is True
    assert row["bridge_slot"] == 1
    assert row["bridge_url"] == ""
    assert row["magic"] == 20260914
    assert row["symbol_suffix"] == ""
    assert row["risk_per_trade_usd"] is None
    assert row["login"] == "998877"
    assert set(row["strategy_ids"]) == set(OWNER_STRATEGY_IDS)
    assert backfill_slot1("u1") is None
    assert len(store.list("trading_accounts", limit=10)) == 1

    # A stored per-account risk must not change the lot. Sizing still uses
    # the user-level default ($50 -> 0.25 lots on this EURUSD stop).
    store.update("trading_accounts", row["id"], {"risk_per_trade_usd": 1})
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert posts[0][0] == "/execute"
    assert posts[0][1]["lots"] == pytest.approx(0.25)
    assert posts[0][1]["magic"] == 20260914
    assert store.get("signals", "sig1")["execution_status"] == "SUBMITTED"


def test_backfill_does_nothing_when_the_bridge_is_off(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(settings, "execution_mode", "off")
    monkeypatch.setattr(settings, "bridge_url", "")
    from app.accounts.backfill import backfill_slot1
    assert backfill_slot1("u1") is None
    assert store.list("trading_accounts", limit=10) == []
