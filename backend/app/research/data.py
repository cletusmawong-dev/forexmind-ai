"""RESEARCH DATA DEPTH (deploy-day fix, 2026-10-08).

The live provider's cache is tuned for scanning (~300-500 bars). That is
too little history for honest research: EMA200 warmup on 408 bars leaves
~200 testable bars and every candidate gets rejected for "no trades" -
not because the strategy failed, but because the engine was data-starved.

get_history() returns the deepest HONEST history available:
  1. the provider cache (bridge/MT5 first)
  2. paged TwelveData historical fetches BEFORE the cache window
     (bounded number of calls, free-tier friendly)
  3. whatever was honestly assembled - never padded, never invented

A short module-level TTL cache keeps the 15-minute background ticks from
re-paging the same history over and over.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

_TTL_BY_TF = {"15M": 6 * 3600, "30M": 8 * 3600, "1H": 12 * 3600,
              "4H": 24 * 3600, "1D": 48 * 3600}
_CHUNK_DAYS = {"15M": 12, "30M": 20, "1H": 40, "4H": 120, "1D": 400}

_cache: dict = {}
_lock = threading.Lock()


def _norm(chunk: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if chunk is None or len(chunk) == 0:
        return None
    out = chunk.copy()
    if "volume" not in out.columns:
        out["volume"] = 0.0
    return out


def get_history(provider, market: str, tf: str,
                target_bars: int = 1500, min_bars: int = 300,
                max_extra_calls: int = 3) -> tuple:
    """(df, note) - deepest honest history for research.

    note explains where the data came from / what was short, so the
    candidate events can state the candle count transparently.
    """
    key = (market, tf.upper(), target_bars)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _TTL_BY_TF.get(tf.upper(), 6 * 3600):
            return hit[1]

    df = _norm(provider.get_candles(market, tf, limit=target_bars))
    parts = [df] if df is not None and len(df) else []
    have = sum(len(p) for p in parts)
    fetched_extra = 0
    if 0 < have < target_bars or have == 0:
        step = _CHUNK_DAYS.get(tf.upper(), 40)
        end = parts[0].index[0] if parts else datetime.now(timezone.utc)
        for _ in range(max_extra_calls):
            start = end - timedelta(days=step)
            try:
                chunk = _norm(provider.fetch_historical_candles(
                    market, tf, start.strftime("%Y-%m-%d %H:%M:%S"),
                    end.strftime("%Y-%m-%d %H:%M:%S")))
            except Exception:
                chunk = None
            if chunk is None or len(chunk) == 0:
                break
            fetched_extra += 1
            parts.insert(0, chunk)
            end = start
            have = sum(len(p) for p in parts)
            if have >= target_bars:
                break

    if not parts:
        out = pd.DataFrame()
        note = "no candle data available (honest empty)"
    else:
        out = pd.concat(parts)
        out = out[~out.index.duplicated(keep="last")].sort_index()
        src = f"provider cache + {fetched_extra} historical page(s)" \
              if fetched_extra else "provider cache"
        note = (f"{len(out)} candles ({src}; requested {target_bars})"
                if len(out) < target_bars else
                f"{len(out)} candles ({src})")
        if len(out) < min_bars:
            note += f" - BELOW research floor ({min_bars})"
    with _lock:
        _cache[key] = (now, (out, note))
    return out, note
