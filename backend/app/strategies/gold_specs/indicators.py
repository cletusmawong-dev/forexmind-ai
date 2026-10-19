"""Self-contained indicators + candle hygiene for the FOREXMIND gold specs.

Every function works on COMPLETED candles only (the strategies never see a
forming bar) and on UTC timestamps (spec rule 3: never mix UTC with broker
time - the provider feed is UTC).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


def prepare(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Completed, chronological, de-duplicated, UTC-indexed OHLC frame."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return None
    d = df[list(need)].dropna().copy()
    try:
        idx = pd.to_datetime(d.index, utc=True)
    except Exception:
        return None
    d.index = idx
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d["open"] = d["open"].astype(float)
    d["high"] = d["high"].astype(float)
    d["low"] = d["low"].astype(float)
    d["close"] = d["close"].astype(float)
    return d if len(d) else None


def ema(series: pd.Series, period: int) -> pd.Series:
    """Standard EMA (recursive, seeded with the first close) - matches
    Pine's ta.ema semantics."""
    return series.ewm(span=period, adjust=False).mean()


def wilder_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Wilder ATR at the LAST completed bar (SMA seed + Wilder recursion)."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)
    if n < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, n):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    atr = sum(trs[:period]) / period                    # SMA seed
    for tr in trs[period:]:
        atr = atr + (tr - atr) / period                 # Wilder recursion
    return atr


def wilder_adx(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Wilder ADX at the LAST completed bar (exact recursion: SMA-seeded
    smoothed TR/+DM/-DM, DX, then ADX seeded with the first `period` DX
    values and smoothed with (period-1)/period weight). A flat market (0/0) is
    zero directional movement -> DX 0, never a crash."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)
    if n < 2 * period + 1:
        return None
    trs: List[float] = []
    plus: List[float] = []
    minus: List[float] = []
    for i in range(1, n):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        up = h[i] - h[i - 1]
        dn = l[i - 1] - l[i]
        plus.append(up if (up > dn and up > 0) else 0.0)
        minus.append(dn if (dn > up and dn > 0) else 0.0)
    atr = sum(trs[:period]) / period
    pdm = sum(plus[:period]) / period
    ndm = sum(minus[:period]) / period
    dxs: List[float] = []
    for i in range(period, n - 1):   # trs/plus/minus span bars 1..n-1
        atr = atr + (trs[i] - atr) / period
        pdm = pdm + (plus[i] - pdm) / period
        ndm = ndm + (minus[i] - ndm) / period
        pdi = 100.0 * pdm / atr if atr > 0 else 0.0
        ndi = 100.0 * ndm / atr if atr > 0 else 0.0
        den = pdi + ndi
        dxs.append(100.0 * abs(pdi - ndi) / den if den > 0 else 0.0)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def result(status: str, strategy_id: str, checks: List[Dict[str, Any]],
           summary: str, **kw) -> Dict[str, Any]:
    """Uniform engine result. `checks` carries EVERY condition with
    ok/fail + detail (spec rule 6: log exactly what passed or failed)."""
    out: Dict[str, Any] = {"strategy_id": strategy_id, "status": status,
                           "summary": summary, "checks": checks}
    out.update(kw)
    return out
