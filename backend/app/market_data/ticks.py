"""Tick normalization and validation (VPS-readiness, user spec §6).

Interfaces and pure functions for future real-time tick feeds. Nothing here
claims a streaming provider exists - LiveProvider.capabilities reports the
honest truth (streaming: False until a real stream is configured via
MARKET_DATA_STREAM_URL).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_FUTURE_SKEW_S = 60        # ticks more than 60s in the future are invalid
DEFAULT_STALE_AFTER_S = 300   # no tick for 5 min -> feed considered stale


@dataclass
class Tick:
    ts: int          # epoch seconds, UTC
    price: float
    volume: float = 0.0
    symbol: str = ""
    source: str = ""


def normalize_ticks(raw: List[Dict[str, Any]], symbol: str = "",
                    now: Optional[int] = None) -> Tuple[List[Tick], List[Dict[str, Any]]]:
    """Validate + normalize raw tick dicts into sorted, de-duplicated Ticks.

    Returns (accepted, rejected) where rejected carries the reason per tick:
      - bad_timestamp   (missing/unparseable, or > MAX_FUTURE_SKEW_S ahead)
      - bad_price       (missing/NaN/non-positive)
      - duplicate       (same normalized timestamp as an accepted tick)

    Out-of-order ticks are accepted and sorted deterministically (stable
    sort by timestamp) - real feeds arrive slightly out of order.
    """
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    accepted: List[Tick] = []
    seen_ts: set = set()
    rejected: List[Dict[str, Any]] = []

    for r in raw or []:
        ts = r.get("ts", r.get("timestamp"))
        try:
            if isinstance(ts, str):
                ts = int(pd_ts(ts).timestamp())
            ts = int(ts)
        except Exception:
            rejected.append({"tick": r, "reason": "bad_timestamp"})
            continue
        if ts > now + MAX_FUTURE_SKEW_S:
            rejected.append({"tick": r, "reason": "bad_timestamp"})
            continue
        price = r.get("price", r.get("last"))
        try:
            price = float(price)
            if not (price > 0):
                raise ValueError
        except Exception:
            rejected.append({"tick": r, "reason": "bad_price"})
            continue
        if ts in seen_ts:
            rejected.append({"tick": r, "reason": "duplicate"})
            continue
        seen_ts.add(ts)
        accepted.append(Tick(ts=ts, price=price,
                             volume=float(r.get("volume") or 0.0),
                             symbol=str(r.get("symbol") or symbol),
                             source=str(r.get("source") or "")))

    accepted.sort(key=lambda t: t.ts)   # stable: preserves input order per ts
    return accepted, rejected


def last_tick_age_s(ticks: List[Tick], now: int) -> Optional[int]:
    return (now - ticks[-1].ts) if ticks else None


def is_stale(ticks: List[Tick], now: int,
             max_age_s: int = DEFAULT_STALE_AFTER_S) -> bool:
    age = last_tick_age_s(ticks, now)
    return age is None or age > max_age_s


def pd_ts(value):
    import pandas as pd
    return pd.Timestamp(value)
