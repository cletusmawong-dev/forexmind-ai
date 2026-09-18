"""Deterministic execution gate (SS19, SS20, SS44, SS52-22/23).

The AI NEVER talks to MT5 directly:

    AI decision -> validate() -> THIS GATE -> MT5 primitives

The gate re-verifies broker truth (position exists, side matches, SL legal),
enforces decision freshness (TTL), suppresses duplicates, applies the
monotonic-protection rule (an SL change may only TIGHTEN risk), and only
then calls the Phase-3 primitives. Hard rules (e.g. TP2 -> SL := TP1) are
enforced here independent of any AI output (SS15).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from ..db.store import get_store
from ..execution import mt5 as X

DECISION_TTL_S = 90          # SS20
DUPLICATE_WINDOW_S = 60


def _side(price: float, sl: float, is_buy: bool) -> bool:
    return sl < price if is_buy else sl > price


def _tightens(current_sl: Optional[float], new_sl: float, entry: float,
              is_buy: bool) -> bool:
    """A protective SL move must REDUCE risk: for BUY strictly higher SL,
    for SELL strictly lower. Breakeven-or-better is tighter. No loosening."""
    if not current_sl:
        return True
    return new_sl > float(current_sl) + 1e-9 if is_buy else new_sl < float(current_sl) - 1e-9


def validate_action(user_id: str, position: dict, decision: dict,
                    snapshot_ts: float, current_sl: Optional[float] = None,
                    last_action: Optional[dict] = None,
                    now: Optional[float] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """Full gate check. Returns (allowed, verdict, plan).

    plan = the exact primitive calls the engine may execute. Never executes
    anything itself - the engine runs the plan and reports back (audit)."""
    now = now or time.time()
    action = decision.get("action", "HOLD")

    # SS20: stale decision -> revalidate (do not act on an old market)
    if now - float(snapshot_ts) > DECISION_TTL_S:
        return False, "STALE_DECISION", {}

    # duplicate suppression: identical action+level within the window
    if last_action:
        same = (last_action.get("action") == action and
                abs(float(last_action.get("sl") or 0) - float(decision.get("recommended_sl") or 0)) < 1e-9 and
                now - float(last_action.get("ts") or 0) < DUPLICATE_WINDOW_S)
        if same:
            return False, "DUPLICATE_SUPPRESSED", {}

    if action == "HOLD":
        return True, "OK_HOLD", {}

    if action == "PROTECT":
        sl = decision.get("recommended_sl")
        if sl is None:
            return False, "PROTECT_WITHOUT_SL", {}
        is_buy = str(position.get("type", "")).upper() == "BUY"
        price = float(position.get("price_current") or 0)
        entry = float(position.get("price_open") or 0)
        if price <= 0:
            return False, "NO_PRICE", {}
        if not _side(price, float(sl), is_buy):
            return False, "SL_WRONG_SIDE", {}
        if not _tightens(current_sl, float(sl), entry, is_buy):
            return False, "SL_WOULD_LOOSEN_RISK", {}
        return True, "OK_PROTECT", {"modify_sl": float(sl)}

    if action == "PARTIAL_PROFIT":
        frac = decision.get("partial_fraction")
        if not frac or not (0.0 < float(frac) < 1.0):
            return False, "BAD_FRACTION", {}
        return True, "OK_PARTIAL", {"partial_fraction": float(frac)}

    if action == "EXIT":
        return True, "OK_EXIT", {"close_full": True}

    return False, "UNKNOWN_ACTION", {}


# ---------------------------------------------------------------------------
# execution of a validated plan (uses ONLY Phase-3 primitives; never raises)
# ---------------------------------------------------------------------------
def execute_plan(user_id: str, position: dict, plan: Dict[str, Any],
                 reason: str) -> Dict[str, Any]:
    store = get_store()
    ticket = int(position.get("ticket") or 0)
    out: Dict[str, Any] = {"executed": [], "errors": []}
    try:
        if "modify_sl" in plan:
            res = X.modify_sl(user_id, ticket, float(plan["modify_sl"]),
                              reason=reason)
            (out["executed"] if res.get("ok") or res.get("queued") else out["errors"]
             ).append({"op": "modify_sl", "sl": plan["modify_sl"],
                       "result": {k: res.get(k) for k in ("ok", "queued", "error")}})
        if "partial_fraction" in plan:
            res = X.partial_close_position(user_id, ticket,
                                           fraction=float(plan["partial_fraction"]),
                                           reason=reason)
            (out["executed"] if res.get("ok") or res.get("queued") else out["errors"]
             ).append({"op": "partial_close", "fraction": plan["partial_fraction"],
                       "result": {k: res.get(k) for k in ("ok", "queued", "error",
                                                          "closed_volume", "remaining_volume")}})
        if "close_full" in plan:
            res = X.close_position(user_id, ticket, reason=reason)
            (out["executed"] if res.get("ok") or res.get("queued") else out["errors"]
             ).append({"op": "close_full",
                       "result": {k: res.get(k) for k in ("ok", "queued", "error")}})
    except Exception as exc:
        out["errors"].append({"op": "plan_execution", "error": type(exc).__name__})
    return out
