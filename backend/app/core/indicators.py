"""Technical indicator primitives (pandas-based).

These mirror Pine Script semantics closely enough for faithful strategy
translations:  EMA = ewm(span), ATR = Wilder RMA of True Range,
`highest(x, n)` = rolling max, crossover/crossunder = strict bar-to-bar cross.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=int(length), adjust=False).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (Pine ta.rma), used by ATR."""
    return series.ewm(alpha=1.0 / int(length), adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return rma(true_range(df), int(length))


def highest(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(int(length), min_periods=int(length)).max()


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """True on the bar where a crosses ABOVE b (prev a <= prev b, now a > b)."""
    a = pd.Series(a); b = pd.Series(b)
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """True on the bar where a crosses BELOW b (prev a >= prev b, now a < b)."""
    a = pd.Series(a); b = pd.Series(b)
    return (a < b) & (a.shift(1) >= b.shift(1))


def trend_series(close: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """State machine: +1 after close crosses above `upper`,
    -1 after close crosses below `lower`, otherwise carries previous state."""
    n = len(close)
    out = np.zeros(n, dtype=int)
    ca = (close > upper) & (close.shift(1) <= upper)
    cb = (close < lower) & (close.shift(1) >= lower)
    state = 0
    vals = close.to_numpy()
    ca_a, cb_a = ca.to_numpy(), cb.to_numpy()
    up_a, lo_a = upper.to_numpy(), lower.to_numpy()
    for i in range(n):
        if not np.isnan(up_a[i]):
            if ca_a[i]:
                state = 1
            elif cb_a[i]:
                state = -1
        out[i] = state
    return pd.Series(out, index=close.index)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule).agg(agg).dropna()
    return out


TF_RULES = {"5M": "5min", "15M": "15min", "1H": "1h", "4H": "4h", "1D": "1D"}


def pct_change(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / abs(b) * 100.0
