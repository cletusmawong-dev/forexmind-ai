"""Phase 2: fail closed, one ledger claim per send, no retry without a look-back.

The healthy single-account path still posts one /execute with the same
payload shape. These tests cover the new branches around that path.
"""
import inspect

import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260914-001",
       "market": "EURUSD", "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    store.create("signals", dict(SIG))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    monkeypatch.setattr(settings, "owner_user_id", "u1")
    import app.market_data.calendar as cal
    monkeypatch.setattr(cal, "is_blackout", lambda m: (False, None))
    return store


def _account_get(path, timeout=8):
    if path == "/account" or path.startswith("/account"):
        return {"balance": 1000, "equity": 1000}
    if path.startswith("/rates"):
        return {"bars": [{"c": 1.1000}]}
    if path.startswith("/positions"):
        return {"positions": []}
    if path.startswith("/deals"):
        return {"deals": []}
    return None


def test_live_loop_uses_cached_user_ids():
    from app.main import live_loop
    src = inspect.getsource(live_loop)
    assert "_user_ids" not in src
    assert "_users_cached()" in src


def test_killswitch_read_failure_blocks(monkeypatch):
    from app.risk import killswitch as ks
    from app.db import store as store_mod

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("db down")

    orig = store_mod._store
    store_mod._store = Boom()
    try:
        ok, why = ks.check("EURUSD")
        assert ok is False and why == "KILLSWITCH_READ_FAILED"
    finally:
        store_mod._store = orig


def test_happy_path_still_sends_once(env, monkeypatch):
    posts = []

    def post(path, payload, timeout=15):
        posts.append(payload)
        return {"ok": True, "ticket": 777, "volume": 0.25, "price": 1.0999, "http_status": 200}

    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert posts[0]["signal_id"] == "SIG-20260914-001"
    assert posts[0]["magic"] == X.MAGIC
    assert posts[0]["symbol"] == "EURUSD"
    doc = env.get("signals", "sig1")
    assert doc["execution_status"] == "SUBMITTED" and doc["mt5_ticket"] == 777
    keyed = [e for e in env.list("exec_events", limit=20) if e.get("idempotency_key")]
    assert {e["stage"] for e in keyed} == {"REQUESTED", "CONFIRMED"}


def test_second_send_does_not_hit_the_bridge(env, monkeypatch):
    posts = []

    def post(path, payload, timeout=15):
        posts.append(payload)
        return {"ok": True, "ticket": 777, "volume": 0.25, "price": 1.0999}

    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1


def test_retry_only_after_terminal_says_empty(env, monkeypatch):
    posts = []

    def post(path, payload, timeout=15):
        posts.append(payload)
        if len(posts) == 1:
            return {"error": "bridge unreachable: Timeout"}
        return {"ok": True, "ticket": 9, "volume": 0.25, "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 2
    assert env.get("signals", "sig1")["mt5_ticket"] == 9


def test_already_filled_blocks_the_retry(env, monkeypatch):
    posts = []

    def post(path, payload, timeout=15):
        posts.append(payload)
        return {"error": "bridge unreachable: Timeout"}

    def get(path, timeout=8):
        if path.startswith("/positions"):
            return {"positions": [{
                "comment": "SIG-20260914-001", "magic": X.MAGIC, "ticket": 42,
                "volume": 0.25, "price_open": 1.099, "symbol": "EURUSD"}]}
        return _account_get(path, timeout)

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert env.get("signals", "sig1")["mt5_ticket"] == 42


def test_broker_reject_is_not_retried(env, monkeypatch):
    posts = []

    def post(path, payload, timeout=15):
        posts.append(payload)
        return {"detail": "MT5 retcode 10016 Invalid stops", "http_status": 400}

    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert env.get("signals", "sig1")["execution_status"] == "FAILED"


def test_ledger_failure_does_not_send(env, monkeypatch):
    posts = []
    real = env.create

    def create(coll, doc, doc_id=None):
        if coll == "exec_events" and doc.get("idempotency_key"):
            raise RuntimeError("disk full")
        return real(coll, doc, doc_id)

    monkeypatch.setattr(env, "create", create)
    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", lambda *a, **k: posts.append(1))
    X.execute_signal(dict(SIG), "u1")
    assert posts == []
    assert env.get("signals", "sig1")["execution_status"] == "FAILED_LEDGER"


def test_registry_outage_fails_closed(env, monkeypatch):
    posts = []

    def boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.api.routes_accounts.active_account", boom)
    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", lambda *a, **k: posts.append(1) or {"ok": True})
    X.execute_signal(dict(SIG), "u1")
    assert posts == []
    assert env.get("signals", "sig1")["execution_status"] == "SKIPPED_ACCOUNT_REGISTRY"


def test_missing_accounts_table_keeps_the_live_send(env, monkeypatch):
    posts = []

    def missing(user_id):
        raise RuntimeError(
            "supabase list trading_accounts: 404 PGRST205 Could not find the table")

    def post(path, payload, timeout=15):
        posts.append(payload)
        return {"ok": True, "ticket": 3, "volume": 0.25, "price": 1.1}

    monkeypatch.setattr("app.api.routes_accounts.active_account", missing)
    monkeypatch.setattr(X, "bridge_get", _account_get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert env.get("signals", "sig1")["execution_status"] == "SUBMITTED"
