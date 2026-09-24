"""Candle gap auto-repair - splice missing bars from the alternate feed.

INCIDENT (2026-09-23/24): the MT5 bridge feed had a 45-minute hole; the
signal engine correctly refuses to generate signals over a gapped series,
but nothing ever repaired the hole - XAUUSD signal generation stayed
paused for ~5 hours and every 9/21 EMA crossover inside the gap was lost
(user report: "the app is missing signs formed by the EMA strategy").

The deployment has TWO independent candle feeds (MT5 bridge and the
Twelve Data chain). They rarely share the same hole, so at gap-detection
time the missing bars are now spliced in from the alternate feed and the
integrity gate re-checked. Honesty preserved: bars only ever come from a
real second source - never interpolated, never fabricated. If the
alternate feed cannot cover the gap, the caller still pauses loudly
(historical stream backfill stays in backfill.py - different concern).
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

from .integrity import detect_gaps


def _default_fetch(market: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    """The feed that is NOT the primary one."""
    from ..config import settings
    from .live_provider import LiveProvider
    p = LiveProvider()
    if settings.bridge_url:          # primary = MT5 terminal -> repair via TD chain
        return p._fetch_oanda(market, timeframe, limit)
    return p._fetch_mt5(market, timeframe, limit)


def backfill_gaps(df: pd.DataFrame, market: str, timeframe: str,
                  fetch_alt=None) -> Tuple[pd.DataFrame, int]:
    """Splice missing in-window candles from the alternate feed.

    Returns (repaired_df, bars_added). fetch_alt is injectable for tests.
    Never raises - any failure returns the original df unchanged (the
    caller's integrity gate then keeps pausing honestly)."""
    try:
        stamps = [int(pd.Timestamp(x).timestamp()) for x in df.index]
        gaps = detect_gaps(stamps, timeframe, "gap_repair")
        if not gaps:
            return df, 0
        fetch = fetch_alt or _default_fetch
        step = {"1M": 60, "5M": 300, "15M": 900, "1H": 3600,
                "4H": 14400, "1D": 86400}.get(timeframe, 900)
        span = max(g["missing_to"] for g in gaps) - \
            min(g["missing_from"] for g in gaps)
        alt = fetch(market, timeframe, max(300, span // step + 300))
        if alt is None or len(alt) == 0:
            return df, 0
        have = set(stamps)
        rows = []
        for g in gaps:
            for ts in range(g["missing_from"], g["missing_to"] + 1, step):
                if ts in have:
                    continue
                idx = pd.Timestamp(ts, unit="s")
                if idx in alt.index:
                    rows.append(alt.loc[idx])
        if not rows:
            return df, 0
        patch = pd.DataFrame(rows)
        merged = pd.concat([df, patch])
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
        return merged, len(rows)
    except Exception:
        return df, 0
