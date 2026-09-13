"""Lifecycle + learning tests (SPEC §17, §18, §25-§27, §58):
signal lifecycle, trade tracking, hypothesis creation, approval, rejection,
version creation, rollback, lessons.
"""
from app.engine.tracker import SignalTracker
from app.learning.experiments import ExperimentEngine
from app.learning.hypotheses import create_hypothesis
from app.learning.metrics import classify_experiment, compute_metrics
from app.learning.versions import (active_params, active_version, approve_hypothesis,
                                   ensure_strategy_docs, reject_hypothesis, rollback)
from app.strategies import get_strategy
import pandas as pd


def _mk_sig(**kw):
    base = {
        "id": "sig-test-1", "userId": "demo-user", "signal_id": "SIG-TEST-001",
        "strategy_id": "strategy_2_ema_atr", "strategy_name": "9/21 EMA",
        "market": "XAUUSD", "timeframe": "15M", "direction": "BUY",
        "entry": 100.0, "sl": 98.5, "tp1": 101.5, "tp2": 103.0, "tp3": 104.5,
        "risk": 1.5, "status": "ACTIVE", "tp_hits": 0, "r_multiple": 0.0,
        "outcome": None, "completed": False, "candle_time": "2026-09-10 09:00:00+00:00",
        "market_conditions": {"session": "London", "volatility_regime": "normal"},
        "checks": [], "params": {"expire_bars": 200},
    }
    base.update(kw)
    return base


def _candle(high, low, ts="2026-09-10 09:15:00+00:00"):
    return pd.Series({"high": high, "low": low, "close": (high + low) / 2}, name=pd.Timestamp(ts))


def test_tp1_then_tp2_tracking(fresh_store):
    fresh_store.create("signals", _mk_sig())
    tracker = SignalTracker(provider=None)
    sig = fresh_store.get("signals", "sig-test-1")

    tracker._update_signal(sig, _candle(high=101.6, low=99.0))     # touches TP1
    sig = fresh_store.get("signals", "sig-test-1")
    assert sig["status"] == "TP1_HIT" and sig["tp_hits"] == 1
    assert abs(sig["r_multiple"] - 1.0) < 1e-6

    tracker._update_signal(sig, _candle(high=103.2, low=100.5, ts="2026-09-10 09:30:00+00:00"))
    sig = fresh_store.get("signals", "sig-test-1")
    assert sig["status"] == "TP2_HIT" and sig["tp_hits"] == 2
    assert sig["outcome"] == "WIN" and abs(sig["r_multiple"] - 2.0) < 1e-6

    # TP3 closes the trade (SPEC 11: TP1/TP2/TP3/SL all tracked)
    tracker._update_signal(sig, _candle(high=104.6, low=101.0, ts="2026-09-10 09:45:00+00:00"))
    sig = fresh_store.get("signals", "sig-test-1")
    assert sig["completed"] and sig["status"] == "TP3_HIT"
    assert abs(sig["r_multiple"] - 3.0) < 1e-6


def test_sl_before_tp_is_loss(fresh_store):
    fresh_store.create("signals", _mk_sig())
    tracker = SignalTracker(provider=None)
    sig = fresh_store.get("signals", "sig-test-1")
    # candle touches BOTH sl (98.5) and tp1 (101.5) -> conservative: SL first
    tracker._update_signal(sig, _candle(high=102.0, low=98.4))
    sig = fresh_store.get("signals", "sig-test-1")
    assert sig["status"] == "SL_HIT" and sig["outcome"] == "LOSS"
    assert sig["r_multiple"] == -1.0


def test_sl_after_tp1_banks_profit(fresh_store):
    fresh_store.create("signals", _mk_sig(tp_hits=1, status="TP1_HIT", r_multiple=1.0))
    tracker = SignalTracker(provider=None)
    sig = fresh_store.get("signals", "sig-test-1")
    tracker._update_signal(sig, _candle(high=101.0, low=98.4, ts="2026-09-10 09:45:00+00:00"))
    sig = fresh_store.get("signals", "sig-test-1")
    assert sig["status"] == "SL_HIT"
    assert sig["outcome"] == "WIN" and abs(sig["r_multiple"] - 1.0) < 1e-6


def test_signal_result_separate_from_user_action(fresh_store):
    fresh_store.create("signals", _mk_sig(user_action="skipped"))
    tracker = SignalTracker(provider=None)
    sig = fresh_store.get("signals", "sig-test-1")
    tracker._update_signal(sig, _candle(high=104.6, low=99.4))  # reaches TP1 (one TP per candle)
    sig = fresh_store.get("signals", "sig-test-1")
    # signal is winning even though the user skipped it - tracked separately
    assert sig["user_action"] == "skipped"
    assert sig["status"] == "TP1_HIT" and sig["tp_hits"] == 1
    assert sig.get("user_trade_result") is None


