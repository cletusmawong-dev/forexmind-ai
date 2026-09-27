"""Execution lifecycle ledger (Master Upgrade Phase 4 / Stage 3).

Every order-changing action (entry, SL modify, partial close, full close)
appends structured events so ANY claimed action can be reconciled against
broker truth:

    REQUESTED -> SENT -> CONFIRMED   (broker acknowledged; ticket attached)
    REQUESTED -> FAILED | EXPIRED | QUEUED | SKIPPED(reason)

Append-only, never raises (a ledger failure must not break trading), tiny
docs. The TP-leak audit (tp_audit.py) reads these plus live broker state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..db.store import get_store

COLL = "exec_events"        # registered in store.COLLECTIONS
KINDS = ("ENTRY", "MODIFY_SL", "PARTIAL_CLOSE", "CLOSE_FULL", "TP_AUDIT")
STAGES = ("REQUESTED", "SENT", "CONFIRMED", "FAILED", "EXPIRED", "QUEUED",
          "SKIPPED")


def emit(user_id: Optional[str], kind: str, stage: str,
         market: Optional[str] = None, signal_id: Optional[str] = None,
         signal_doc_id: Optional[str] = None, ticket: Optional[int] = None,
         detail: str = "", latency_ms: Optional[float] = None,
         extra: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Append one lifecycle event. Never raises; truncates noisy fields."""
    if kind not in KINDS or stage not in STAGES:
        return None
    try:
        return get_store().create(COLL, {
            "userId": user_id,
            "kind": kind,
            "stage": stage,
            "ok": stage in ("CONFIRMED",),
            "market": market,
            "signal_id": signal_id,
            "signal_doc_id": signal_doc_id,
            "ticket": ticket,
            "detail": str(detail)[:200],
            "latency_ms": (round(float(latency_ms), 1)
                           if latency_ms is not None else None),
            "extra": extra or {},
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        return None


def recent(user_id: str, limit: int = 50) -> list:
    """Newest-first lifecycle events for a user (dashboard surface)."""
    try:
        return get_store().list(COLL, filters={"userId": user_id},
                                order_by="createdAt", desc=True, limit=limit)
    except Exception:
        return []
