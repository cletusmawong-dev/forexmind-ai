"""Journal MT5 trade log: executed trades with live/broker-confirmed dollars."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def jworld(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "execution_enabled": True,
                                 "execution_mode": "vps", "mt5_deal_sync_ts": 0})
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.api.deps import get_user_id
    from app.main import app
    from app.state import State
    import app.execution.mt5 as mt5x
    monkeypatch.setattr(State, "store", store, raising=False)

    store.create("signals", {"userId": "u1", "signal_id": "SIG-X", "market": "EURUSD",
                             "direction": "BUY", "timeframe": "15M",
                             "entry": 1.1, "sl": 1.09, "tp1": 1.11, "tp2": 1.12,
                             "mt5_ticket": 111, "mt5_volume": 0.85,
                             "mt5_open_price": 1.1, "createdAt": "2026-09-18T08:00:00"})
    store.create("signals", {"userId": "u1", "signal_id": "SIG-Y", "market": "XAUUSD",
                             "direction": "SELL", "timeframe": "15M",
                             "entry": 4300.0, "sl": 4310.0, "tp1": 4280.0,
                             "mt5_ticket": 222, "mt5_volume": 0.01,
                             "mt5_confirmed": True, "mt5_pl": 21.5,
                             "outcome": "WIN", "mt5_closed_at": "2026-09-18T09:00:00",
                             "createdAt": "2026-09-18T07:00:00"})
    from app.config import settings
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700", raising=False)
    monkeypatch.setattr(mt5x, "bridge_get",
                        lambda path, timeout=8: {"positions": [
                            {"ticket": 111, "magic": 20260914, "profit": -11.05}]})
    app.dependency_overrides[get_user_id] = lambda: "u1"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_user_id, None)


def test_mt5_trades_live_and_closed(jworld):
    r = jworld.get("/api/journal/mt5-trades")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2 and d["mode"] == "vps"
    by_sig = {t["signal_id"]: t for t in d["trades"]}
    live = by_sig["SIG-X"]
    assert live["state"] == "LIVE" and live["pl_usd"] == -11.05 and live["ticket"] == 111
    closed = by_sig["SIG-Y"]
    assert closed["state"] == "CLOSED" and closed["pl_usd"] == 21.5
    assert closed["outcome"] == "WIN"


def test_mt5_trades_skips_unexecuted(jworld):
    from app.db import store as store_mod
    store_mod._store.create("signals", {"userId": "u1", "signal_id": "SIG-ADV",
                                        "market": "GBPUSD", "direction": "BUY",
                                        "entry": 1.2, "sl": 1.19})
    r = jworld.get("/api/journal/mt5-trades")
    assert all(t["signal_id"] != "SIG-ADV" for t in r.json()["trades"])


def test_journal_stats_survives_missing_candle_time(jworld):
    """Regression: an MT5-synced close with NO candle_time (real case:
    SIG-20260918-011) must not 500 the whole journal - stats falls back to
    completed_at/createdAt and skips unparseable timestamps."""
    from app.db import store as store_mod
    store_mod._store.create("signals", {"userId": "u1", "signal_id": "SIG-NOCT",
                                        "market": "GBPUSD", "direction": "SELL",
                                        "timeframe": "15M", "entry": 1.34, "sl": 1.35,
                                        "tp1": 1.32, "completed": True,
                                        "status": "CLOSED_MT5", "outcome": "WIN",
                                        "r_multiple": -1.0,
                                        "createdAt": "2026-09-19T13:25:00+00:00",
                                        "completed_at": "2026-09-21T14:09:12+00:00"})
    r = jworld.get("/api/journal/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["completed"] >= 1
    assert "2026-09-21" in d["daily"]     # fell back to completed_at
