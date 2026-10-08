"""Strategy 2 (Supply & Demand + FVG) state persistence.

Same narrow-row pattern as the previous S2 machine: one document per
"<market>|<entry_tf>" in the dedicated collection, merged over defaults on
load so older/partial documents resume safely. Nothing resets on restart.
"""
from __future__ import annotations

from typing import Any, Dict

from ...db.store import get_store

COLLECTION = "strategy2_sd_fvg_state"


def doc_id(market: str, entry_tf: str) -> str:
    return f"{market}|{entry_tf}"


def load(market: str, entry_tf: str) -> Dict[str, Any]:
    from .machine import default_state
    st = default_state()
    try:
        doc = get_store().get(COLLECTION, doc_id(market, entry_tf))
    except Exception:
        return st
    if not doc:
        return st
    for k in st:
        if k in doc and doc[k] is not None:
            st[k] = doc[k]
    return st


def save(market: str, entry_tf: str, state: Dict[str, Any]) -> None:
    """Write only when something changed (quota is precious)."""
    try:
        store = get_store()
        doc = store.get(COLLECTION, doc_id(market, entry_tf)) or {}
        changed = any(doc.get(k) != v for k, v in state.items())
        if not changed:
            return
        if doc:
            store.update(COLLECTION, doc_id(market, entry_tf), dict(state))
        else:
            store.create(COLLECTION, dict(state), doc_id=doc_id(market, entry_tf))
    except Exception:
        # persistence must never break scanning; the in-memory pass still
        # produced a candidate and duplicate prevention holds via last_signal_key
        pass
