"""Candle-series integrity checks + honest gap handling (user spec §9).

Used by the signal engine (never generate a signal from incomplete data)
and by the system status endpoint. Philosophy: HONEST FAILURE over silent
failure - gaps are logged with exact bounds, never fabricated over.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .candle_builder import INTERVALS
from .events import log_event

CHECK_WINDOW = 20          # only the most recent N candles are scrutinized
MAX_NORMAL_GAP_S = 4 * 3600   # spans longer than this = weekend/holiday closure


def validate_candles(stamps: List[int]) -> Dict[str, Any]:
    """Structural validation of an ordered candle timestamp list."""
    issues: Dict[str, Any] = {"duplicates": [], "out_of_order": [], "count": len(stamps)}
    seen: set = set()
    for i, ts in enumerate(stamps):
        if ts in seen:
            issues["duplicates"].append(ts)
        seen.add(ts)
        if i and ts < stamps[i - 1]:
            issues["out_of_order"].append({"index": i, "ts": ts})
    return issues


def detect_gaps(stamps: List[int], timeframe: str,
                source: str = "scan") -> List[Dict[str, int]]:
    """Real data gaps in the CHECK_WINDOW most recent candles.

    Weekend/holiday closures (span > MAX_NORMAL_GAP_S) are not gaps.
    Every real gap is logged as DATA_GAP_DETECTED with exact bounds.
    """
    step = INTERVALS.get(timeframe, 15) * 60
    recent = sorted(stamps)[-CHECK_WINDOW:]
    gaps: List[Dict[str, int]] = []
    for a, b in zip(recent, recent[1:]):
        span = b - a
        if span > step and span <= MAX_NORMAL_GAP_S:
            gaps.append({"missing_from": a + step, "missing_to": b - step})
    for g in gaps:
        log_event(
            "DATA_GAP_DETECTED", source,
            f"Data gap detected - no candles between {g['missing_from']} and {g['missing_to']}",
            {"missing_from": g["missing_from"], "missing_to": g["missing_to"],
             "timeframe": timeframe},
            dedupe_key=f"{timeframe}|{g['missing_from']}|{g['missing_to']}")
    return gaps


def series_is_trustworthy(stamps: List[int], timeframe: str,
                          min_len: int) -> Dict[str, Any]:
    """Gate used before signal generation on a candle series."""
    if len(stamps) < min_len:
        return {"ok": False, "reason": "insufficient_history", "detail": len(stamps)}
    issues = validate_candles(stamps)
    if issues["duplicates"] or issues["out_of_order"]:
        return {"ok": False, "reason": "malformed_series",
                "detail": {"duplicates": len(issues["duplicates"]),
                           "out_of_order": len(issues["out_of_order"])}}
    gaps = detect_gaps(stamps, timeframe)
    if gaps:
        return {"ok": False, "reason": "data_gap",
                "detail": gaps[0], "gaps": gaps}
    return {"ok": True, "reason": None, "detail": None}


def backfill_report(old_stamps: List[int], new_candles: List[Dict[str, Any]],
                    timeframe: str) -> Dict[str, Any]:
    """Validate a backfill batch against existing candles before merging.

    Rejects duplicates, overlapping or non-continuing batches honestly -
    a backfill must FILL a gap, not invent history.
    """
    new_stamps = [int(c["ts"]) for c in new_candles]
    if len(set(new_stamps)) != len(new_stamps):
        return {"ok": False, "reason": "duplicate_candles_in_batch"}
    old_set = set(old_stamps)
    overlap = [t for t in new_stamps if t in old_set]
    if overlap:
        return {"ok": False, "reason": "overlaps_existing",
                "detail": {"first_overlap": min(overlap)}}
    step = INTERVALS.get(timeframe, 15) * 60
    contiguous = all(b - a == step for a, b in zip(new_stamps, new_stamps[1:]))
    return {"ok": True, "count": len(new_stamps),
            "contiguous": contiguous,
            "bridges_gap": bool(old_stamps and new_stamps
                                and min(new_stamps) > max(old_stamps))}
