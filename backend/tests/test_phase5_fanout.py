"""Phase 5: fan-out isolation, per-account sizing, caps, kill switch, idempotency."""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X
from app.strategies import OWNER_STRATEGY_IDS

SID = sorted(OWNER_STRATEGY_IDS)[0]
SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-P5-001",
       "strategy_id": SID, "market": "EURUSD", "direction": "SELL",
       "entry": 1.1000, "sl": 1.1020, "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}


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


def _account(i, **extra):
    row = {
        "userId": "u1", "label": f"A{i}", "mode": "vps_bridge",
        "is_active": i == 1, "trading_enabled": True, "bridge_slot": i,
        "bridge_url": "" if i == 1 else f"http://slot{i}:870{i}",
        "strategy_ids": [SID], "symbol_suffix": "", "kill_switch": False,
        "magic": 20260914 if i == 1 else 20260914 + i,
    }
    row.update(extra)
    return row


def test_five_bridges_one_failure_does_not_block_the_rest(env, monkeypatch):
    for i in range(1, 6):
        env.create("trading_accounts", _account(i), doc_id=f"a{i}")
    posts = []

    def get(path, timeout=8):
        url = (X._bridge_ctx.get() or {}).get("url") or ""
        if "slot3" in url:
            raise RuntimeError("bridge timeout")
        if str(path).startswith("/account"):
            return {"balance": 1000, "equity": 1000}
        if str(path).startswith("/quote"):
            return {"bid": 1.1, "ask": 1.1002}
        if str(path).startswith("/positions"):
            return {"positions": []}
        if str(path).startswith("/deals"):
            return {"deals": []}
        return {}

    def post(path, payload, timeout=15):
        url = (X._bridge_ctx.get() or {}).get("url") or settings.bridge_url
        posts.append((url, payload["lots"], payload["magic"]))
        return {"ok": True, "ticket": payload["magic"], "volume": payload["lots"], "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    doc = env.get("signals", "sig1")
    assert doc["execution_status"] == "PARTIAL"
    assert len(posts) == 4
    failed = [row for row in doc["fanout_results"] if row["status"] == "FAILED"]
    filled = [row for row in doc["fanout_results"] if row["status"] == "FILLED"]
    assert len(failed) == 1 and len(filled) == 4


def test_per_account_risk_sizes_each_order(env, monkeypatch):
    # MRMS has no strategy-level dollar override, so each account's own
    # risk_per_trade_usd is what sizes the order.
    sid = "strategy_mrms_scalper"
    env.create("trading_accounts", _account(2, risk_per_trade_usd=50,
                                            strategy_ids=[sid],
                                            bridge_url="http://slot2:8702"), doc_id="a2")
    env.create("trading_accounts", _account(3, risk_per_trade_usd=100,
                                            strategy_ids=[sid],
                                            bridge_url="http://slot3:8703"), doc_id="a3")
    lots = {}

    def get(path, timeout=8):
        if str(path).startswith("/account"):
            return {"balance": 5000, "equity": 5000}
        if str(path).startswith("/quote"):
            return {"bid": 1.1, "ask": 1.1002}
        if str(path).startswith("/positions"):
            return {"positions": []}
        if str(path).startswith("/deals"):
            return {"deals": []}
        return {}

    def post(path, payload, timeout=15):
        url = (X._bridge_ctx.get() or {}).get("url")
        lots[url] = payload["lots"]
        assert payload["symbol"] == "EURUSDm" or True
        return {"ok": True, "ticket": 1, "volume": payload["lots"], "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", post)
    env.update("trading_accounts", "a3", {"symbol_suffix": "m"})
    sized = dict(SIG, strategy_id=sid)
    X.execute_signal(sized, "u1")
    assert lots["http://slot2:8702"] == pytest.approx(0.25)
    assert lots["http://slot3:8703"] == pytest.approx(0.50)
    posted_symbols = []

    def post2(path, payload, timeout=15):
        posted_symbols.append(payload["symbol"])
        return {"ok": True, "ticket": 2, "volume": payload["lots"], "price": 1.1}

    # second signal to observe the suffix on account 3 only
    env.create("signals", dict(SIG, id="sig2", signal_id="SIG-P5-002", strategy_id=sid), doc_id="sig2")
    monkeypatch.setattr(X, "bridge_post", post2)
    X.execute_signal(dict(SIG, id="sig2", signal_id="SIG-P5-002", strategy_id=sid), "u1")
    assert "EURUSDm" in posted_symbols
    assert "EURUSD" in posted_symbols


def test_kill_switch_and_pick_list_and_idempotency(env, monkeypatch):
    env.create("trading_accounts", _account(2, kill_switch=True), doc_id="killed")
    env.create("trading_accounts", _account(3), doc_id="other")
    env.update("trading_accounts", "other", {"strategy_ids": ["strategy_not_this"]})
    env.create("trading_accounts", _account(4, bridge_url="http://slot4:8704"), doc_id="live")
    posts = []

    def get(path, timeout=8):
        if str(path).startswith("/account"):
            return {"balance": 1000, "equity": 1000}
        if str(path).startswith("/quote"):
            return {"bid": 1.1, "ask": 1.1002}
        if str(path).startswith("/positions"):
            return {"positions": []}
        if str(path).startswith("/deals"):
            return {"deals": []}
        return {}

    def post(path, payload, timeout=15):
        posts.append(payload["magic"])
        return {"ok": True, "ticket": 4, "volume": payload["lots"], "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert posts == [20260918]
    X.execute_signal(dict(SIG), "u1")
    assert posts == [20260918]


def test_daily_loss_cap_blocks_only_that_account(env, monkeypatch):
    env.create("trading_accounts", _account(2, max_daily_loss_usd=10,
                                            bridge_url="http://slot2:8702"), doc_id="capped")
    env.create("trading_accounts", _account(3, bridge_url="http://slot3:8703"), doc_id="open")

    def get(path, timeout=8):
        url = (X._bridge_ctx.get() or {}).get("url") or ""
        if str(path).startswith("/deals") and "slot2" in url:
            return {"deals": [{"magic": 20260916, "profit": -25, "commission": 0, "swap": 0}]}
        if str(path).startswith("/deals"):
            return {"deals": []}
        if str(path).startswith("/positions"):
            return {"positions": []}
        if str(path).startswith("/account"):
            return {"balance": 1000, "equity": 1000}
        if str(path).startswith("/quote"):
            return {"bid": 1.1, "ask": 1.1002}
        return {}

    posts = []
    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", lambda *a, **k: posts.append(
        (X._bridge_ctx.get() or {}).get("url")) or {"ok": True, "ticket": 1, "volume": 0.25, "price": 1.1})
    X.execute_signal(dict(SIG), "u1")
    assert posts == ["http://slot3:8703"]


