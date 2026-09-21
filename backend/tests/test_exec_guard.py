"""2026-09-21 incident fixes: double orders + duplicate signal_ids + latency.

Broker history showed every Sunday-open signal placed TWICE on the shared VPS
account (7s apart, identical signal_id comments): multi-user signal delivery
creates one doc per user (by design), and every VPS-mode copy executed. Root
enablers: (a) no cross-user "setup already sent" guard, (b) count() cache not
invalidated on create -> two docs shared one signal_id. Order latency (user:
"supposed to fill at 1:11, filled 1:13") traced to the fixed 60s loop tick
missing candle boundaries by up to a minute.
"""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake:8700")
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 99)
    sent = []

    def fake_post(path, payload, timeout=15):
        sent.append(payload)
        return {"ok": True, "ticket": 100 + len(sent), "position_id": 1,
                "volume": payload["lots"], "price": 1.10}

    monkeypatch.setattr(X, "bridge_post", fake_post)
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    return store, sent


def _doc(store, uid, sig_id, candle="2026-09-20 22:45:00"):
    return store.create("signals", {
        "userId": uid, "signal_id": sig_id,
        "market": "EURUSD", "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
        "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850,
        "candle_time": candle, "execution_status": "",
        "signal_id": sig_id})


def _goals(store, uid):
    store.create("agent_goals", {"userId": uid, "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True,
                                 "mode": "vps"})


def test_same_setup_executes_once(env):
    """Two users' copies of the SAME setup -> exactly ONE broker order."""
    store, sent = env
    _goals(store, "u1")
    _goals(store, "u2")
    d1 = _doc(store, "u1", "SIG-20260920-001")
    d2 = _doc(store, "u2", "SIG-20260920-001")     # same-id collision scenario
    X.execute_signal(d1, "u1")
    X.execute_signal(d2, "u2")
    assert len(sent) == 1                          # ONE broker order
    assert store.get("signals", d1["id"])["execution_status"] == "SUBMITTED"
    d2doc = store.get("signals", d2["id"])
    assert d2doc["execution_status"] == "SKIPPED_SETUP_ALREADY_EXECUTED"
    assert "SIG-20260920-001" in (d2doc.get("mt5_note") or "")


def test_different_candles_both_execute(env):
    """A NEW candle is a NEW setup - the guard must never over-block."""
    store, sent = env
    _goals(store, "u1")
    d1 = _doc(store, "u1", "SIG-20260920-001", candle="2026-09-20 22:45:00")
    d2 = _doc(store, "u1", "SIG-20260920-002", candle="2026-09-20 23:30:00")
    X.execute_signal(d1, "u1")
    X.execute_signal(d2, "u1")
    assert len(sent) == 2
    assert store.get("signals", d2["id"])["execution_status"] == "SUBMITTED"


def test_different_markets_both_execute(env):
    store, sent = env
    _goals(store, "u1")
    d1 = _doc(store, "u1", "SIG-20260920-001")
    d2 = _doc(store, "u1", "SIG-20260920-002")
    store.update("signals", d2["id"], {"market": "XAUUSD"})
    d2 = store.get("signals", d2["id"])
    X.execute_signal(d1, "u1")
    X.execute_signal(d2, "u1")
    assert len(sent) == 2


def test_count_cache_invalidated_on_create():
    """Stale count() gave two docs the SAME signal_id (the 001,003,005 gap)."""
    from app.db.store import FirestoreStore
    fs = FirestoreStore.__new__(FirestoreStore)   # no Firebase init
    import threading
    fs._cache = {}
    fs._cache_lock = threading.RLock()
    key = f"C:signals|{repr(sorted(({'day': '2026-09-21'}).items(), key=lambda kv: kv[0]))}"
    fs._cache_set(key, 0)
    assert fs._cache_get(key) == 0
    fs._invalidate("signals", "abc")              # what create() calls
    assert fs._cache_get(key) is None             # count cache is GONE -> fresh count


def test_sleep_to_boundary():
    from app.main import sleep_to_boundary
    b = 1000000.0 * 900                              # a boundary
    assert sleep_to_boundary(b + 30) == 60.0         # far from boundary: normal tick
    assert sleep_to_boundary(b + 890) == pytest.approx(12.0)   # approach: align
    assert sleep_to_boundary(b + 898.5) == pytest.approx(3.5)
    assert sleep_to_boundary(b - 1) == pytest.approx(3.0)      # wakes just after the boundary
    assert sleep_to_boundary(b) == 60.0                        # just processed it: normal cadence
    assert sleep_to_boundary(b + 1) == 60.0
