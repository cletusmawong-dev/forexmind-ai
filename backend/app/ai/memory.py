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
    given, plus global notes. Bounded to k items. Never raises."""
    if not user_id:
        return []
    try:
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
            out.append({"kind": d.get("kind"), "text": d.get("text"),
                        "meta": m, "createdAt": d.get("createdAt")})
        if len(out) >= k:
            break
    return out
