"""Book-level risk that sits above any one account.

combined_gold_cap_usd is off when the settings doc is missing or the
value is empty. Nothing in the order path reads this yet. Fan-out will.
"""
from __future__ import annotations

from typing import Optional

DOC_ID = "book_risk"


def combined_gold_cap_usd() -> Optional[float]:
    try:
        from ..db.store import get_store
        doc = get_store().get("settings", DOC_ID) or {}
    except Exception:
        return None
    raw = doc.get("combined_gold_cap_usd")
    if raw in (None, "", 0, 0.0):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
