"""Indicator primitive tests (SPEC §58: ATR calculation, EMA 9/21 crossover)."""
import numpy as np
import pandas as pd

from app.core.indicators import atr, crossover, crossunder, ema, highest, trend_series


def test_ema_converges_to_constant():
    s = pd.Series([100.0] * 100)
    assert abs(ema(s, 9).iloc[-1] - 100.0) < 1e-9


def test_ema_direction():
    up = pd.Series(np.linspace(1, 200, 300))
    dn = pd.Series(np.linspace(200, 1, 300))
    assert ema(up, 9).iloc[-1] > ema(up, 21).iloc[-1]
    assert ema(dn, 9).iloc[-1] < ema(dn, 21).iloc[-1]


def test_atr_positive_and_wilder_smoothed():
    rng = np.random.default_rng(7)
    n = 500
    close = 100 + np.cumsum(rng.standard_normal(n))
    df = pd.DataFrame({
        "open": close, "close": close,
        "high": close + np.abs(rng.standard_normal(n)),
        "low": close - np.abs(rng.standard_normal(n)),
    }, index=pd.date_range("2026-01-01", periods=n, freq="15min"))
    a = atr(df, 14).dropna()
    assert len(a) > 0 and (a > 0).all()
    # Wilder smoothing: ATR(14) should react less than raw TR extremes
    tr_manual = pd.concat([df["high"] - df["low"],
                           (df["high"] - df["close"].shift(1)).abs(),
                           (df["low"] - df["close"].shift(1)).abs()], axis=1).max(axis=1)
    assert a.iloc[-1] < tr_manual.max()


def test_crossover_crossunder_exact():
    a = pd.Series([1, 1, 3, 3, 1, 1.0])
    b = pd.Series([2, 2, 2, 2, 2, 2.0])
    up = crossover(a, b)
    dn = crossunder(a, b)
    assert list(up) == [False, False, True, False, False, False]
    assert list(dn) == [False, False, False, False, True, False]


def test_highest_rolling_max():
    s = pd.Series([1, 5, 3, 8, 2.0])
    assert list(highest(s, 2).dropna()) == [5.0, 5.0, 8.0, 8.0]


def test_trend_state_machine():
    close = pd.Series([10, 11, 12, 8, 7, 12.0])
    upper = pd.Series([11.0] * 6)
    lower = pd.Series([9.0] * 6)
    t = trend_series(close, upper, lower)
    # i=1: close=11 is NOT strictly above upper=11 -> still 0
    # i=2: close=12 crosses above -> +1;  i=3: close=8 crosses below -> -1
    # i=4: stays -1;  i=5: close=12 crosses above -> +1
    assert list(t) == [0, 0, 1, -1, -1, 1]
