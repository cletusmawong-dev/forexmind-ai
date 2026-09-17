"""Prop-firm account rules (user request 2026-09-17).

The signal engine must enforce prop rules as HARD limits on signal
generation: daily drawdown (with safety buffer) and max total drawdown.
Personal accounts keep the existing max_daily_loss_pct behaviour.
Deterministic: crafted candles, fresh store, no network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.engine import signal_engine as SE


def _cross_at_last_bar() -> pd.DataFrame:
    """15M frame whose last bar triggers the S2 9/21 cross (same recipe as
    test_strategy_selection)."""
    n = 400
    idx = pd.date_range("2026-01-01 00:00", periods=n, freq="15min")
    close = []
    price = 2000.0
    for i in range(n):
        price += -4.0 if i < n - 8 else 3.0  # down leg, then reversal into the last bars
        close.append(price)
    df = pd.DataFrame(
        {"open": [c - 1 for c in close], "high": [c + 2 for c in close],
         "low": [c - 2 for c in close], "close": close, "volume": [100.0] * n},
        index=idx)
    df.index.name = "timestamp"
    return df


class P:
    def get_candles(self, market, tf, limit=600):
        return _cross_at_last_bar()

    def higher_frames(self, market, tf):
        return {}


@pytest.fixture()
def prop_env(fresh_store, monkeypatch):
    from app.agent.core import ensure_user_docs
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    ensure_user_docs("demo-user")
    monkeypatch.setattr(SE, "get_store", lambda: fresh_store)
    monkeypatch.setattr(SE, "notify", lambda *a, **k: None)
    store = fresh_store
    engine = SE.SignalEngine(P())

    def set_risk(**kw):
        doc = store.list("settings", filters={"userId": "demo-user", "kind": "risk"}, limit=1)[0]
        store.update("settings", doc["id"], kw)

    def close_signal(r: float, day: str | None = None):
        store.create("signals", {
            "userId": "demo-user", "day": day or pd.Timestamp.utcnow().strftime("%Y-%m-%d"), "completed": True,
            "r_multiple": r, "outcome": "WIN" if r > 0 else "LOSS",
            "strategy_id": "strategy_2_ema_atr", "status": "TP_HIT" if r > 0 else "SL_HIT",
            "market": "XAUUSD", "timeframe": "15M",
        })
    return SimpleNamespace(store=store, engine=engine, set_risk=set_risk, close_signal=close_signal)


def _candidate():
    return SimpleNamespace(market="XAUUSD", strategy_id="strategy_2_ema_atr",
                           rr_primary=2.0, session="London")


def test_personal_account_unchanged(prop_env):
    """No prop fields set -> existing single daily-loss guard applies."""
    env = prop_env
    env.set_risk(account_type="personal", prop_rules=None, max_daily_loss_pct=3.0)
    for _ in range(4):
        env.close_signal(-1.0)  # -4% today -> blocks at 3% personal limit
    ok, reason = env.engine._guards("demo-user", _candidate(), "London")
    assert not ok and reason == "max daily loss reached"


def test_prop_daily_buffer_blocks_before_the_rule_breaks(prop_env):
    """Prop 5% daily DD with default 20% buffer -> wall at 4%. -4% today blocks."""
    env = prop_env
    env.set_risk(account_type="propfirm", prop_rules={"daily_drawdown_pct": 5.0,
                                                      "max_total_drawdown_pct": 10.0,
                                                      "daily_dd_buffer_pct": 20.0},
                 max_daily_loss_pct=50.0)  # personal limit loose; prop must bind
    for _ in range(4):
        env.close_signal(-1.0)  # -4% -> at the buffer wall
    ok, reason = env.engine._guards("demo-user", _candidate(), "London")
    assert not ok and reason == "prop daily drawdown buffer reached"


def test_prop_room_left_allows_signals(prop_env):
    env = prop_env
    env.set_risk(account_type="propfirm", prop_rules={"daily_drawdown_pct": 5.0,
                                                      "daily_dd_buffer_pct": 20.0},
                 max_daily_loss_pct=50.0)
    env.close_signal(-1.0)  # -1% today, buffer wall at 4% -> room left
    ok, reason = env.engine._guards("demo-user", _candidate(), "London")
    assert ok and reason == ""


def test_prop_total_drawdown_stop(prop_env):
    """Overall drawdown beyond (10% - buffer) stops signal generation."""
    env = prop_env
    env.set_risk(account_type="propfirm", prop_rules={"daily_drawdown_pct": 5.0,
                                                      "max_total_drawdown_pct": 10.0,
                                                      "daily_dd_buffer_pct": 20.0},
                 max_daily_loss_pct=50.0)
    for _ in range(4):
        env.close_signal(-1.0, day="2026-01-10")  # past days only - today stays clean
    for _ in range(4):
        env.close_signal(-1.0, day="2026-01-11")
    ok, reason = env.engine._guards("demo-user", _candidate(), "London")
    assert not ok and reason == "prop max drawdown buffer reached"


def test_patch_risk_validates_account_fields(prop_env):
    from app.agent.core import get_risk, patch_risk
    env = prop_env
    out = patch_risk("demo-user", {"account_type": "propfirm",
                                   "prop_rules": {"daily_drawdown_pct": 4.0,
                                                  "profit_target_pct": 8.0}})
    assert out["account_type"] == "propfirm"
    assert out["prop_rules"]["daily_drawdown_pct"] == 4.0
    got = get_risk("demo-user")
    assert got["account_type"] == "propfirm" and got["prop_rules"]["profit_target_pct"] == 8.0
    for bad in ({"account_type": "banana"},
                {"prop_rules": {"daily_drawdown_pct": 0}},
                {"prop_rules": {"daily_drawdown_pct": 99}},
                {"prop_rules": "five"}):
        with pytest.raises(ValueError):
            patch_risk("demo-user", bad)
