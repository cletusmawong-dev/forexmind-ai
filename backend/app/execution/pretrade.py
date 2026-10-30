"""Universal pre-trade validation layer (owner brief 2026-10-24, s.3).

ONE gate every order must pass - vps bridge and manual queue alike - before
anything is sent toward MT5. The validator NEVER repairs an order: a failing
check blocks the order and records a clear reason. Strategy rules (stop
distance, target multiple) are read from the signal itself and enforced
against the ACTUAL order prices, so a strategy's configured R:R can never
silently differ from the broker order's R:R.

Checks (brief s.3):
  1. symbol is selected and supported
  2. strategy is enabled for that symbol (lifecycle LIVE, doc ACTIVE)
  3. BUY/SELL direction is correct (SL and TP on the right sides)
  4. entry, SL and TP prices are valid (finite, positive, non-zero risk)
  5. actual planned R:R (|tp-entry| / |entry-sl|) matches the strategy's
     configured requirement (signal rr_primary) within rounding tolerance
  6. position size respects the risk-per-trade setting (+ lot-mode
     multiplier) and the account balance; drawdown budgets are checked by
     the prop-firm cap upstream and re-asserted here
  7. duplicate-order and one-order-per-setup protections ran upstream
     (asserted flag - the validator refuses to run without them)

Every rejection -> (False, code, human reason) and the caller records
execution_status=REJECTED_PRETRADE with the code. Never raises.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional, Tuple

# Order R:R must match the strategy's configured R:R within this tolerance.
# Absolute floor 0.02 covers broker-rounding of the levels; the relative
# term covers wide-ATR stops where 2 decimals still matter.
RR_ABS_TOL = float(os.getenv("EXECUTION_RR_ABS_TOL", "0.02"))
RR_REL_TOL = float(os.getenv("EXECUTION_RR_REL_TOL", "0.02"))

# Entry-drift guard (vps): the live market price at execution time must sit
# within this fraction of the signal's stop distance from the signal's
# intended entry. The specs time their entries at a candle open; an order
# fired minutes later into a moved market silently destroys realized R:R
# (prod evidence 2026-10-20..23: intended 1.25, realized 0.31-0.71). 0.25R
# of drift is the ceiling of acceptable slippage - beyond that the setup no
# longer matches the spec and the order is REFUSED, not re-anchored.
MAX_ENTRY_DRIFT_R = float(os.getenv("EXECUTION_MAX_ENTRY_DRIFT_R", "0.25"))


def planned_rr(entry: float, sl: float, tp: Optional[float]) -> Optional[float]:
    """Reward distance / risk distance of the ACTUAL order prices."""
    try:
        risk = abs(float(entry) - float(sl))
        reward = abs(float(tp) - float(entry))
        if risk <= 0 or reward <= 0:
            return None
        return reward / risk
    except (TypeError, ValueError):
        return None


def _same_price(a: Optional[float], b: Optional[float]) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return False


def _finite_positive(*vals: float) -> bool:
    try:
        return all(math.isfinite(float(v)) and float(v) > 0 for v in vals)
    except (TypeError, ValueError):
        return False


def validate_order(
    *,
    market: str,
    direction: str,
    entry: float,
    sl: float,
    tp: Optional[float],
    lots: float,
    intended_rr: Optional[float],
    declared_tps: Optional[list] = None,   # the signal's own tp1..tp3 (non-null)
    symbol_known: bool,
    strategy_status: Optional[str],
    lifecycle: str,
    pip_size: Optional[float],
    pip_value: Optional[float],
    min_stop_pips: float,
    balance: float,
    eff_risk_pct: float,        # risk_per_trade_pct already scaled by lot-mode
    lots_cap: float,
    dup_checked: bool = True,
    budgets_allowed: bool = True,
    allow_no_tp: bool = False,     # trail strategies (no fixed target by rule)
    reference_price: Optional[float] = None,   # live market ref (vps path)
) -> Tuple[bool, str, str]:
    """Returns (ok, code, reason). Never raises."""
    try:
        # 1. symbol selected & supported -------------------------------------
        if not market or not str(market).strip():
            return False, "SYMBOL_MISSING", "order has no symbol"
        if not symbol_known:
            return (False, "SYMBOL_UNSUPPORTED",
                    f"{market} is not a supported/configured instrument - "
                    "refusing to fall back to any other symbol")
        if strategy_status is not None and strategy_status != "ACTIVE":
            return (False, "STRATEGY_NOT_ACTIVE",
                    f"strategy doc status is {strategy_status}")
        if lifecycle != "LIVE":
            return False, "STRATEGY_NOT_LIVE", f"strategy lifecycle is {lifecycle}"

        # 3. direction & 4. price validity -----------------------------------
        dirn = str(direction or "").upper()
        if dirn not in ("BUY", "SELL"):
            return False, "DIRECTION_INVALID", f"direction '{direction}' is not BUY/SELL"
        if not _finite_positive(entry, sl):
            return False, "PRICES_INVALID", "entry and SL must be finite positive numbers"
        risk_dist = abs(entry - sl)
        if risk_dist <= 0:
            return False, "RISK_ZERO", "entry equals SL - zero risk distance"
        # side correctness: for BUY the SL is below and the TP above entry;
        # for SELL inverted. Catches swapped/wrong-side stops before the
        # broker does. Trail strategies (no fixed target by rule) are checked
        # on the stop side only - the exit is the managed trail, never a TP.
        if allow_no_tp:
            if tp is not None and _finite_positive(tp):
                if dirn == "BUY" and not (sl < entry < tp):
                    return (False, "SIDES_INCONSISTENT",
                            f"BUY requires SL < entry < TP (got SL {sl}, entry {entry}, TP {tp})")
                if dirn == "SELL" and not (tp < entry < sl):
                    return (False, "SIDES_INCONSISTENT",
                            f"SELL requires TP < entry < SL (got TP {tp}, entry {entry}, SL {sl})")
            elif tp not in (None, 0, 0.0):
                return False, "PRICES_INVALID", "TP present but invalid - refusing"
            else:
                # no-TP order: the STOP side must still be correct
                if dirn == "BUY" and not sl < entry:
                    return (False, "SIDES_INCONSISTENT",
                            f"BUY requires SL < entry (got SL {sl}, entry {entry})")
                if dirn == "SELL" and not entry < sl:
                    return (False, "SIDES_INCONSISTENT",
                            f"SELL requires entry < SL (got entry {entry}, SL {sl})")
        else:
            if tp is None or not _finite_positive(tp):
                return (False, "PRICES_INVALID",
                        "TP missing or invalid - refusing to send an order without a target")
            if dirn == "BUY" and not (sl < entry < tp):
                return (False, "SIDES_INCONSISTENT",
                        f"BUY requires SL < entry < TP (got SL {sl}, entry {entry}, TP {tp})")
            if dirn == "SELL" and not (tp < entry < sl):
                return (False, "SIDES_INCONSISTENT",
                        f"SELL requires TP < entry < SL (got TP {tp}, entry {entry}, SL {sl})")
        if pip_size and abs(entry - sl) < min_stop_pips * pip_size:
            return (False, "STOP_TOO_TIGHT",
                    f"stop distance {abs(entry - sl) / pip_size:.1f} pips < broker "
                    f"minimum {min_stop_pips:g}")

        # 5. actual planned R:R vs the strategy's configured R:R --------------
        #    Single-target signals (both live specs): the order RR must EQUAL
        #    the configured RR. Multi-TP signals: the TP-level picker may
        #    choose among the signal's OWN declared targets - so the rule is
        #    "the submitted TP is one the strategy defined", never invented.
        #    Trail strategies (allow_no_tp, no target by rule): no R:R exists
        #    to compute - the exits are the managed trail + time exit.
        multi = len([t for t in (declared_tps or []) if t]) >= 2
        _no_tp_order = (tp is None or tp in (0, 0.0))
        rr = planned_rr(entry, sl, tp)
        if allow_no_tp and not multi and _no_tp_order:
            pass                       # trail strategy: no fixed R:R exists
        elif rr is None:
            return False, "RR_INVALID", "could not compute reward/risk from order prices"
        elif multi:
            if not any(_same_price(tp, t) for t in declared_tps if t):
                return (False, "TP_NOT_DECLARED",
                        f"order TP {tp} is none of the strategy's declared "
                        f"targets {declared_tps} - refusing the invented target")
        elif intended_rr:
            tol = max(RR_ABS_TOL, RR_REL_TOL * abs(float(intended_rr)))
            if abs(rr - float(intended_rr)) > tol:
                return (False, "RR_MISMATCH",
                        f"order R:R {rr:.3f} does not match the strategy's "
                        f"configured {float(intended_rr):.3f} (tol {tol:.3f}) - "
                        "the order is refused, not corrected")

        # 6. sizing vs risk setting & balance ---------------------------------
        if lots <= 0:
            return False, "LOTS_INVALID", "lot size computed to zero - sizing failed loudly"
        if lots > lots_cap:
            return False, "LOTS_OVER_CAP", f"lots {lots} exceed the hard cap {lots_cap}"
        if pip_size and pip_value and balance > 0 and eff_risk_pct > 0:
            usd_risk = (risk_dist / pip_size) * pip_value * lots
            budget = balance * (eff_risk_pct / 100.0)
            # 1.35 (owner postmortem 2026-10-30, aligned with the
            # RISK_USD_MISMATCH guard): lot-step rounding on wide-stop
            # instruments (NAS100 0.027 -> 0.03) shifts risk ~+12%; the
            # dollar guard still refuses everything beyond 1.35x.
            if usd_risk > budget * 1.35 + 1e-9:
                return (False, "RISK_OVER_BUDGET",
                        f"order risks ${usd_risk:.2f} ({lots} lots, {risk_dist / pip_size:.1f} pips) "
                        f"but the budget is ${budget:.2f} ({eff_risk_pct:.2f}% of "
                        f"${balance:.2f})")

        # 7. upstream protections asserted ------------------------------------
        if not dup_checked:
            return (False, "DUP_CHECK_MISSING",
                    "duplicate/one-order-per-setup check did not run - refusing")
        if not budgets_allowed:
            return False, "BUDGET_REFUSED", "prop-firm budget refused the entry"

        # entry-drift guard (vps path only: the caller passes the broker's
        # own last close; None = check not applicable, e.g. manual queue) ----
        if reference_price is not None and _finite_positive(reference_price):
            drift = abs(float(reference_price) - float(entry))
            if drift > MAX_ENTRY_DRIFT_R * risk_dist:
                return (False, "ENTRY_STALE",
                        f"market moved {drift:.5f} from the intended entry {entry} "
                        f"(>{MAX_ENTRY_DRIFT_R:g}x the {risk_dist:.5g} stop distance) - "
                        "the setup no longer matches the spec; order refused, "
                        "not re-anchored")

        return True, "OK", "all pre-trade checks passed"
    except Exception as exc:            # fail-CLOSED: a broken validator must
        code = type(exc).__name__       # never wave an order through
        return False, "VALIDATOR_ERROR", f"pre-trade validator error ({code}) - order blocked"
