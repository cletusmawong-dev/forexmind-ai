"""Strategy selection gating (user request 2026-09-16: 'select the strategy
you want to use instead of it using all the strategies').

The engine must generate signals ONLY from strategies whose doc status is
ACTIVE. PAUSED / DISABLED strategies never produce candidates. The app UI
exposes this as a one-tap Signal Engine selector (Strategies screen).
"""
import numpy as np
import pandas as pd
import pytest


def _cross_at_last_bar():
    """Frame whose LAST closed bar is a bullish 9/21 EMA cross, long enough
    to pass the engine's 300-bar data floor."""
    close = np.concatenate([4000 - np.arange(340) * 0.5,
                            3830 + np.arange(60) * 0.5])
    idx = pd.date_range("2026-08-01", periods=400, freq="15min")
    df = pd.DataFrame({"open": close, "high": close + 0.4,
                       "low": close - 0.4, "close": close,
                       "volume": 1.0}, index=idx)
    from app.core.indicators import crossover, ema
    cross_idx = int(np.argmax(crossover(ema(df["close"], 9), ema(df["close"], 21)).values))
    assert cross_idx >= 300
    return df.iloc[:cross_idx + 1]


def _engine_with(store, monkeypatch, df):
    import app.engine.signal_engine as SE

    monkeypatch.setattr(SE, "get_store", lambda: store)
    monkeypatch.setattr(SE, "notify", lambda *a, **k: None)

    class P:
        def get_candles(self, market, tf, limit=600):
            return df.copy()

        def higher_frames(self, market, tf):
            return {}

    return SE.SignalEngine(P())


@pytest.fixture()
def setup(fresh_store, monkeypatch):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    yield fresh_store, _engine_with(fresh_store, monkeypatch, _cross_at_last_bar())


def test_paused_strategy_generates_nothing(setup):
    store, engine = setup
    store.update("strategies", "strategy_2_ema_atr", {"status": "PAUSED"})
    created = engine.scan("demo-user", "XAUUSD", "15M")
    assert [c["strategy_id"] for c in created] == []


def test_active_strategy_generates_signal(setup):
    store, engine = setup
    store.update("strategies", "strategy_2_ema_atr", {"status": "ACTIVE"})
    created = engine.scan("demo-user", "XAUUSD", "15M")
    assert "strategy_2_ema_atr" in [c["strategy_id"] for c in created]
    sig = next(c for c in created if c["strategy_id"] == "strategy_2_ema_atr")
    assert sig["direction"] == "BUY"


def test_selector_states_only_selected_strategy_fires(setup):
    """Mirrors the UI selector: Strategy 2 only -> S2 signals, S1 silent."""
    store, engine = setup
    store.update("strategies", "strategy_2_ema_atr", {"status": "ACTIVE"})
    store.update("strategies", "strategy_1_zero_lag", {"status": "PAUSED"})
    created = engine.scan("demo-user", "XAUUSD", "15M")
    ids = {c["strategy_id"] for c in created}
    assert "strategy_2_ema_atr" in ids
    assert "strategy_1_zero_lag" not in ids
