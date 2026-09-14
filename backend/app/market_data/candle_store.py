"""Persistent candle storage.

Keeps a rolling window of closed 15M candles per market in the same
DataStore used for everything else (Firestore in prod, local JSON in dev):

  * charts/API get history instantly after a cold start — fewer
    TwelveData calls (free tier is 800/day),
  * real data survives provider hiccups and feeds future backtests,
  * writes happen only when a NEW closed 15M candle appears
    (max ~96/market/day) — well inside the Firestore free tier.

Every failure degrades to memory-only; storage never blocks scanning.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from ..db.store import get_store

COLL = "candles"
TF = "15M"
KEEP = 600                      # rolling window per market (≈ 6 days of 15M)

MARKETS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NAS100"]

_lock = threading.RLock()
_mem: Dict[str, List[List[float]]] = {}     # market -> [[ts,o,h,l,c], ...] ascending
_hydrated: set = set()
_persist_errors = 0


def _encode(rows: List[List[float]]) -> List[str]:
    """Firestore forbids nested arrays — serialize each row as "ts,o,h,l,c"."""
    return [",".join(str(x) for x in r) for r in rows]


def _decode(items: Optional[List[str]]) -> List[List[float]]:
    out: List[List[float]] = []
    for it in items or []:
        try:
            p = it.split(",")
            out.append([int(float(p[0]))] + [float(x) for x in p[1:]])
        except Exception:
            continue
    return out


def _rows_from_df(df: pd.DataFrame) -> List[List[float]]:
    """Provider df (DatetimeIndex, open/high/low/close) -> compact rows."""
    rows: List[List[float]] = []
    for ts, row in df.iterrows():
        try:
            t = int(pd.Timestamp(ts).timestamp())
            rows.append([t, round(float(row["open"]), 6), round(float(row["high"]), 6),
                         round(float(row["low"]), 6), round(float(row["close"]), 6)])
        except Exception:
            continue
    return rows


def _hydrate(market: str) -> None:
    """Load stored history once per process (best effort)."""
    if market in _hydrated:
        return
    with _lock:
        _hydrated.add(market)
        if _mem.get(market):
            return
    try:
        doc = get_store().get(COLL, market)
        rows = _decode((doc or {}).get("rows"))
        if rows:
            with _lock:
                _mem[market] = sorted(rows, key=lambda r: r[0])[-KEEP:]
    except Exception:
        pass


def _persist(market: str) -> None:
    global _persist_errors
    try:
        store = get_store()
        with _lock:
            rows = list(_mem.get(market) or [])
        doc = {"tf": TF, "rows": _encode(rows), "count": len(rows),
               "lastTs": rows[-1][0] if rows else 0}
        if store.get(COLL, market):
            store.update(COLL, market, doc)
        else:
            store.create(COLL, doc, doc_id=market)
        _persist_errors = 0
    except Exception:
        _persist_errors += 1   # quota pause / hiccup — memory keeps working


def record(market: str, df: Optional[pd.DataFrame]) -> int:
    """Append newly closed candles (provider df is TTL-cached → no extra API calls).

    Returns how many new candles were stored."""
    if df is None or len(df) == 0:
        return 0
    m = market.upper()
    if m not in MARKETS:
        return 0
    rows = _rows_from_df(df)
    if not rows:
        return 0
    with _lock:
        cur = _mem.setdefault(m, [])
        last_ts = cur[-1][0] if cur else 0
        fresh = [r for r in rows if r[0] > last_ts]
        if fresh:
            cur.extend(fresh)
            del _mem[m][: max(0, len(cur) - KEEP)]
        new = len(fresh)
    _hydrated.add(m)
    if new:
        _persist(m)
    return new


def history(market: str, limit: int = 300) -> List[dict]:
    """Stored candles, oldest -> newest (real recorded data only)."""
    m = market.upper()
    _hydrate(m)
    with _lock:
        rows = (_mem.get(m) or [])[-max(1, min(limit, KEEP)):]
    return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
            for r in rows]


def stats() -> dict:
    """Honest per-market storage counters (for Settings)."""
    out = {}
    for m in MARKETS:
        _hydrate(m)
        with _lock:
            rows = _mem.get(m) or []
        last = datetime.fromtimestamp(rows[-1][0], tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if rows else None
        out[m] = {"count": len(rows), "last": last}
    return {"tf": TF, "keep": KEEP, "markets": out,
            "total": sum(v["count"] for v in out.values()),
            "persist_errors": _persist_errors}
