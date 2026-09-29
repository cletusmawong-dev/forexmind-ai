"""TRANSACTION COST ANALYSIS + BROKER INTELLIGENCE (3.0 spec sections 17-19).

Built ONLY from recorded facts: exec_events (latency, stages, rejects) and
signal docs (requested entry vs actual mt5_open_price -> slippage; mt5_pl net
of commission+swap). Nothing is estimated that was not measured - unmeasured
components are reported as null with an explicit note.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tca_report(user_id: str, limit: int = 300) -> dict:
    from ..db.store import get_store
    store = get_store()
    sigs = [s for s in store.list("signals", filters={"userId": user_id}, limit=limit)
            if s.get("mt5_confirmed") or s.get("mt5_open_price")]

    slips: List[float] = []
    per_market: Dict[str, dict] = {}
    theoretical_r = realized_r_dollars = 0.0
    n_costed = 0
    for s in sigs:
        req, act = _f(s.get("entry")), _f(s.get("mt5_open_price"))
        if req and act and req != act:
            # direction-aware: positive slippage = worse fill
            signed = (act - req) if s.get("direction") == "BUY" else (req - act)
            slips.append(round(signed, 5))
        m = per_market.setdefault(s.get("market") or "?",
                                  {"n": 0, "slippage_sum": 0.0, "slippage_n": 0})
        m["n"] += 1
        if req and act and req != act:
            signed = (act - req) if s.get("direction") == "BUY" else (req - act)
            m["slippage_sum"] += signed
            m["slippage_n"] += 1
        r = _f(s.get("r_multiple"))
        pl = _f(s.get("mt5_pl"))
        if r is not None:
            theoretical_r += r
        if pl is not None:
            realized_r_dollars += pl
            n_costed += 1

    events = store.list("exec_events", filters={"userId": user_id}, limit=1000)
    entries = [e for e in events if e.get("kind") == "ENTRY"]
    confirmed = [e for e in entries if e.get("stage") == "CONFIRMED"]
    failed = [e for e in entries if e.get("stage") == "FAILED"]
    lat = [_f(e.get("latency_ms")) for e in confirmed]
    lat = [x for x in lat if x is not None]

    avg_slip = round(sum(slips) / len(slips), 5) if slips else None
    return {
        "basis": "recorded exec_events + broker-confirmed signal fills only",
        "signals_with_fills": len(sigs),
        "slippage": {
            "measured_n": len(slips),
            "avg_signed_slippage": avg_slip,
            "note": "positive = fill worse than requested (direction-adjusted)",
            "per_market": {k: {"n": v["n"],
                               "avg_slippage": round(v["slippage_sum"] / v["slippage_n"], 5)
                               if v["slippage_n"] else None}
                           for k, v in per_market.items()},
        },
        "costs": {
            "commission_swap": None,
            "note": ("broker reports net P/L (profit+commission+swap combined); "
                     "separate commission/swap not exposed by bridge - reported "
                     "as null, never estimated"),
            "realized_net_usd": round(realized_r_dollars, 2) if n_costed else 0.0,
            "trades_costed": n_costed,
            "theoretical_r_sum": round(theoretical_r, 2),
        },
        "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
        "broker_intelligence": {
            "entry_requests": len(entries),
            "confirmed": len(confirmed),
            "failed": len(failed),
            "reject_rate_pct": round(100 * len(failed) / len(entries), 1) if entries else None,
            "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
            "note": "factual statistics over time - no automatic conclusions",
        },
    }
