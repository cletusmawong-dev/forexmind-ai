"""Claim an order before it is sent.

The row lives in exec_events, which is already in the Supabase schema, so
the live account does not wait on a new table. One idempotency key per
signal and account. If the write fails, the caller must not send.

Stages used here: REQUESTED (claimed, not yet known to the broker),
CONFIRMED (ticket known), FAILED (broker refused), UNKNOWN (the HTTP call
did not return and the terminal could not be re-read).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

COLL = "exec_events"


class LedgerError(RuntimeError):
    """The pre-send row could not be written. Do not call the bridge."""


def idempotency_key(signal_id: Optional[str], account_id: Optional[str]) -> str:
    return f"{signal_id or ''}:{account_id or 'legacy'}"


def _rank(stage: str) -> int:
    # CONFIRMED beats an earlier REQUESTED. FAILED beats REQUESTED so a
    # broker reject is not sent again. REQUESTED alone means "not finished".
    order = {"CONFIRMED": 5, "SENT": 4, "FAILED": 3, "UNKNOWN": 2, "REQUESTED": 1}
    return order.get(stage or "", 0)


def find(store, key: str) -> Optional[dict]:
    rows = store.list(COLL, filters={"idempotency_key": key}, limit=20)
    if not rows:
        return None
    return sorted(rows, key=lambda r: _rank(r.get("stage")), reverse=True)[0]


def claim(store, user_id: Optional[str], signal: dict, account_id: Optional[str],
          detail: str = "") -> dict:
    """Return {action: claimed|exists, row, key}. Raises LedgerError on write failure.

    The REQUESTED row is the pre-send claim and is left in place (the ledger
    is append-only). Later CONFIRMED / FAILED rows carry the same key."""
    sig = signal.get("signal_id") or signal.get("id")
    key = idempotency_key(sig, account_id)
    existing = find(store, key)
    if existing:
        return {"action": "exists", "row": existing, "key": key}
    try:
        row = store.create(COLL, {
            "userId": user_id,
            "kind": "ENTRY",
            "stage": "REQUESTED",
            "ok": False,
            "market": signal.get("market"),
            "signal_id": signal.get("signal_id"),
            "signal_doc_id": signal.get("id"),
            "account_id": account_id,
            "idempotency_key": key,
            "detail": (detail or "claimed before send")[:200],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        raise LedgerError(str(exc)) from exc
    if not row or not row.get("id"):
        raise LedgerError("ledger returned no row")
    return {"action": "claimed", "row": row, "key": key}
