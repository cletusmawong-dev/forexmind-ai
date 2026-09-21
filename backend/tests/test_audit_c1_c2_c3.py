"""Production-readiness audit fixes C-1, C-2, C-3 (approved 2026-09-21).

C-1: manual-PC path gets the same EXECUTION_MAX_LOTS cap as the VPS path.
C-2: advisory/blocked branches stamp terminal statuses + mt5_note.
C-3: MFE/MAE (in R, the project's normalized unit) accumulated from closed
     candles by the tracker and persisted at TP-hit updates and completion.
Bookkeeping/coverage only - no trading-logic changes.
"""
import time

import pandas as pd
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
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    sent = []

    def fake_post(path, payload, timeout=15):
        sent.append(payload)
        return {"ok": True, "ticket": 1, "position_id": 2,
                "volume": payload["lots"], "price": 1.10}
    monkeypatch.setattr(X, "bridge_post", fake_post)
    return store, sent


def _sig(store, **over):
    doc = {"userId": "u1", "signal_id": "SIG-C-001", "market": "EURUSD",
           "direction": "SELL", "entry": 1.1000, "sl": 1.1020,       # 20 pips
           "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850,
           "candle_time": "2026-09-21 12:00:00", "execution_status": ""}
    doc.update(over)
    return store.create("signals", doc)


def _goals(store, **over):
    g = {"userId": "u1", "account_balance": 10000, "risk_per_trade_pct": 1.0,
         "execution_enabled": True, "mode": "manual",
         "execution_mode": "manual", "mt5_connector_seen": time.time()}
    g.update(over)
    store.create("agent_goals", g)


# ============================ C-1 ===========================================
def test_manual_path_caps_oversized_lot(env):
    """20-pip SL, $10k balance, 1% risk -> 5.0 lots computed; capped at max."""
    store, sent = env
    _goals(store, account_balance=100000)
    d = _sig(store)
    assert X.calc_lot("EURUSD", 1.1000, 1.1020, 100000, 1.0) == 5.0  # oversized
    X.execute_signal(store.get("signals", d["id"]), "u1")
    cmds = store.list("exec_commands", limit=10)
    assert len(cmds) == 1                             # queued for the PC
    assert cmds[0]["payload"]["lots"] == X.MAX_LOTS   # capped, not 5.0
    doc = store.get("signals", d["id"])
    assert doc["execution_status"] == "QUEUED_PC"
    assert doc["mt5_volume"] == X.MAX_LOTS


def test_manual_path_keeps_normal_lot(env):
    """A normal-sized lot must pass through uncapped (only the max is added)."""
    store, sent = env
    _goals(store, account_balance=1000)               # 1% of $1k, 20 pips -> 0.05
    d = _sig(store)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    cmds = store.list("exec_commands", limit=10)
    assert cmds[0]["payload"]["lots"] == 0.05


# ============================ C-2 ===========================================
def test_mode_off_stamps_advisory_status(env):
    store, sent = env
    _goals(store, execution_mode="off", mode="off")
    d = _sig(store)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    doc = store.get("signals", d["id"])
    assert doc["execution_status"] == "SKIPPED_ADVISORY_MODE"
    assert "advisory" in (doc.get("mt5_note") or "").lower()


def test_kill_switch_stamps_status(env):
    store, sent = env
    _goals(store, execution_enabled=False)
    d = _sig(store)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    doc = store.get("signals", d["id"])
    assert doc["execution_status"] == "SKIPPED_KILL_SWITCH"
    assert "kill switch" in (doc.get("mt5_note") or "").lower()


def test_exec_daily_cap_stamps_status(env, monkeypatch):
    store, sent = env
    _goals(store)
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 0)  # 0 >= 0
    d = _sig(store)
    X.execute_signal(store.get("signals", d["id"]), "u1")
    doc = store.get("signals", d["id"])
    assert doc["execution_status"] == "SKIPPED_EXEC_DAILY_CAP"
    assert "cap" in (doc.get("mt5_note") or "").lower()


