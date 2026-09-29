"""COUNTERFACTUAL ENGINE (3.0 spec sections 40-41).

Research-only alternative-outcome simulations on COMPLETED signals, computed
from the recorded candle store: different TP target, delayed entry, blocked
instead of taken. NEVER rewrites the historical record - the original outcome
fields stay untouched and the simulation is returned separately, labeled.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _load(store, user_id: str, signal_ref) -> Optional[dict]:
    docs = store.list("signals", filters={"userId": user_id}, limit=3000)
    for s in docs:
        if s.get("id") == signal_ref or s.get("signal_id") == signal_ref:
            return s
    return None


def _walk(hist: List[dict], start: str, buy: bool,
          sl: float, target: float) -> Optional[str]:
    """First touch of SL or target after start. Returns SL/TARGET/None."""
    after = False
    for c in hist:
        ts = str(c.get("t") or c.get("time") or "")
        if not after:
            if ts > start:
                after = True
            else:
                continue
        hi, lo = float(c["h"]), float(c["l"])
        if (lo <= sl) if buy else (hi >= sl):
            return "SL"
        if (hi >= target) if buy else (lo <= target):
            return "TARGET"
    return None


def counterfactuals(user_id: str, signal_ref, delay_min: int = 15) -> dict:
    from ..db.store import get_store
    store = get_store()
    sig = _load(store, user_id, signal_ref)
    if not sig:
        return {"error": "signal not found"}
    if not sig.get("completed"):
        return {"error": "only completed signals can be counterfactually analyzed"}
    try:
        from ..market_data import candle_store
        hist = candle_store.history(sig.get("market") or "EURUSD", limit=1000)
    except Exception:
        hist = []
    if not hist:
        return {"error": "no recorded candles for this market - simulation refused (never invented)"}

    start = str(sig.get("candle_time") or "")
    buy = (sig.get("direction") or "BUY").upper() == "BUY"
    sl = sig.get("sl")
    actual = {"outcome": sig.get("outcome"), "tp_hits": sig.get("tp_hits"),
              "r_multiple": sig.get("r_multiple")}

    sims: List[dict] = []
    # 1. TP3 instead of actual final TP (same SL)
    if sig.get("tp3") and sl is not None:
        sims.append({"question": "what if TP3 was the target instead?",
                     "result": _walk(hist, start, buy, float(sl), float(sig["tp3"]))})
    # 2. delayed entry (first candle >= start + delay): target = TP2, SL same
    if sig.get("tp2") and sl is not None:
        from datetime import datetime, timedelta
        try:
            d0 = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            d0 = None
        if d0 is not None:
            late = None
            for c in hist:
                ts = str(c.get("t") or c.get("time") or "")
                try:
                    dc = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                if dc >= d0 + timedelta(minutes=delay_min):
                    late = ts
                    break
            if late:
                sims.append({"question": f"what if entry was delayed {delay_min} minutes?",
                             "result": _walk(hist, late, buy, float(sl), float(sig["tp2"]))})
            else:
                sims.append({"question": f"what if entry was delayed {delay_min} minutes?",
                             "result": "no candle at/after delayed time in record"})
    # 3. blocked instead of taken: would price have hit SL or TP1 after signal?
    if sig.get("tp1") and sl is not None:
        r = _walk(hist, start, buy, float(sl), float(sig["tp1"]))
        sims.append({"question": "what if a filter had blocked this trade?",
                     "result": f"hypothetical first-touch: {r}"})

    return {
        "signal_id": sig.get("signal_id") or sig.get("id"),
        "actual_record": actual,          # untouched original
        "simulations": sims,              # clearly separate
        "epistemic": "research simulation from recorded candles - never a rewrite of history",
    }
