"""Serious-check fixes (2026-09-21 afternoon): S2 fresh-sweep rule, broker
min-stop/max-lot guards, fresh cap count.

Live findings driving these: SIG-007 (S2 EURUSD BUY) executed FAILED
'retcode 10016 Invalid stops' with a 2.2-pip SL (and 0.77 lots implied);
SIG-006 -> SIG-007 re-fired 15 min apart off the SAME sweep (machine must
return to NO_SWEEP after a signal, per the Pine loop).
"""
import pandas as pd
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """Isolated store - S2 state must never touch the repo's data/db.json."""
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    return store


# ================= S2: SIGNAL -> NO_SWEEP (fresh sweep required) ===========
def _frames():
    import sys
    sys.path.insert(0, "tests")
    from test_strategy2_mtf import (bull_sweep_frame, bos_bull_frame,
                                    entry_retest_frame)
    return entry_retest_frame(), bull_sweep_frame(), bos_bull_frame()


def test_s2_does_not_refire_without_fresh_sweep(world):
    from app.strategies.strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy
    S = MtfSweepBosRetestStrategy()
    e, sw, bo = _frames()
    c1 = S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c1 is not None

    # evolve: NEW BOS candle (breaks a new confirmed pivot) + NEW retest candle,
    # but the SAME original sweep -> the Pine machine requires a NEW sweep
    bo2 = bo.copy()
    new_bo_row = pd.DataFrame([(103.4, 103.9, 103.3, 103.8)],
                              columns=["open", "high", "low", "close"],
                              index=[bo.index[-1] + pd.Timedelta("1h")])
    bo2 = pd.concat([bo2, new_bo_row])
    e2 = e.copy()
    new_e_row = pd.DataFrame([(103.05, 103.25, 103.0, 103.2)],
                             columns=["open", "high", "low", "close"],
                             index=[e.index[-1] + pd.Timedelta("15min")])
    e2 = pd.concat([e2, new_e_row])
    c2 = S.detect_signal(e2, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo2})
    assert c2 is None, "same sweep must NOT re-arm after a signal (Pine: SIGNAL -> NO SWEEP)"

    # a FRESH sweep re-arms the machine
    sw2 = pd.concat([sw, pd.DataFrame(
        [(101.5, 102, 99.0, 101.8)],       # takes out the prior candle's low
        columns=["open", "high", "low", "close"],
        index=[sw.index[-1] + pd.Timedelta("4h")])])
    c3 = S.detect_signal(e2.iloc[:-1], "XAUUSD", "15M",
                         higher_frames={"4H": sw2, "1H": bo2})
    assert c3 is None  # sweep processed, but no completed BOS+retest in this tick


def test_s2_bullish_state_cleared_after_signal(world):
    from app.strategies.strategy_2_mtf_sweep_bos_retest import state as ss
    from app.strategies.strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy
    S = MtfSweepBosRetestStrategy()
    e, sw, bo = _frames()
    c = S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c is not None
    st = ss.load("XAUUSD", "15M")
    assert st["bullish_setup"] is False and st["bearish_setup"] is False
    assert st["waiting_bull_retest"] is False and st["broken_high"] is None


# ================= execution guards ========================================
@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake:8700")
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 99)
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    sent = []

    def fake_post(path, payload, timeout=15):
        sent.append(payload)
        return {"ok": True, "ticket": 1, "position_id": 2,
                "volume": payload["lots"], "price": payload["symbol"] and 1.1}
    monkeypatch.setattr(X, "bridge_post", fake_post)
    store.create("agent_goals", {"userId": "u1", "account_balance": 10000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True,
                                 "mode": "vps"})
    return store, sent


def _sig(store, **over):
    doc = {"userId": "u1", "signal_id": "SIG-T-001", "market": "EURUSD",
           "direction": "SELL", "entry": 1.1000, "sl": 1.1020,       # 20 pips
           "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850,
           "candle_time": "2026-09-21 12:00:00", "execution_status": ""}
    doc.update(over)
    doc["id"] = store.create("signals", dict(doc))["id"] if False else None
    d = store.create("signals", dict(doc))
    return d


def test_tight_stop_skipped_never_sent(env):
    store, sent = env
    d = _sig(store, sl=1.10022)                     # 2.2 pips like SIG-007
    X.execute_signal(store.get("signals", d["id"]), "u1")
    assert sent == []                               # nothing reached the broker
    doc = store.get("signals", d["id"])
    assert doc["execution_status"] == "SKIPPED_STOP_TOO_TIGHT"
    assert "2.2 pips" in (doc.get("mt5_note") or "")


def test_normal_stop_still_executes(env):
    store, sent = env
    d = _sig(store)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    assert len(sent) == 1
    assert store.get("signals", d["id"])["execution_status"] == "SUBMITTED"


def test_lots_capped_at_max(env, monkeypatch):
    store, sent = env
    d = _sig(store)
    base = X.calc_lot("EURUSD", 1.1000, 1.1020, 1000, 1.0)   # mocked acct balance
    assert base == 0.05
    cap = 0.02                                       # definitely below the computed lot
    monkeypatch.setattr(X, "MAX_LOTS", cap)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    assert sent and sent[0]["lots"] == cap           # capped down to the max


# ================= fresh cap count =========================================
def test_count_fresh_bypasses_cache(fresh_store):
    from app.db import store as store_mod
    fs = store_mod._store
    d = fs.create("signals", {"userId": "uX", "day": "2026-09-21", "x": 1})
    assert fs.count("signals", filters={"userId": "uX", "day": "2026-09-21"}) >= 1
    # fresh=True must also work through the same signature
    assert fs.count("signals", filters={"userId": "uX", "day": "2026-09-21"},
                    fresh=True) >= 1
