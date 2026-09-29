"""Controlled memory for the AI Brain (Stage 2).

Bounded, auditable, per-user. Every entry is capped in text length and the
per-user count is capped (oldest trimmed). The brain may only READ through
recall() and may only WRITE short factual outcome notes - never trading
instructions. Memory informs wording/evidence, never risk numbers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import settings
from ..db.store import get_store

COLL = "ai_memory"          # registered in store.COLLECTIONS
MAX_TEXT_CHARS = 400
MAX_PER_USER = 100


def remember(user_id: Optional[str], text: str, kind: str = "note",
             meta: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Write one short memory note. Never raises. Trims over cap (FIFO)."""
    if not user_id:
        return None
    try:
        store = get_store()
        doc = store.create(COLL, {
            "userId": user_id,
            "kind": str(kind)[:32],
            "text": str(text)[:MAX_TEXT_CHARS],
            "meta": meta or {},
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        _trim(user_id)
        return doc
    except Exception:
        return None


def _trim(user_id: str) -> int:
    """Keep only the newest MAX_PER_USER entries. Returns deleted count."""
    try:
        store = get_store()
        docs = store.list(COLL, filters={"userId": user_id},
                          order_by="createdAt", desc=True, limit=0)
        removed = 0
        for old in docs[MAX_PER_USER:]:
            if store.delete(COLL, old["id"]):
                removed += 1
        return removed
    except Exception:
        return 0


def recall(user_id: Optional[str], market: Optional[str] = None,
           strategy: Optional[str] = None, k: int = 5) -> List[dict]:
    """Newest-first relevant memories: entries for this market/strategy when
    given, plus global notes. Bounded to k items. Never raises.

    3.0 spec section 43: every memory carries a lifecycle state
    (ACTIVE/STALE/CONTRADICTED/INVALIDATED) and an `authority` flag -
    only ACTIVE entries are authoritative; the rest stay visible but flagged
    so old beliefs can never be silently trusted."""
    if not user_id:
        return []
    try:
        lifecycle_sweep(user_id)
        docs = get_store().list(COLL, filters={"userId": user_id},
                                order_by="createdAt", desc=True,
                                limit=MAX_PER_USER)
    except Exception:
        return []
    out: List[dict] = []
    for d in docs:
        m = d.get("meta") or {}
        hit = ((market and m.get("market") == market) or
               (strategy and m.get("strategy") == strategy) or
               d.get("kind") == "global")
        if hit:
            state = d.get("state") or "ACTIVE"
            out.append({"kind": d.get("kind"), "text": d.get("text"),
                        "meta": m, "createdAt": d.get("createdAt"),
                        "state": state,
                        "authority": state == "ACTIVE"})
        if len(out) >= k:
            break
    return out


# ---- lifecycle (3.0 spec section 43) ---------------------------------------
STALE_AFTER_DAYS = 60


def lifecycle_sweep(user_id: Optional[str] = None) -> int:
    """Mark memory states: CONTRADICTED/INVALIDATED (explicit meta flags) win
    over everything; otherwise entries untouched for STALE_AFTER_DAYS become
    STALE. States stay visible - they just lose authority. Returns the number
    of state changes. Never raises."""
    try:
        store = get_store()
        from datetime import datetime, timezone
        filters = {"userId": user_id} if user_id else None
        docs = store.list(COLL, filters=filters, limit=MAX_PER_USER * 2)
        changed = 0
        now = datetime.now(timezone.utc)
        for d in docs:
            state = d.get("state") or "ACTIVE"
            if state in ("CONTRADICTED", "INVALIDATED"):
                continue
            new_state = state
            meta = d.get("meta") or {}
            if meta.get("invalidated"):
                new_state = "INVALIDATED"
            elif meta.get("contradicted"):
                new_state = "CONTRADICTED"
            else:
                try:
                    created = datetime.fromisoformat(
                        str(d.get("createdAt")).replace("Z", "+00:00"))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if (now - created).days >= STALE_AFTER_DAYS:
                        new_state = "STALE"
                except Exception:
                    pass
            if new_state != state:
                store.update(COLL, d["id"], {"state": new_state})
                changed += 1
        return changed
    except Exception:
        return 0