# ============================ C-3 ===========================================
class CandleProvider:
    """One candle per get_candles call (the tracker reads the last closed)."""
    def __init__(self, candles):
        self.candles = list(candles)

    def get_candles(self, market, tf, limit=3):
        if not self.candles:
            return None
        row = self.candles.pop(0)
        idx = pd.DatetimeIndex([pd.Timestamp(row.pop("ts"))])
        return pd.DataFrame([row], index=idx)


def _mk_store(monkeypatch, tmp_path):
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    return store


def _open_sig(store, direction="BUY", entry=100.0, sl=99.0,
              tps=(101.0, 102.0, 103.0), risk=1.0, first_ts=1800000000):
    return store.create("signals", {
        "userId": "uX", "signal_id": "SIG-MFE-001", "strategy_id": "s",
        "strategy_name": "S", "market": "XAUUSD", "timeframe": "15M",
        "direction": direction, "entry": entry, "sl": sl, "risk": risk,
        "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
        # one bar before the first candle so the tracker's expiry window is valid
        "candle_time": pd.Timestamp(first_ts - 900, unit="s").isoformat(),
        "status": "ACTIVE", "completed": False, "tp_hits": 0,
        "params": {"expire_bars": 200}})


def _candle(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def test_mfe_mae_buy_full_lifecycle(monkeypatch, tmp_path):
    store = _mk_store(monkeypatch, tmp_path)
    sig = _open_sig(store, "BUY")
    # candles: +0.5R/-0.2R, TP1 (partial stamp), +3.4R & TP2 (one event per
    # candle by design), then TP3 completes on the 4th candle
    candles = [_candle(1800000000, 100, 100.5, 99.8, 100.2),
               _candle(1800000900, 100.2, 101.2, 99.9, 101.1),
               _candle(1800001800, 101.1, 103.4, 99.7, 103.2),
               _candle(1800002700, 103.2, 103.3, 100.1, 103.0)]
    from app.engine.tracker import SignalTracker
    t = SignalTracker(CandleProvider(candles))
    for _ in range(4):
        t.update_market("XAUUSD")
    doc = store.get("signals", sig["id"])
    assert doc["completed"] is True and doc["status"].startswith("TP")
    assert doc["mfe_r"] == pytest.approx(3.4, abs=0.001)   # high 103.4 - 100
    assert doc["mae_r"] == pytest.approx(-0.3, abs=0.001)  # 100 - low 99.7


def test_mfe_mae_sell_adverse_then_sl(monkeypatch, tmp_path):
    store = _mk_store(monkeypatch, tmp_path)
    sig = _open_sig(store, "SELL", entry=100.0, sl=101.0, tps=(99.0, 98.0, 97.0))
    # SELL: favorable = entry - low; adverse = high - entry
    candles = [_candle(1800000000, 100, 100.4, 99.5, 99.6),   # +0.5R / -0.4R
               _candle(1800000900, 99.6, 101.2, 99.4, 101.0)] # SL hit -> LOSS
    from app.engine.tracker import SignalTracker
    t = SignalTracker(CandleProvider(candles))
    for _ in range(2):
        t.update_market("XAUUSD")
    doc = store.get("signals", sig["id"])
    assert doc["completed"] is True and doc["status"] == "SL_HIT"
    assert doc["outcome"] == "LOSS"
    assert doc["mfe_r"] == pytest.approx(0.6, abs=0.001)   # candle 2 low 99.4
    assert doc["mae_r"] == pytest.approx(-1.2, abs=0.001)  # high 101.2 - entry 100


def test_mfe_mae_persisted_on_partial_tp(monkeypatch, tmp_path):
    """Running values are stamped on the TP-hit doc update (no extra writes)."""
    store = _mk_store(monkeypatch, tmp_path)
    sig = _open_sig(store, "BUY")
    candles = [_candle(1800000000, 100, 100.5, 99.8, 100.2),
               _candle(1800000900, 100.2, 101.2, 99.9, 101.1)]  # TP1 only
    from app.engine.tracker import SignalTracker
    t = SignalTracker(CandleProvider(candles))
    for _ in range(2):
        t.update_market("XAUUSD")
    doc = store.get("signals", sig["id"])
    assert doc["status"] == "TP1_HIT" and doc["completed"] is False
    assert doc["mfe_r"] == pytest.approx(1.2, abs=0.001)
    assert doc["mae_r"] == pytest.approx(-0.2, abs=0.001)
