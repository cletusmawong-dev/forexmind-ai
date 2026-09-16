"""Structured market-data / infrastructure event logging.

All VPS-readiness events flow through agent activity with the DATA_EVENT
kind so observability has ONE convention. Event types (SPEC: observability):

DATA_CONNECTED, DATA_DISCONNECTED, DATA_RECONNECTED, DATA_GAP_DETECTED,
DATA_BACKFILL_STARTED, DATA_BACKFILL_COMPLETED, CANDLE_CLOSED,
INDICATOR_UPDATED, SIGNAL_CANDIDATE, SIGNAL_CREATED,
BRIDGE_CONNECTED, BRIDGE_DISCONNECTED,
EXECUTION_SKIPPED, EXECUTION_SENT, EXECUTION_FILLED, EXECUTION_FAILED

Existing execution logs keep their kinds; this module is used by the new
market-data infrastructure so every state change is visible and honest.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_LAST: Dict[str, str] = {}   # event_type -> last state signature (dedupe)


def log_event(event_type: str, source: str, message: str,
              payload: Optional[Dict[str, Any]] = None,
              dedupe_key: Optional[str] = None) -> None:
    """Store a structured event. Transitions (e.g. connected->disconnected)
    are always logged; identical repeats of the same dedupe_key are not."""
    from ..db.store import get_store
    sig = f"{event_type}|{dedupe_key or ''}"
    with _LOCK:
        if dedupe_key and _LAST.get(event_type) == sig:
            return
        _LAST[event_type] = sig
    try:
        get_store().create("agent_activity", {
            "userId": None,
            "kind": "DATA_EVENT",
            "event_type": event_type,
            "source": source,
            "message": message,
            "payload": payload or {},
            "at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass  # observability must never break the pipeline


def reset_dedupe() -> None:
    with _LOCK:
        _LAST.clear()