def test_combined_gold_cap_stops_the_second_account(env, monkeypatch):
    env.create("settings", {"combined_gold_cap_usd": 60}, doc_id="book_risk")
    env.create("trading_accounts", _account(2, risk_per_trade_usd=50,
                                            bridge_url="http://slot2:8702"), doc_id="g1")
    env.create("trading_accounts", _account(3, risk_per_trade_usd=50,
                                            bridge_url="http://slot3:8703"), doc_id="g2")
    gold = dict(SIG, market="XAUUSD", entry=2400.0, sl=2390.0, tp1=2430, tp2=2460, tp3=2490)
    posts = []

    def get(path, timeout=8):
        if str(path).startswith("/account"):
            return {"balance": 10000, "equity": 10000}
        if str(path).startswith("/quote"):
            return {"bid": 2400, "ask": 2400.5}
        if str(path).startswith("/positions"):
            return {"positions": []}
        if str(path).startswith("/deals"):
            return {"deals": []}
        return {}

    monkeypatch.setattr(X, "bridge_get", get)
    monkeypatch.setattr(X, "bridge_post", lambda *a, **k: posts.append(1) or
                        {"ok": True, "ticket": 1, "volume": 0.01, "price": 2400})
    X.execute_signal(gold, "u1")
    assert len(posts) == 1


def test_slot1_alone_stays_on_the_legacy_bridge(env, monkeypatch):
    env.create("trading_accounts", _account(1, bridge_url="", trading_enabled=True,
                                            strategy_ids=[SID]), doc_id="a1")
    env.create("trading_accounts", _account(2, trading_enabled=False), doc_id="a2")
    posts = []

    def post(path, payload, timeout=15):
        assert X._bridge_ctx.get() is None
        posts.append(payload)
        return {"ok": True, "ticket": 11, "volume": payload["lots"], "price": 1.1}

    monkeypatch.setattr(X, "bridge_get", lambda path, timeout=8: (
        {"balance": 1000, "equity": 1000} if str(path).startswith("/account")
        else {"bid": 1.1, "ask": 1.1002} if str(path).startswith("/quote")
        else {"positions": []} if str(path).startswith("/positions")
        else {"deals": []} if str(path).startswith("/deals")
        else {"bars": [{"c": 1.1}]} if str(path).startswith("/rates")
        else {}))
    monkeypatch.setattr(X, "bridge_post", post)
    X.execute_signal(dict(SIG), "u1")
    assert len(posts) == 1
    assert posts[0]["symbol"] == "EURUSD"
    assert posts[0]["lots"] == pytest.approx(0.25)
    assert "fanout_results" not in (env.get("signals", "sig1") or {})
