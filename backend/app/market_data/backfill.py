"""Historical backfill (VPS-readiness, user spec §10).

When a live stream (future VPS collector) reconnects, the pipeline must:
  1. find the last known candle
  2. request exactly the missing interval from the provider
  3. validate the returned candles (no duplicates / no overlap / honest bounds)
  4. merge + recalculate downstream

Nothing here fabricates candles: if the provider cannot serve the gap, the
gap stays open and is reported. This module is infrastructure for the future
stream; the current poll-based provider keeps working unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .candle_builder import INTERVALS
from .events import log_event
from .integrity import backfill_report


def plan_backfill(existing_stamps: List[int], timeframe: str,
                  now: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Compute the missing interval to request (UTC epoch seconds).

    Returns None when there is nothing sensible to backfill (no baseline,
    or the latest candle is already current within one interval).
    """
    step = INTERVALS.get(timeframe, 15) * 60
    now = int(now if now is not None else pd.Timestamp.utcnow().timestamp())
    if not existing_stamps:
        return None  # no baseline: a bounded range must be requested explicitly
    last = max(existing_stamps)
    if now - last <= step:
        return None
    return {"start": last + step, "end": now - step, "timeframe": timeframe}


def merge_backfill(existing_stamps: List[int], fetched: pd.DataFrame,
                   timeframe: str) -> Dict[str, Any]:
    """Validate a fetched gap batch; return the rows to append (or a reason).

    The caller owns persistence - this function is pure and honest.
    """
    if fetched is None or len(fetched) == 0:
        log_event("DATA_BACKFILL_COMPLETED", "backfill",
                  "Backfill produced no candles - gap left open, nothing fabricated.",
                  {"timeframe": timeframe})
        return {"ok": False, "reason": "provider_returned_nothing"}
    rows = [{"ts": int(pd.Timestamp(idx).timestamp()),
             "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])}
            for idx, r in fetched.iterrows()]
    report = backfill_report(existing_stamps, rows, timeframe)
    if not report["ok"]:
        log_event("DATA_BACKFILL_COMPLETED", "backfill",
                  f"Backfill rejected: {report['reason']}. Gap left open.",
                  {"timeframe": timeframe, **report})
        return report
    report["rows"] = rows
    log_event("DATA_BACKFILL_COMPLETED", "backfill",
              f"Backfill validated: {len(rows)} candles ready to merge ({timeframe}).",
              {"timeframe": timeframe, "count": len(rows)})
    return report
