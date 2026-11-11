"""Phase 4: slot 1 still sends. Slots 2-5 are health-only on the legacy path."""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-P4-001",
       "market": "EURUSD", "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://slot1:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    monkeypatch.setattr(settings, "owner_user_id", "u1")
    store.create("agent_goals", {"userId": "u1", "execution_enabled": True,
                                 "execution_mode": "vps", "risk_per_trade_usd": 50})
    store.create("signals", dict(SIG))
    import app.market_data.calendar as cal
    monkeypatch.setattr(cal, "is_blackout", lambda m: (False, None))
    return store


def _get(path, timeout=8):
    if str(path).startswith("/account"):
        return {"balance": 1000, "equity": 1000}
    if str(path).startswith("/positions"):
        return {"positions": []}
    if str(path).startswith("/deals"):
        return {"deals": []}
    if str(path).startswith("/rates"):
        return {"bars": [{"c": 1.1}]}
    return None


def test_slot1_still_sends_when_slot2_is_only_being_watched(env, monkeypatch):
    env.create("trading_accounts", {
        "userId": "u1", "label": "Primary", "mode": "vps_bridge",
        "is_active": True, "trading_enabled": True, "bridge_slot": 1,
        "bridge_url": "", "strategy_ids": [], "symbol_suffix": "",
        "kill_switch": False,
    }, doc_id="slot1")
    env.create("trading_accounts", {
        "userId": "u1", "label": "Second", "mode": "vps_bridge",
        "is_active": False, "trading_enabled": False, "bridge_slot": 2,
        "bridge_url": "http://127.0.0.1:8701", "strategy_ids": [],
        "kill_switch": False,
    }, doc_id="slot2")
    posts = []
    monkeypatch.setattr(X, "bridge_get", _get)
    monkeypatch.setattr(X, "bridge_post", lambda *a, **k: posts.append(a) or
                        {"ok": True, "ticket": 7, "volume": 0.25, "price": 1.1})
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert env.get("signals", "sig1")["execution_status"] == "SUBMITTED"


def test_viewing_slot2_does_not_send_on_the_slot1_bridge(env, monkeypatch):
    """Phase 4 guard: a slot-2 row is not eligible until fan-out, and a
    strategy-less signal with an explicit pick-list is not a slot-1 send.
    Fan-out (phase 5) sends this signal to slot 2's own bridge instead."""
    env.create("trading_accounts", {
        "userId": "u1", "label": "Second", "mode": "vps_bridge",
        "is_active": True, "trading_enabled": True, "bridge_slot": 2,
        "bridge_url": "http://127.0.0.1:8701", "strategy_ids": ["strategy_mrms_scalper"],
        "kill_switch": False,
    }, doc_id="slot2")
    urls = []

    def post(path, payload, timeout=15):
        from app.execution.mt5 import _bridge_target
        urls.append(_bridge_target()[0])
        return {"ok": True, "ticket": 9, "volume": payload.get("lots"), "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", _get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG, strategy_id="strategy_mrms_scalper"), "u1")
    assert urls == ["http://127.0.0.1:8701"]
    assert env.get("signals", "sig1")["execution_status"] == "SUBMITTED"


def test_health_poll_does_not_place_orders(env, monkeypatch):
    env.create("trading_accounts", {
        "userId": "u1", "label": "Second", "bridge_slot": 2, "login": "55",
        "bridge_url": "http://slots.test:8701", "trading_enabled": True,
    }, doc_id="slot2")
    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "slot": 2, "account": {"login": "55", "balance": 2000, "equity": 1900}}

    def get(url, headers=None, timeout=0):
        calls.append(url)
        return Resp()

    monkeypatch.setattr("app.execution.slot_health.requests.get", get)
    from app.execution.slot_health import poll_extra_slots
    assert poll_extra_slots() == 1
    assert calls == ["http://slots.test:8701/health"]
    row = env.get("trading_accounts", "slot2")
    assert row["connected"] is True
    assert row["equity"] == 1900