def test_metrics_and_classification():
    trades = [{"r_multiple": 2.0, "tp_hits": 2, "status": "TP2_HIT", "duration_bars": 10,
               "entry_time": "2026-09-01 10:00:00", "market": "XAUUSD",
               "timeframe": "15M", "session": "London"},
              {"r_multiple": -1.0, "tp_hits": 0, "status": "SL_HIT", "duration_bars": 5,
               "entry_time": "2026-09-01 11:00:00", "market": "XAUUSD",
               "timeframe": "15M", "session": "London"}] * 20
    m = compute_metrics(trades)
    assert m["trades"] == 40
    assert m["win_rate"] == 50.0
    assert m["tp2_hit_rate"] == 50.0
    assert abs(m["expectancy"] - 0.5) < 1e-9

    verdict = classify_experiment(m, m, min_trades=30)
    assert verdict["result"] == "NO_SIGNIFICANT_CHANGE"
    small = compute_metrics(trades[:10])
    assert classify_experiment(m, small, min_trades=30)["result"] == "INSUFFICIENT_DATA"
    better = dict(m, win_rate=m["win_rate"] + 6, expectancy=m["expectancy"] + 0.2)
    assert classify_experiment(m, better, min_trades=30)["result"] == "IMPROVED"
    worse = dict(m, win_rate=m["win_rate"] - 10, expectancy=m["expectancy"] - 0.4)
    assert classify_experiment(m, worse, min_trades=30)["result"] == "WORSE"


def test_hypothesis_approval_creates_version(fresh_store):
    ensure_strategy_docs()
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "fast_len", 10,
                          reason="test hypothesis", expected_effect="fewer signals")
    assert h["old_value"] == 9 and h["new_value"] == 10
    assert h["hypothesis_id"].startswith("HYP-")

    v = approve_hypothesis("demo-user", h["id"])
    assert v["version"] == "1.1"
    assert v["active"] is True
    assert v["params"]["fast_len"] == 10
    assert active_version("strategy_2_ema_atr") == "1.1"
    assert active_params("strategy_2_ema_atr")["fast_len"] == 10
    # only ONE variable changed vs v1.0
    changes = v["changes"]
    assert len(changes) == 1 and changes[0]["variable"] == "fast_len"


def test_rejection_does_not_change_strategy(fresh_store):
    ensure_strategy_docs()
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "sl_mult", 1.8,
                          reason="test", expected_effect="test")
    rejected = reject_hypothesis("demo-user", h["id"])
    assert rejected["status"] == "REJECTED"
    assert active_params("strategy_2_ema_atr")["sl_mult"] == 1.5
    assert active_version("strategy_2_ema_atr") == "1.0"


def test_double_decision_blocked(fresh_store):
    ensure_strategy_docs()
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "fast_len", 11,
                          reason="t", expected_effect="t")
    approve_hypothesis("demo-user", h["id"])
    import pytest
    from fastapi import HTTPException
    with pytest.raises(ValueError):
        reject_hypothesis("demo-user", h["id"])


def test_rollback_restores_previous_and_keeps_history(fresh_store):
    ensure_strategy_docs()
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "sl_mult", 2.0,
                          reason="t", expected_effect="t")
    approve_hypothesis("demo-user", h["id"])
    assert active_version("strategy_2_ema_atr") == "1.1"

    target = rollback("strategy_2_ema_atr")
    assert target["version"] == "1.0"
    assert active_version("strategy_2_ema_atr") == "1.0"
    assert active_params("strategy_2_ema_atr")["sl_mult"] == 1.5
    # history retained: both versions still exist
    versions = fresh_store.list("strategy_versions",
                                filters={"strategy_id": "strategy_2_ema_atr"})
    assert {v["version"] for v in versions} == {"1.0", "1.1"}


def test_experiment_run_from_hypothesis_same_dataset(fresh_store, xau_15m, monkeypatch):
    """Full experiment: original vs experimental on the SAME dataset."""
    class FakeProvider:
        is_demo = True
        name = "test"
        def get_candles(self, market, tf, limit=4000):
            return xau_15m.copy()

    ensure_strategy_docs()
    engine = ExperimentEngine(FakeProvider())
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "fast_len", 10,
                          reason="t", expected_effect="t",
                          dataset={"market": "XAUUSD", "timeframe": "15M"})
    h["entry_ok"] = True
    exp = engine.run_from_hypothesis("demo-user", fresh_store.get("hypotheses", h["id"]))
    assert exp["variable"] == "fast_len"
    assert exp["old_value"] == 9 and exp["new_value"] == 10
    assert exp["result"] in ("IMPROVED", "NO_SIGNIFICANT_CHANGE", "WORSE",
                             "INSUFFICIENT_DATA")
    # strategy params untouched by a mere experiment (SPEC §41)
    assert active_params("strategy_2_ema_atr")["fast_len"] == 9
