"""WHY-DIDN'T-WE-TRADE + OPPORTUNITY COST + FILTER CONTRIBUTION
(3.0 spec sections 24-26).

Blocked opportunities are signals whose execution was refused (SKIPPED_*
statuses already recorded loudly). This engine evaluates what the market did
AFTER the block (from recorded candles) - strictly as FILTER RESEARCH. It
never rewrites the decision and never converts a block into a trade.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _reason_of(s: dict) -> str:
    for k in ("execution_status", "status"):
        v = str(s.get(k) or "")
        if v.startswith("SKIPPED"):
            return v
    return "SKIPPED_UNKNOWN"


def _guard_of(reason: str) -> str:
    r = reason.upper()
    for g in ("NEWS", "SPREAD", "VOL", "CORRELATION", "SESSION", "DAILY",
              "ADVISORY", "NOT_AUTHORIZED", "PERMISSION", "RISK"):
        if g in r:
            return g
    return "OTHER"


def _first_touch(hist: List[dict], start: str, buy: bool,
                 sl: float, tp: float, horizon: int = 96) -> str:
    after = False
    seen = 0
    for c in hist:
        ts = str(c.get("t") or c.get("time") or "")
        if not after:
            if ts > start:
                after = True
            else:
                continue
        seen += 1
        if seen > horizon:
            break
        hi, lo = float(c["h"]), float(c["l"])
        if (lo <= sl) if buy else (hi >= sl):
            return "blocked -> SL"
        if (hi >= tp) if buy else (lo <= tp):
            return "blocked -> TP1"
    if seen:
        return "blocked -> no meaningful move in horizon"
    return "blocked -> no post-block candles recorded"


def blocked_analysis(user_id: str, limit: int = 200) -> dict:
    from ..db.store import get_store
    store = get_store()
    blocked = [s for s in store.list("signals", filters={"userId": user_id}, limit=1000)
               if _reason_of(s).startswith("SKIPPED")]
    try:
        from ..market_data import candle_store
        candles: Dict[str, list] = {}
        for s in blocked[:limit]:
            mkt = s.get("market") or "EURUSD"
            if mkt not in candles:
                try:
                    candles[mkt] = candle_store.history(mkt, limit=1000)
                except Exception:
                    candles[mkt] = []
    except Exception:
        candles = {}

    rows: List[dict] = []
    per_filter: Dict[str, dict] = {}
    for s in blocked[:limit]:
        reason = _reason_of(s)
        guard = _guard_of(reason)
        mkt = s.get("market") or "EURUSD"
        buy = (s.get("direction") or "BUY").upper() == "BUY"
        follow = "not_evaluated"
        try:
            if s.get("sl") and s.get("tp1") and candles.get(mkt):
                follow = _first_touch(candles[mkt], str(s.get("candle_time") or ""),
                                      buy, float(s["sl"]), float(s["tp1"]))
        except (TypeError, ValueError):
            follow = "not_evaluated"
        rows.append({"market": mkt, "at": s.get("candle_time"),
                     "reason": reason, "afterward": follow})
        a = per_filter.setdefault(guard, {"blocked": 0, "would_SL": 0,
                                          "would_TP": 0, "no_move": 0, "unevaluated": 0})
        a["blocked"] += 1
        if follow.endswith("SL"):
            a["would_SL"] += 1
        elif follow.endswith("TP1"):
            a["would_TP"] += 1
        elif follow.startswith("blocked -> no meaningful"):
            a["no_move"] += 1
        else:
            a["unevaluated"] += 1

    summary = []
    for g, a in sorted(per_filter.items(), key=lambda kv: -kv[1]["blocked"]):
        judged = a["would_SL"] + a["would_TP"]
        summary.append({
            "filter": g, "blocked": a["blocked"],
            "losing_blocked": a["would_SL"], "winning_blocked": a["would_TP"],
            "no_move": a["no_move"], "unevaluated": a["unevaluated"],
            "net_effect": (f"saved {a['would_SL']} losers / missed {a['would_TP']} winners"
                           if judged else "insufficient post-block data"),
            "note": "statistical association - filter research, not causation",
        })
    return {"blocked_total": len(blocked), "recent": rows[:40],
            "filter_contribution": summary,
            "honesty": "future outcomes never rewrite the original decision record"}


def why_blocked(user_id: str, signal_ref) -> Optional[dict]:
    from ..db.store import get_store
    store = get_store()
    for s in store.list("signals", filters={"userId": user_id}, limit=1000):
        if (s.get("id") == signal_ref or s.get("signal_id") == signal_ref) \
                and _reason_of(s).startswith("SKIPPED"):
            return {"signal_id": s.get("signal_id") or s.get("id"),
                    "market": s.get("market"), "at": s.get("candle_time"),
                    "reason": _reason_of(s),
                    "guard": _guard_of(_reason_of(s)),
                    "strategy": s.get("strategy_name"),
                    "detail": "deterministic guard refusal - recorded loudly at block time"}
    return None
