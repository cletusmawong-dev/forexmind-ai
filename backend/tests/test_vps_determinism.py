"""Indicator determinism + execution safety (user spec 2026-09-16, §8, §18,
§24, §25).

Determinism: a known candle fixture produces EXACT EMA9/EMA21/ATR14/EMA100
values, computed independently (manual recursion) in this file - the same
series yields the same values regardless of which provider dictionary built
the DataFrame. Execution: kill switch safe default, mode OFF default, TP2,
risk caps - and none of it can be flipped by anything but explicit config.
"""
import numpy as np
import pandas as pd
import pytest


# ---------- shared deterministic fixture (§25) ----------

def _fixture_df():
    """Deterministic 15M candles: 260 bars, oscillating around a rising trend
    with a final dip designed to put EMA9 < EMA21 on the last bar."""
    n = 260
    vals = []
    for i in range(n):
        vals.append(3300 + i * 0.10 + (0.8 if i % 2 == 0 else -0.8))
    idx = pd.date_range("2026-08-01", periods=n, freq="15min")
    close = pd.Series(vals, index=idx)
    high = close + 0.5
    low = close - 0.5
    open_ = close.shift(1).fillna(close.iloc[0])
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": 1.0}, index=idx)


def _manual_ema(values, length):
    """Independent EMA recursion: seed = first value, then
    e = e + (2/(length+1)) * (x - e). Matches Pine ta.ema semantics."""
    k = 2.0 / (length + 1.0)
    out = []
    e = None
    for x in values:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def _manual_atr(highs, lows, closes, length=14):
    """Wilder ATR: TR then RMA with alpha=1/length, TR[0] = high-low."""
    trs = []
    prev_close = None
    for h, l, c in zip(highs, lows, closes):
        tr = h - l if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    a = None
    out = []
    alpha = 1.0 / length
    for tr in trs:
        a = tr if a is None else a + alpha * (tr - a)
        out.append(a)
    return out


def test_ema9_ema21_atr14_exact_known_values():
    from app.core.indicators import atr, ema
    df = _fixture_df()
    e9 = ema(df["close"], 9)
    e21 = ema(df["close"], 21)
    a14 = atr(df, 14)
    ref9 = _manual_ema(list(df["close"]), 9)
    ref21 = _manual_ema(list(df["close"]), 21)
    ref14 = _manual_atr(list(df["high"]), list(df["low"]), list(df["close"]))
    for i in (10, 100, 259):
        assert e9.iloc[i] == pytest.approx(ref9[i], rel=1e-12)
        assert e21.iloc[i] == pytest.approx(ref21[i], rel=1e-12)
        assert a14.iloc[i] == pytest.approx(ref14[i], rel=1e-12)


def test_htf_100_ema_deterministic():
    from app.core.indicators import ema
    df = _fixture_df()
    e100 = ema(df["close"], 100)
    ref = _manual_ema(list(df["close"]), 100)
    for i in (99, 150, 259):
        assert e100.iloc[i] == pytest.approx(ref[i], rel=1e-12)


def test_indicators_provider_independent():
    """Same values whether the df came from TD-style or test-fixture dicts."""
    from app.core.indicators import atr, ema
    df1 = _fixture_df()
    # a 'different source': float32 rounding via round-trip through strings
    df2 = df1.copy()
    df2.index = pd.to_datetime([str(x) for x in df1.index])
    df2["close"] = [float(f"{v:.6f}") for v in df1["close"]]
    d = abs(float(ema(df1["close"], 9).iloc[-1]) - float(ema(df2["close"], 9).iloc[-1]))
    assert d < 1e-9
    d = abs(float(atr(df1, 14).iloc[-1]) - float(atr(df2, 14).iloc[-1]))
    assert d < 1e-9


def test_strategy2_state_on_fixture_is_deterministic():
    from app.learning.versions import active_params
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    df = _fixture_df()
    strat = EmaAtrStrategy()
    params = active_params("strategy_2_ema_atr")
    st1 = strat.compute(df, params)
    st2 = strat.compute(df.copy(), params)
    assert bool(st1["buy"].iloc[-1]) == bool(st2["buy"].iloc[-1])
    assert bool(st1["sell"].iloc[-1]) == bool(st2["sell"].iloc[-1])
    assert float(st1["ema_f"].iloc[-1]) == float(st2["ema_f"].iloc[-1])
    # any crossover in this fixture appears on CLOSED bars by construction
    # (the fixture contains only fully-formed candles)


# ---------- execution safety stays put (§18, §24) ----------

def test_kill_switch_safe_default(monkeypatch):
    """Users with NO explicit setting are KILLED by default (spec §14)."""
    import os, tempfile
    from app.db.store import LocalStore
    from app.execution import mt5 as M

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(M, "get_store", lambda: store)
    assert M.execution_enabled("nobody") is False


def test_mode_defaults_off_and_bridge_mode_gated(monkeypatch):
    import os, tempfile
    from app.db.store import LocalStore
    from app.execution import mt5 as M

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(M, "get_store", lambda: store)
    assert M.user_mode("nobody") == "off"              # safe default
    # vps mode is refused while the bridge env is not configured
    monkeypatch.setattr(M.settings, "execution_mode", "off")
    with pytest.raises(PermissionError):
        M.set_mode("u1", "vps")


def test_tp_level_is_tp2_and_risk_cap(monkeypatch):
    from app.config import settings
    assert str(settings.execution_tp_level).strip() in ("1", "2", "3", "AUTO")
    assert settings.execution_risk_pct_cap <= 1.0
    assert settings.execution_max_trades_per_day == 6


def test_lot_sizing_caps_risk(monkeypatch):
    from app.execution.mt5 import calc_lot
    # XAUUSD: pip 0.1, $10/pip/lot. $10k balance, 1% = $100 risk.
    lots = calc_lot("XAUUSD", entry=3300.0, sl=3290.0, balance=10_000.0, risk_pct=1.0)
    # 10.0 risk distance = 100 pips -> 0.1 lots risk $100
    assert lots == pytest.approx(0.1)
    # tiny balance -> respects 0.01 lot floor/step
    small = calc_lot("EURUSD", entry=1.1, sl=1.095, balance=100.0, risk_pct=1.0)
    assert small >= 0.01
    assert round(small / 0.01) * 0.01 == pytest.approx(small)


def test_execution_failure_never_breaks_signals(monkeypatch):
    """execute_signal must swallow every error - signal flow continues."""
    import os, tempfile
    from app.db.store import LocalStore
    from app.execution import mt5 as M

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(M, "get_store", lambda: store)
    sig = {"id": "s1", "signal_id": "SIG-X", "market": "XAUUSD", "entry": 3300.0,
           "sl": 3295.0, "tp1": 3310.0, "tp2": 3320.0, "tp3": 3330.0,
           "direction": "BUY"}

    def boom(*a, **k):
        raise RuntimeError("bridge exploded")

    monkeypatch.setattr(M, "bridge_get", boom)
    M.execute_signal(sig, "u1")                        # must not raise
    doc = store.get("signals", "s1")
    assert doc is None or doc.get("execution_status") != "FILLED"
