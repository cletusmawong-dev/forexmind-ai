"""DETERMINISTIC POSITION POLICY (owner brief 2026-10-10, sections 3 & 5).

Evidence-driven management of OPEN trades using market food (1H/4H levels)
and completed-candle evidence. Runs inside the existing TradeManager review
pipeline AFTER the deterministic TP ladder and BEFORE the AI/LLM path: when
this policy has an opinion, that opinion IS the decision (recorded with
source="deterministic_policy") and still flows through the SAME
risk_gate.validate_action -> execute_plan path as every other decision.
The LLM can annotate but can never bypass or override the gate.

Hard principles (verbatim from the brief):
  * never close a position merely because price TOUCHES a key level;
  * a wick through a level is not proof - completed CLOSES, follow-through
    and structure decide (true breakout vs false breakout);
  * never move a stop farther away; never loosen risk to preserve a trade
    (risk_gate._tightens enforces this independently - belt & braces);
  * no forced 1:3/1:4: bigger winners are allowed while evidence supports
    holding; profits are protected when the thesis weakens;
  * HOLD is the default. Data problems mean NO ACTION, never a guess.

Decision rules (deterministic, first match wins):
  CLOSE   thesis_invalid: a completed candle CLOSED beyond the nearest
          opposing 1H level AGAINST the trade WITH follow-through (the next
          completed candle also closes further against), and the trade is
          not beyond +1R.
  PROTECT (to breakeven) thesis_weak: trade at/above +0.75R AND price
          reached an opposing 1H major/internal level AND the last completed
          candle shows rejection (close back on the trade's side of the
          level) - protect accumulated gains.
  PROTECT (to breakeven) confirmation_reached: trade beyond +1R (only when
          the position has no strategy break-even marker already applied -
          see `has_strategy_be`).
  PARTIAL_PROFIT (35%) + PROTECT (to breakeven) bank_into_level: trade
          beyond +1R, price within 0.5R of an opposing level or liquidity
          pool, and the last candle is NOT a confirmed breakout of it.
  HOLD    otherwise (with the reason recorded).
Insufficient/stale candles -> HOLD (no_data) - never a guess.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..analysis import market_food

PARTIAL_FRACTION = 0.35
NEAR_LEVEL_R = 0.5          # "approaching a level" band
BE_R = 1.0                  # confirmation threshold for breakeven protect
WEAK_R = 0.75               # accumulated-profit protection threshold
MIN_CANDLES_1H = 60


def _candle_evidence(d: Optional[pd.DataFrame], level: float,
                     is_buy: bool) -> Dict[str, Any]:
    """True/false breakout + rejection evidence around one level IN THE
    TRADE'S PATH (for a long the path is up, for a short down), judged on
    COMPLETED closes (wick-through alone is explicitly insufficient)."""
    from ..strategies.gold_specs.indicators import prepare as _prep
    dd = _prep(d)
    if dd is None or len(dd) < 3:
        return {"ok": False, "reason": "not enough completed candles"}
    last = dd.iloc[-1]
    prev = dd.iloc[-2]
    above = is_buy                  # long: opposing levels lie ABOVE
    closed_beyond = (float(last["close"]) > level) if above \
        else (float(last["close"]) < level)
    prev_closed_beyond = (float(prev["close"]) > level) if above \
        else (float(prev["close"]) < level)
    follow_through = closed_beyond and prev_closed_beyond
    touched = ((float(last["high"]) >= level) if above
               else (float(last["low"]) <= level))
    wick_through = touched and not closed_beyond
    rejected = touched and not closed_beyond and (
        (float(last["close"]) < float(last["open"])) if above
        else (float(last["close"]) > float(last["open"])))
    return {"ok": True, "closed_beyond": bool(closed_beyond),
            "follow_through": bool(follow_through),
            "wick_through_only": bool(wick_through),
            "rejection_close": bool(rejected and not closed_beyond),
            "last_close": float(last["close"])}


def evaluate(pos: Dict[str, Any], risk: float, r_multiple: float,
             food: Dict[str, Any], h1: Optional[pd.DataFrame],
             has_strategy_be: bool = False) -> Dict[str, Any]:
    """One deterministic review of one open position.

    pos  : broker position dict (type BUY/SELL, price_open, price_current, sl)
    risk : initial risk per unit (|entry - initial SL|); > 0 required
    r_multiple: current floating R (computed by the caller from broker truth)
    food : market_food.analyze() output for this market
    h1   : completed 1H candles (close-evidence around the levels)
    has_strategy_be: True when the strategy's own break-even marker is
          already known applied (spec engines); the policy then skips its
          own breakeven rule (never duplicates a strategy rule).

    Level semantics:
      level AHEAD (in the trade's path)  -> banking / approach decisions
      level BEHIND (support for longs, resistance for shorts)
                                         -> thesis invalidation evidence
    """
    try:
        is_buy = str(pos.get("type", "")).upper() in ("BUY", "LONG")
        entry = float(pos.get("price_open") or 0)
        price = float(pos.get("price_current") or 0)
        if entry <= 0 or price <= 0 or risk <= 0:
            return {"action": "HOLD", "reason_code": "NO_DATA",
                    "detail": "position/risk data incomplete - no action"}
        direction = "LONG" if is_buy else "SHORT"
        h1_levels = food.get("h1") or {}
        if not h1_levels.get("ok"):
            return {"action": "HOLD", "reason_code": "NO_DATA",
                    "detail": f"market food unavailable "
                              f"({h1_levels.get('reason', 'no levels')}) - hold"}

        room = market_food.room_to_next_level(h1_levels, price, direction, risk)
        nxt = room.get("next_level") if room.get("ok") else None

        # nearest mapped level BEHIND the trade (protection side)
        behind_pool = [lv["price"] for lv in
                       h1_levels.get("majors", []) + h1_levels.get("internal", [])
                       if (lv["price"] < price if is_buy else lv["price"] > price)]
        behind_pool += (h1_levels.get("sell_side_liquidity") or []) if is_buy \
            else (h1_levels.get("buy_side_liquidity") or [])
        behind = (max(behind_pool) if is_buy else min(behind_pool)) \
            if behind_pool else None
        ev_behind = _candle_evidence(h1, behind, not is_buy) if behind \
            else {"ok": False, "reason": "no mapped level behind"}
        ev_ahead = _candle_evidence(h1, nxt, is_buy) if nxt \
            else {"ok": False, "reason": "no mapped opposing level"}

        notes: List[Dict[str, Any]] = []
        if room.get("ok") and nxt:
            notes.append({"observation": "level_ahead", "level": nxt,
                          "distance_r": room.get("distance_r"),
                          "closed_beyond": ev_ahead.get("closed_beyond")})
        if behind:
            notes.append({"observation": "protection_level", "level": behind,
                          "closed_beyond": ev_behind.get("closed_beyond")})
        # a wick through a level alone -> internal observation, NO action
        for tag, ev in (("ahead", ev_ahead), ("behind", ev_behind)):
            if ev.get("ok") and ev.get("wick_through_only") \
                    and not ev.get("follow_through"):
                notes.append({"observation": "wick_through_level_no_close",
                              "side": tag,
                              "detail": "noted internally - not breakout "
                                        "evidence"})

        # ---- CLOSE: confirmed structural invalidation of the thesis
        # (completed closes beyond the PROTECTION level with follow-through)
        if ev_behind.get("ok") and ev_behind.get("follow_through") \
                and r_multiple < 1.0:
            return {"action": "EXIT", "reason_code": "THESIS_INVALID",
                    "detail": (f"completed candles closed beyond the 1H "
                               f"protection level {behind} with follow-through "
                               f"against the trade at {r_multiple:+.2f}R - "
                               f"thesis invalid"),
                    "level": behind, "observations": notes,
                    "source": "deterministic_policy"}

        # ---- PARTIAL + PROTECT: banking into a level without breakout proof
        if (r_multiple >= BE_R and nxt and ev_ahead.get("ok")
                and not ev_ahead.get("closed_beyond")
                and room.get("distance_r") is not None
                and room["distance_r"] <= NEAR_LEVEL_R):
            return {"action": "PARTIAL_PROFIT", "partial_fraction": PARTIAL_FRACTION,
                    "recommended_sl": entry, "reason_code": "BANK_INTO_LEVEL",
                    "detail": (f"+{r_multiple:.2f}R and price is "
                               f"{room['distance_r']}R from the opposing 1H "
                               f"level {nxt} without a confirmed close beyond "
                               f"it - bank a partial, protect the rest at "
                               f"entry"),
                    "level": nxt, "observations": notes,
                    "source": "deterministic_policy"}

        # ---- PROTECT: rejection at the ahead level after solid gains
        if (r_multiple >= WEAK_R and nxt and ev_ahead.get("ok")
                and ev_ahead.get("rejection_close")):
            return {"action": "PROTECT", "recommended_sl": entry,
                    "reason_code": "REJECTION_AT_LEVEL",
                    "detail": (f"+{r_multiple:.2f}R with a completed-candle "
                               f"rejection at the opposing 1H level {nxt} - "
                               f"protect gains at breakeven"),
                    "level": nxt, "observations": notes,
                    "source": "deterministic_policy"}

        # ---- PROTECT at +1R (never duplicating a strategy break-even rule)
        if r_multiple >= BE_R and not has_strategy_be:
            return {"action": "PROTECT", "recommended_sl": entry,
                    "reason_code": "CONFIRMATION_REACHED",
                    "detail": f"+{r_multiple:.2f}R confirmed - stop to entry",
                    "observations": notes, "source": "deterministic_policy"}

        return {"action": "HOLD", "reason_code": "THESIS_INTACT",
                "detail": (f"{r_multiple:+.2f}R, "
                           + (f"next opposing level {nxt} at "
                              f"{room.get('distance_r')}R - room for the "
                              f"thesis" if nxt else
                              "open structure ahead - no mapped opposing "
                              "level")),
                "observations": notes, "source": "deterministic_policy"}
    except Exception as exc:                        # policy must never throw
        return {"action": "HOLD", "reason_code": "POLICY_ERROR",
                "detail": f"policy error ({type(exc).__name__}) - hold",
                "source": "deterministic_policy"}
