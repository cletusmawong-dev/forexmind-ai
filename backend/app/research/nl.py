"""NATURAL-LANGUAGE RESEARCH (3.0 spec section 45).

Translates a question into a SAFE structured query over the user's own
signals. Deterministic parser first; if it cannot confidently map, ONE router
call proposes a JSON filter which is VALIDATED against a whitelist before any
execution. Read-only by construction; the translation is returned for
traceability. Unknown fields never execute.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

ALLOWED = {
    "strategy_id": ("strategy_1_zero_lag", "strategy_2_ema_atr"),
    "market": ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NAS100"),
    "outcome": ("WIN", "LOSS"),
    "session": ("Asian", "London", "NewYork", "Late"),
    "regime": ("TRENDING", "RANGING", "HIGH_VOL", "LOW_VOL"),
}

_PATTERNS = [
    ("outcome", re.compile(r"\b(loss|losses|loser|losers)\b", re.I), lambda m: "LOSS"),
    ("outcome", re.compile(r"\b(win|wins|winner|winners)\b", re.I), lambda m: "WIN"),
    ("market", re.compile(r"\b(EURUSD|GBPUSD|USDJPY|XAUUSD|GOLD|NAS100)\b", re.I),
     lambda m: ("XAUUSD" if m.group(0).upper() == "GOLD" else m.group(0).upper())),
    ("session", re.compile(r"\b(Asian|London|NewYork|Late)\b"), lambda m: m.group(0)),
    ("regime", re.compile(r"high[- ]vol", re.I), lambda m: "HIGH_VOL"),
    ("regime", re.compile(r"low[- ]vol", re.I), lambda m: "LOW_VOL"),
    ("regime", re.compile(r"\btrending\b", re.I), lambda m: "TRENDING"),
    ("regime", re.compile(r"\branging\b", re.I), lambda m: "RANGING"),
    ("strategy_id", re.compile(r"strategy\s*2|smart\s*tp", re.I),
     lambda m: "strategy_2_ema_atr"),
    ("strategy_id", re.compile(r"strategy\s*1|zero[- ]?lag", re.I),
     lambda m: "strategy_1_zero_lag"),
]


def parse_question(question: str, router=None) -> dict:
    """Returns {filters, via}. Only whitelisted fields ever appear."""
    filters: Dict[str, str] = {}
    for field, rx, fn in _PATTERNS:
        if field in filters:
            continue
        m = rx.search(question or "")
        if m:
            try:
                filters[field] = fn(m)
            except Exception:
                pass
    if filters:
        return {"filters": filters, "via": "deterministic_parser"}
    # fallback: ONE router call proposing a filter object - validated hard
    if router is not None:
        try:
            res = router.analyze(
                'Translate the research question into filters. Reply ONLY JSON '
                'with AT MOST these keys: strategy_id (strategy_1_zero_lag|'
                'strategy_2_ema_atr), market (EURUSD|GBPUSD|USDJPY|XAUUSD|'
                'NAS100), outcome (WIN|LOSS), session (Asian|London|NewYork|'
                'Late), regime (TRENDING|RANGING|HIGH_VOL|LOW_VOL). No other '
                'keys are permitted.',
                {"question": question}, escalate=False, user_id="system")
            import json
            txt = res.get("text", "")
            blob = json.loads(txt[txt.find("{"):txt.rfind("}") + 1] or "{}")
            clean = {k: str(v) for k, v in blob.items()
                     if k in ALLOWED and str(v) in ALLOWED[k]}
            if clean:
                return {"filters": clean, "via": "ai_proposed_validated"}
        except Exception:
            pass
    return {"filters": {}, "via": "unparsed"}


def ask(user_id: str, question: str, router=None) -> dict:
    from ..db.store import get_store
    parsed = parse_question(question, router=router)
    store = get_store()
    rows = [s for s in store.list("signals", filters={"userId": user_id}, limit=3000)
            if all(s.get(k) == v or (s.get("market_conditions") or {}).get(k) == v
                   for k, v in parsed["filters"].items())]
    sample = [{"market": s.get("market"), "direction": s.get("direction"),
               "outcome": s.get("outcome"), "r_multiple": s.get("r_multiple"),
               "at": s.get("candle_time"), "strategy": s.get("strategy_name")}
              for s in rows[:25]]
    return {"question": str(question)[:400], "translation": parsed,
            "matched": len(rows), "sample": sample,
            "epistemic": ("statistical results from the user's own records - "
                          "associations, not causation"),
            "allowed_fields": sorted(ALLOWED)}
