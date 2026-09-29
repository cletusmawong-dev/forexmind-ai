"""PORTFOLIO EXPOSURE INTELLIGENCE (3.0 spec sections 21-22).

Currency/directional exposure from OPEN positions (bridge truth when online,
signal-recorded fills otherwise). Deterministic aggregation - and a
REPORT-ONLY adaptive-risk suggestion with hard clamps (never increases risk,
never enforced unless explicitly enabled by configuration).
"""
from __future__ import annotations

from typing import Dict, Optional

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "CHF", "CAD", "NZD", "XAU")


def _roots(market: str) -> set:
    m = (market or "").upper()
    found = {c for c in CURRENCIES if c in m}
    return found or {m[:3]}


def exposure_snapshot(user_id: str) -> dict:
    from ..db.store import get_store
    store = get_store()
    positions: list = []
    basis = "stored"
    try:
        from ..execution.mt5 import bridge_get, MAGIC
        data = bridge_get("/positions", timeout=6) or {}
        for p in data.get("positions") or []:
            if int(p.get("magic") or 0) == MAGIC:
                positions.append(p)
        basis = "mt5_bridge"
    except Exception:
        basis = "stored"
    if not positions:
        positions = [
            {"symbol": s.get("market"), "type": 0 if s.get("direction") == "BUY" else 1,
             "volume": s.get("mt5_volume") or 0.01}
            for s in store.list("signals", filters={"userId": user_id}, limit=200)
            if s.get("status") in ("open", "active") and not s.get("completed")]

    per_ccy: Dict[str, dict] = {}
    for p in positions:
        sym = (p.get("symbol") or p.get("app_market") or "?").upper()
        side = "BUY" if int(p.get("type") or 0) == 0 else "SELL"
        vol = float(p.get("volume") or 0)
        for c in _roots(sym):
            a = per_ccy.setdefault(c, {"buy_lots": 0.0, "sell_lots": 0.0})
            a["buy_lots" if side == "BUY" else "sell_lots"] += vol
    for c, a in per_ccy.items():
        a["net_lots"] = round(a["buy_lots"] - a["sell_lots"], 2)
        a["dominant_side"] = ("BUY" if a["buy_lots"] >= a["sell_lots"] else "SELL") \
            if (a["buy_lots"] + a["sell_lots"]) else None
        tot = a["buy_lots"] + a["sell_lots"]
        a["concentration"] = round(max(a["buy_lots"], a["sell_lots"]) / tot, 2) if tot else 0.0

    instruments = sorted({(p.get("symbol") or p.get("app_market") or "?").upper()
                          for p in positions})
    return {
        "basis": basis,
        "open_positions": len(positions),
        "instruments": instruments,
        "currency_exposure": per_ccy,
        "usd_dominance": max((a["buy_lots"] + a["sell_lots"]
                              for c, a in per_ccy.items() if c == "USD"), default=0.0),
        "note": "shared-currency positions can be correlated even when instruments differ",
    }


def adaptive_risk_suggestion(user_id: str, market: str) -> dict:
    """Deterministic, clamp-bounded RISK-REDUCING multiplier suggestion from
    evidence state + strategy health. Hard bounds [0.5, 1.0] - it may only
    ever reduce risk. REPORT-ONLY unless RISK_ADAPTIVE_ENFORCE=1 (default 0):
    AI cannot set it, config cannot exceed the clamps (spec section 22)."""
    mult = 1.0
    reasons: list = []
    strategy_ids: list = []
    try:
        from ..db.store import get_store
        store = get_store()
        strategy_ids = sorted({s.get("strategy_id")
                               for s in store.list("signals", filters={"userId": user_id}, limit=100)
                               if s.get("market") == market and s.get("strategy_id")})
    except Exception:
        pass
    try:
        from ..evidence import engine
        for sid in strategy_ids[:2]:
            ev = engine.build_evidence(user_id, sid, market, persist=False)
            if ev["state"] in ("INSUFFICIENT", "CONTRADICTED"):
                mult = min(mult, 0.5 if ev["state"] == "CONTRADICTED" else 0.75)
                reasons.append(f"{sid}: evidence {ev['state']}")
            elif ev["contradictions"]:
                mult = min(mult, 0.9)
                reasons.append(f"{sid}: active contradictions")
    except Exception as exc:
        reasons.append(f"evidence unavailable: {type(exc).__name__}")
    try:
        from ..learning.health import strategy_health
        for sid in strategy_ids[:2]:
            h = strategy_health(user_id, sid)
            if h["state"] in ("DEGRADING", "INVESTIGATION"):
                mult = min(mult, 0.7)
                reasons.append(f"{sid}: health {h['state']}")
    except Exception as exc:
        reasons.append(f"health unavailable: {type(exc).__name__}")
    mult = round(max(0.5, min(1.0, mult)), 2)
    try:
        from ..config import settings
        enforced = bool(getattr(settings, "risk_adaptive_enforce", False))
    except Exception:
        enforced = False
    return {
        "market": market, "suggested_multiplier": mult,
        "hard_clamps": [0.5, 1.0], "reasons": reasons,
        "enforced": enforced,
        "note": ("deterministic, config-gated, clamped; AI cannot set it"
                 if enforced else
                 "REPORT-ONLY (RISK_ADAPTIVE_ENFORCE unset) - no live effect"),
    }
