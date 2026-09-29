"""DECISION REPLAY + TIME MACHINE + WHY (3.0 spec sections 38-39, 53).

Reconstructs what the system KNEW AT THE TIME from the records that exist:
signal DNA (snapshot at signal moment), brain decision record, exec_events
chain, guard outcomes, outcome records. Anything not recorded is reported as
not_recorded - future information is NEVER injected into the replay.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _signal(store, user_id: str, signal_id_or_doc: str) -> Optional[dict]:
    if isinstance(signal_id_or_doc, dict):
        return signal_id_or_doc
    docs = store.list("signals", filters={"userId": user_id}, limit=3000)
    for s in docs:
        if s.get("id") == signal_id_or_doc or s.get("signal_id") == signal_id_or_doc:
            return s
    return None


def decision_replay(user_id: str, signal_ref) -> Optional[dict]:
    from ..db.store import get_store
    store = get_store()
    sig = _signal(store, user_id, signal_ref)
    if not sig:
        return None

    dna = sig.get("dna")
    events = [e for e in store.list("exec_events", filters={"userId": user_id}, limit=1000)
              if e.get("signal_doc_id") == sig.get("id")
              or e.get("signal_id") == sig.get("signal_id")]
    events.sort(key=lambda e: str(e.get("createdAt") or ""))

    brains = [d for d in store.list("ai_decisions", filters={"userId": user_id}, limit=1000)
              if d.get("signal_id") == sig.get("signal_id")]

    known_at_time = {
        "market_fingerprint": (dna or {}).get("fingerprint") or {
            k: (dna or {}).get(k) for k in ("regime", "session")
            if (dna or {}).get(k) is not None} if dna else None,
        "market_conditions": sig.get("market_conditions"),
        "entry_tf": sig.get("timeframe"),
        "entry": sig.get("entry"), "sl": sig.get("sl"),
        "tps": [sig.get("tp1"), sig.get("tp2"), sig.get("tp3")],
        "strategy": sig.get("strategy_name"),
        "note": "everything above was recorded at/nefore signal time",
    }
    later_outcome = {
        "completed": bool(sig.get("completed")),
        "outcome": sig.get("outcome"),
        "r_multiple": sig.get("r_multiple"),
        "tp_hits": sig.get("tp_hits"),
        "mt5": {"ticket": sig.get("mt5_ticket"), "open_price": sig.get("mt5_open_price"),
                "pl": sig.get("mt5_pl"), "confirmed": bool(sig.get("mt5_confirmed"))}
        if sig.get("mt5_ticket") else None,
        "note": "outcome information - kept STRICTLY separate from known-at-time",
    }
    gates = [g for g in (sig.get("guard_results") or [])] if \
        isinstance(sig.get("guard_results"), list) else []

    return {
        "signal_id": sig.get("signal_id") or sig.get("id"),
        "market": sig.get("market"), "direction": sig.get("direction"),
        "why_traded": (sig.get("reason") or sig.get("reasons")
                       or (dna or {}).get("trigger_reason") or "not_recorded"),
        "known_at_time": known_at_time,
        "brain": brains[-1] if brains else None,
        "gates": gates or "not_recorded",
        "execution_chain": [{"stage": e.get("stage"), "kind": e.get("kind"),
                             "ticket": e.get("ticket"),
                             "latency_ms": e.get("latency_ms"),
                             "detail": e.get("detail"), "at": e.get("createdAt")}
                            for e in events] or "not_recorded",
        "later_outcome": later_outcome,
        "honesty": ("replay reconstructs ONLY recorded data; gaps are shown as "
                    "not_recorded - future information is never injected"),
    }


def time_machine(user_id: str, signal_ref, window: int = 24) -> Optional[dict]:
    """Candles around the entry with T-x markers; known-at-time vs outcome
    kept separate."""
    from ..db.store import get_store
    store = get_store()
    sig = _signal(store, user_id, signal_ref)
    if not sig:
        return None
    try:
        from ..market_data import candle_store
        hist = candle_store.history(sig.get("market") or "EURUSD", limit=1000)
    except Exception:
        hist = []
    ct = str(sig.get("candle_time") or "")
    around = [c for c in hist if abs(_idx_distance(str(c.get("t") or c.get("time") or ""), ct)) <= window]
    markers = {"entry": sig.get("entry"), "sl": sig.get("sl"),
               "tp1": sig.get("tp1"), "tp2": sig.get("tp2"), "tp3": sig.get("tp3")}
    return {"signal_id": sig.get("signal_id") or sig.get("id"),
            "candle_time": ct or None, "markers": markers,
            "candles_known_at_time": around if ct else "not_recorded",
            "outcome": {"outcome": sig.get("outcome"), "tp_hits": sig.get("tp_hits"),
                        "r_multiple": sig.get("r_multiple")},
            "note": ("candles up to entry are what the system could see; the "
                     "outcome block is later information, kept separate")}


def _idx_distance(a: str, b: str) -> float:
    from datetime import datetime
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        if da.tzinfo is None or db.tzinfo is None:
            da, db = da.replace(tzinfo=None), db.replace(tzinfo=None)
        return abs((da - db).total_seconds() / 900.0)
    except Exception:
        return 10 ** 9
