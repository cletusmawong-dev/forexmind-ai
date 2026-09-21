"""Strategy 2 state persistence (spec section 18/28).

State lives in the "strategy2_state" collection keyed "<market>|<entry_tf>" -
per SYMBOL + per entry timeframe (spec section 19: XAUUSD state can never leak
into EURUSD). On load the state is validated and merged over defaults so older
/ partial documents resume safely; nothing is reset to zero on restart.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...db.store import get_store

COLLECTION = "strategy2_state"


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
    """Write only when something changed (Firestore quota is precious)."""
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
        # state persistence must never break scanning (spec section 37);
        # duplicate protection still holds via the engine's per-user dedupe
        pass


def clear(market: str, entry_tf: str) -> None:
    try:
        get_store().delete(COLLECTION, doc_id(market, entry_tf))
    except Exception:
        pass
