"""S×P×Session×Regime statistics + [REVIEW][APPLY][REJECT] recommendations
(Master Upgrade Phase 6 / Stage 4).

Cells aggregate COMPLETED signals by strategy × pair(market) × session ×
regime: sample size, win rate, avg R. Cells whose outcome diverges strongly
and negatively from the strategy's overall baseline become RECOMMENDATIONS.

Lifecycle (hard rule - recommendations are NEVER auto-applied):
  REVIEW   -> status REVIEWING (a human is looking at the evidence)
  APPLY    -> drafts the change ONLY:
               - session segment  -> proposed sessions on the strategy doc;
                 activating it is an explicit, audited strategy PATCH
               - regime segment   -> honest no-op (no enforcement primitive
                 yet; run a one-variable experiment instead)
  REJECT   -> closed with a reason, stays in history
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..db.store import get_store

MIN_SEGMENT = 10        # cell sample size to be recommendable
MIN_OVERALL = 20        # strategy baseline sample size
WR_DELTA_PP = 15.0      # required negative divergence (percentage points)


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------
def _cell_key(strategy_id, market, session, regime):
    return f"{strategy_id}|{market}|{session or '-'}|{regime or '-'}"


def build_matrix(user_id: str, limit: int = 2000) -> Dict[str, Any]:
    """Aggregate completed signals into S×P×Session×Regime cells."""
    from .adaptive import features
    store = get_store()
    sigs = store.list("signals", filters={"userId": user_id, "completed": True},
                      limit=limit)
    cells: Dict[str, Dict[str, Any]] = {}
    strategies: Dict[str, Dict[str, Any]] = {}
    for s in sigs:
        sid = s.get("strategy_id")
        if not sid:
            continue
        r = float(s.get("r_multiple") or 0.0)
        win = 1 if s.get("outcome") == "WIN" else 0
        strat = strategies.setdefault(sid, {"n": 0, "wins": 0, "r": 0.0,
                                            "name": s.get("strategy_name")})
        strat["n"] += 1
        strat["wins"] += win
        strat["r"] += r
        feat = features(s)
        key = _cell_key(sid, s.get("market"), feat.get("session"), feat.get("regime"))
        cell = cells.setdefault(key, {"strategy_id": sid,
                                      "strategy_name": s.get("strategy_name"),
                                      "market": s.get("market"),
                                      "session": feat.get("session"),
                                      "regime": feat.get("regime"),
                                      "n": 0, "wins": 0, "r": 0.0,
                                      "signal_ids": []})
        cell["n"] += 1
        cell["wins"] += win
        cell["r"] += r
        if len(cell["signal_ids"]) < 25:
            cell["signal_ids"].append(s.get("signal_id"))

    def _fin(d):
        n = d.pop("n", 0) or 0
        wins = d.pop("wins", 0) or 0
        rsum = d.pop("r", 0.0) or 0.0
        d["n"] = n
        d["win_rate"] = round(100.0 * wins / n, 1) if n else None
        d["avg_r"] = round(rsum / n, 3) if n else None
        return d

    cell_list = [_fin(dict(c)) for c in cells.values()]
    for sid, st in strategies.items():
        _fin(st)
    return {"cells": cell_list, "strategies": strategies,
            "total_completed": len(sigs), "generated_at":
                datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
def _dedupe_key(rec_type, strategy_id, market, session, regime):
    return f"{rec_type}|{strategy_id}|{market}|{session or '-'}|{regime or '-'}"


def generate_recommendations(user_id: str, max_new: int = 3) -> List[dict]:
    """Deterministic scan of matrix cells -> new REVIEW recommendations.
    Never touches any live parameter (hard rule)."""
    store = get_store()
    mx = build_matrix(user_id)
    created: List[dict] = []
    for sid, st in mx["strategies"].items():
        if (st.get("n") or 0) < MIN_OVERALL or st.get("win_rate") is None:
            continue
        for c in mx["cells"]:
            if c["strategy_id"] != sid or (c.get("n") or 0) < MIN_SEGMENT:
                continue
            if c["win_rate"] is None or c["avg_r"] is None:
                continue
            delta = round(c["win_rate"] - st["win_rate"], 1)
            if delta > -WR_DELTA_PP or c["avg_r"] >= 0:
                continue
            seg = (f"{c['session'] or 'unknown'} session" +
                   (f" × {c['regime']}" if c["regime"] else ""))
            rec_type = "FILTER_SESSION" if c["session"] else "REGIME_NOTE"
            key = _dedupe_key(rec_type, sid, c["market"], c["session"], c["regime"])
            if store.list("recommendations", filters={"dedupe_key": key}, limit=1):
                continue
            created.append(store.create("recommendations", {
                "userId": user_id,
                "type": rec_type,
                "status": "REVIEW",
                "strategy_id": sid,
                "strategy_name": c["strategy_name"],
                "market": c["market"],
                "session": c["session"],
                "regime": c["regime"],
                "segment": seg,
                "cell": {k: c[k] for k in ("n", "win_rate", "avg_r")},
                "baseline": {k: st[k] for k in ("n", "win_rate", "avg_r")},
                "win_rate_delta_pp": delta,
                "claim": (f"{c['strategy_name']} on {c['market']} during {seg} "
                          f"underperforms: {c['win_rate']}% win rate vs "
                          f"{st['win_rate']}% overall (Δ{delta}pp, avg R "
                          f"{c['avg_r']} vs {st['avg_r']}, n={c['n']})."),
                "suggestion": (f"Consider removing the {c['session']} session "
                               f"for this strategy×pair (subject to your explicit "
                               f"approval)." if rec_type == "FILTER_SESSION" else
                               "Regime-segment weakness noted - respond with a "
                               "one-variable experiment, not a filter."),
                "never_auto_applied": True,
                "dedupe_key": key,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }))
            if len(created) >= max_new:
                return created
    return created


def list_recommendations(user_id: str, status: Optional[str] = None,
                         limit: int = 50) -> List[dict]:
    flt = {"userId": user_id}
    if status:
        flt["status"] = status
    try:
        return get_store().list("recommendations", filters=flt,
                                order_by="createdAt", desc=True, limit=limit)
    except Exception:
        return []


def set_status(user_id: str, rec_id: str, status: str,
               reason: str = "") -> Optional[dict]:
    """Lifecycle transition with an audit trail. Returns the updated rec."""
    from ..core.permissions import audit
    store = get_store()
    rec = store.get("recommendations", rec_id)
    if not rec or rec.get("userId") != user_id:
        return None
    prev = {"status": rec.get("status")}
    patch: Dict[str, Any] = {"status": status}
    applied = None
    if status == "APPLYING" and rec["type"] == "FILTER_SESSION":
        # DRAFT only: proposed sessions land on the strategy doc; activation
        # requires an explicit audited strategy PATCH - never auto-applied.
        sid = rec["strategy_id"]
        sdoc = store.list("strategies", filters={"id": sid}, limit=1)
        if not sdoc:
            return None
        current = sdoc[0].get("sessions") or ["Asian", "London", "NewYork", "Late"]
        proposed = [x for x in current if x != rec["session"]]
        store.update("strategies", sid,
                     {"proposed_sessions": proposed,
                      "proposed_change_note": (f"From recommendation {rec_id}: "
                                               f"drop {rec['session']} on "
                                               f"{rec['market']} "
                                               f"(Δ{rec.get('win_rate_delta_pp')}pp)")})
        patch["proposed_sessions"] = proposed
        applied = {"strategy_id": sid, "proposed_sessions": proposed}
    store.update("recommendations", rec_id, patch)
    audit(user_id, f"recommendation.{status.lower()}", None,
          prev, patch, reason or rec.get("claim", "")[:120])
    out = store.get("recommendations", rec_id)
    out["applied_draft"] = applied
    return out
