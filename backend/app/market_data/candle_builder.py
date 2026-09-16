"""Deterministic tick -> candle builder (VPS-readiness, user spec §7).

TICKS -> 1M -> 5M -> 15M -> 1H -> 4H -> 1D

Guarantees (pinned by tests):
- UTC candle boundaries at exact epoch multiples of the interval.
- OHLCV construction from ticks is deterministic: same tick sequence ->
  byte-identical candles.
- Duplicate ticks are ignored (normalized upstream by ticks.normalize_ticks,
  and defensively here by ts).
- Out-of-order ticks are handled safely (sorted with a stable sort; a tick
  never modifies an already-closed bucket).
- Incomplete candles are NEVER returned as closed: a 15M bucket starting
  12:00 is closed only when a tick >= 12:15 exists (or `as_of` says so).
- A closed candle is emitted exactly once (builder keeps the last closed
  bucket timestamp; closed_candles() is idempotent).

Strategy 2 continues to consume CLOSED candles only - the signal engine's
provider layer already drops the forming candle; this module never produces
one as closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .ticks import Tick

INTERVALS = {"1M": 1, "5M": 5, "15M": 15, "1H": 60, "4H": 240, "1D": 1440}


def bucket_start(ts: int, minutes: int) -> int:
    """UTC-aligned bucket start epoch for `minutes` intervals."""
    return (int(ts) // (minutes * 60)) * minutes * 60


def _empty_bucket(start: int) -> Dict[str, float]:
    return {"ts": start, "open": 0.0, "high": float("-inf"),
            "low": float("inf"), "close": 0.0, "volume": 0.0}


def _fill(b: Dict[str, float], tick: Tick) -> None:
    if b["open"] == 0.0 and b["high"] == float("-inf"):
        b["open"] = tick.price
    b["high"] = max(b["high"], tick.price)
    b["low"] = min(b["low"], tick.price)
    b["close"] = tick.price
    b["volume"] += tick.volume


def _finalize(b: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not b or b["high"] == float("-inf"):
        return None
    return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in b.items()}


def build_candles(ticks: List[Tick], interval: str,
                  as_of: Optional[int] = None) -> List[Dict[str, float]]:
    """Deterministic closed candles for the interval.

    `as_of` (epoch s) defaults to now; a bucket is closed when
    bucket_start + interval <= as_of. Ticks beyond as_of are ignored.
    """
    minutes = INTERVALS[interval]
    as_of = int(as_of if as_of is not None else datetime.now(timezone.utc).timestamp())
    ordered = sorted(ticks, key=lambda t: t.ts)          # stable, out-of-order safe
    buckets: Dict[int, Dict[str, float]] = {}
    for t in ordered:
        start = bucket_start(t.ts, minutes)
        buckets.setdefault(start, _empty_bucket(start))
        _fill(buckets[start], t)
    out = []
    for start in sorted(buckets):
        if start + minutes * 60 <= as_of:                # fully-formed only
            c = _finalize(buckets[start])
            if c:
                out.append(c)
    return out


def missing_intervals(candles: List[Dict[str, float]], interval: str,
                      start: Optional[int] = None,
                      end: Optional[int] = None) -> List[Dict[str, int]]:
    """Deterministic gap detector on the candle grid.

    Returns the missing UTC buckets between the first and last candle
    (or between start/end when provided). Weekends and typical market
    holidays are NOT fabricated: spans longer than MAX_GAP_MINUTES are
    treated as market closure and reported as one `market_closed` span
    instead of thousands of fake missing candles.
    """
    minutes = INTERVALS[interval]
    step = minutes * 60
    MAX_GAP_MINUTES = 4 * 60          # > 4h without candles = closure, not a gap
    if not candles:
        return []
    stamps = [int(c["ts"]) for c in candles]
    lo = bucket_start(min(stamps), minutes) if start is None else int(start)
    hi = bucket_start(max(stamps), minutes) if end is None else int(end)
    have = set(stamps)
    gaps: List[Dict[str, int]] = []
    run_start: Optional[int] = None
    t = lo
    while t <= hi:
        if t not in have:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None:
                _append_gap(gaps, run_start, t - step, step, MAX_GAP_MINUTES * 60)
                run_start = None
        t += step
    if run_start is not None:
        _append_gap(gaps, run_start, hi, step, MAX_GAP_MINUTES * 60)
    return gaps


def _append_gap(gaps: List[Dict[str, int]], missing_from: int, missing_to: int,
                step: int, max_gap_s: int) -> None:
    if missing_to < missing_from:
        missing_to = missing_from
    if missing_to - missing_from > max_gap_s:
        gaps.append({"type": "market_closed", "missing_from": missing_from,
                     "missing_to": missing_to})
    else:
        gaps.append({"type": "data_gap", "missing_from": missing_from,
                     "missing_to": missing_to})
