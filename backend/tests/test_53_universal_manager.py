"""Universal Trade Manager (owner reference implementation 2026-10-29).

Contract under test - the deterministic ladder runs EVERY tick, never waits
for a review trigger, never fires twice:
  +1R : bank 50% (tp1 policy) + SL to break-even + buffer (default 0.1R)
  +2R : lock profit (default +1R)
  after: trail the best price (default 0.9R, tighten-only); when the 5M
         EMA8/21 market read flips against the position, tighten to 0.5R
  +3R : close the remainder (broker TP3 is the primary exit)
"""
import time

import pytest

from app.config import settings
from app.db.store import LocalStore

MAGIC = 20260914
NOW = time.time()

SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260917-001",
       "market": "XAUUSD", "strategy_name": "TPC-15 Pullback", "direction": "BUY",
       "entry": 2400.0, "sl": 2390.0, "tp1": 2410.0, "tp2": 2420.0, "tp3": 2430.0,
       "risk": 10.0, "timeframe": "15M", "status": "SUBMITTED", "completed": False}


def _pos(price):
    return {"ticket": 11, "symbol": "XAUUSD", "app_market": "XAUUSD", "type": "BUY",
            "volume": 0.05, "price_open": 2400.0, "price_current": price,
            "sl": 2390.0, "tp": 2430.0, "profit": price - 2400.0, "magic": MAGIC,
            "time": NOW - 3600, "comment": "SIG-20260917-001",
            "signal_id": "SIG-20260917-001"}


class FakeRouter:
    def analyze(self, prompt, context, escalate=False, user_id=None):
        return {"text": '{"action": "HOLD", "continuation_assessment": "STRONG",'
                        '"confidence": 0.7, "reason_codes": ["T"], '
                        '"risk_state": "CONTROLLED"}',
                "model": "fake", "layer": "primary", "escalated": False,
                "fallback": False, "notes": []}


@pytest.fixture()
def uni(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(settings, "brain_v2_enabled", False)
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True,
                                 "execution_mode": "vps"})
    store.create("settings", {"userId": "u1", "kind": "risk",
                              "allowed_markets": ["XAUUSD"],
                              "ai_manage_enabled": True,
                              "tp1_policy": "partial", "tp3_policy": "close"})
    store.create("signals", dict(SIG))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.execution import mt5 as X
    world = {"pos": _pos(2400.0), "rates": []}

    def fake_get(p, timeout=8):
        if p == "/positions":
            return {"positions": [dict(world["pos"])]}
        if p.startswith("/rates"):
            return {"bars": world["rates"]}
        return {"balance": 1000}

    monkeypatch.setattr(X, "bridge_get", fake_get)
    sent = []
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15:
                        sent.append((p, payload)) or {"ok": True, **(payload or {})})
    monkeypatch.setattr(X, "user_mode", lambda uid: "vps")
    from app.aimanager.engine import TradeManager
    mgr = TradeManager(provider=None, router=FakeRouter())
    return store, X, mgr, sent, world


def _sls(sent):
    return [p["sl"] for op, p in sent if op == "/modify_sl"]


def test_stage0_at_1r_banks_half_and_moves_be_plus_buffer(uni):
    store, X, mgr, sent, world = uni
    world["pos"] = _pos(2410.5)                    # +1.05R
    mgr.tick("u1")
    assert ("/partial_close", {"ticket": 11, "fraction": 0.5}) in sent
    assert 2401.0 in _sls(sent)                    # BE + 0.1R buffer (entry 2400, R 10)


def test_stage1_at_2r_locks_1r_and_never_double_banks(uni):
    store, X, mgr, sent, world = uni
    world["pos"] = _pos(2410.5)                    # tick 1: tp1
    mgr.tick("u1")
    world["pos"] = _pos(2420.5)                    # tick 2: tp2
    mgr.tick("u1")
    assert 2410.0 in _sls(sent)                    # lock +1R
    partials = [1 for op, _ in sent if op == "/partial_close"]
    assert sum(partials) == 1, "the 50% bank must fire exactly once"


def test_stage2_trails_the_extreme_tighten_only(uni):
    store, X, mgr, sent, world = uni
    world["pos"] = _pos(2421.0)                    # jump straight past 2R
    mgr.tick("u1")                                 # tp1 + tp2 + trail 2421-9=2412
    assert 2410.0 in _sls(sent)
    assert 2412.0 in _sls(sent)
    world["pos"] = _pos(2429.0)                    # just under tp3: trail 2420.0
    mgr.tick("u1")
    assert 2420.0 in _sls(sent)
    world["pos"] = _pos(2428.0)                    # pullback - extreme unchanged
    mgr.tick("u1")
    assert _sls(sent).count(2420.0) == 1, "trail must not loosen or spam"


def test_stage2_market_flip_tightens_half_r(uni):
    store, X, mgr, sent, world = uni
    world["pos"] = _pos(2421.0)
    mgr.tick("u1")
    world["pos"] = _pos(2426.0)
    # strong_sell read: falling closes below a falling EMA8 under EMA21
    world["rates"] = [{"c": round(2440.0 - i * 0.8, 1)} for i in range(30)]
    mgr.tick("u1")
    assert 2421.0 in _sls(sent)                    # flip tighten: 2426 - 0.5R


def test_tp3_touch_closes_the_remainder(uni):
    store, X, mgr, sent, world = uni
    world["pos"] = _pos(2421.0)
    mgr.tick("u1")
    world["pos"] = _pos(2430.5)                    # tp3
    mgr.tick("u1")
    assert ("/close", {"ticket": 11}) in sent or \
           any(op == "/close" for op, _ in sent), "TP3 must close the rest"


def test_strategy_owned_plans_are_not_touched(uni):
    store, X, mgr, sent, world = uni
    sig = dict(SIG, id="sig2", signal_id="SIG-20260917-002",
               strategy_id="strategy_trendbreak_v2")
    store.create("signals", sig)
    world["pos"] = dict(_pos(2412.0), signal_id="SIG-20260917-002",
                        comment="SIG-20260917-002")
    mgr.tick("u1")
    assert not sent, "TrendBreak v2 owns its own trail - universal must skip"
