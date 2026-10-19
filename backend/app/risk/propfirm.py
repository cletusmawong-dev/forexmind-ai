"""PROP-FIRM CHALLENGE ENGINE (owner brief 2026-10-10, section 6).

Reads each account's configured challenge rules (trading_accounts.prop_rules
- never assumed: absent rules => the account is treated as NOT challenge-
governed and every function returns {"governed": False} honestly), computes
the remaining risk budgets from live account truth, and caps position sizes
so an entry can never breach a daily or maximum drawdown limit.

Isolation: everything is keyed by account_id - accounts never share budgets.
Anti-recovery: after consecutive losses the cap tightens (risk never rises
to win losses back).

ADDITIVE by design: called from the execution path AFTER the existing
calc_lot/apply_lot_mode math; it can only SHRINK a lot or refuse the entry -
it never enlarges anything and never touches the existing risk engine.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# daily-risk budget as a fraction of the daily drawdown allowance
DAILY_RISK_FRACTION = 0.5
# extra safety margin kept between the stop and the DD line (fraction of DD)
DD_BUFFER_FRACTION = 0.15
# consecutive full-R losses before the cap starts tightening
TIGHTEN_AFTER_LOSSES = 2


def rules_for(account: Dict[str, Any]) -> Dict[str, Any]:
    """The account's configured challenge rules, or governed=False."""
    pr = account.get("prop_rules") or {}
    if not isinstance(pr, dict) or not pr.get("governed"):
        return {"governed": False}
    out = {"governed": True,
           "label": pr.get("label", "challenge"),
           "profit_target_pct": _pos(pr.get("profit_target_pct")),
           "daily_dd_pct": _pos(pr.get("daily_dd_pct")),
           "max_dd_pct": _pos(pr.get("max_dd_pct")),
           "dd_type": str(pr.get("dd_type", "static")),      # static | trailing
           "open_equity_restrictions": bool(pr.get("open_equity_restrictions")),
           "min_trading_days": _pos(pr.get("min_trading_days")),
           "restrictions": pr.get("restrictions") or []}
    if not out["daily_dd_pct"] and not out["max_dd_pct"]:
        return {"governed": False,
                "note": "rules present but no drawdown limits configured"}
    return out


def _num(v) -> Optional[float]:
    """Numeric or None (accepts negatives - P/L values are negative)."""
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _pos(v) -> Optional[float]:
    """Strictly positive number or None (percentages, balances)."""
    f = _num(v)
    return f if f and f > 0 else None


def budgets(account: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    """Remaining risk budgets for one account from live truth.

    account: trading_accounts doc (balance/equity/prop_rules)
    stats:   {"daily_pl": realized+floating today (negative = loss),
              "consecutive_losses": int, "peak_equity": trailing peak}
    """
    r = rules_for(account)
    if not r.get("governed"):
        return {"governed": False}
    bal = _pos(account.get("balance")) or 0.0
    eq = _num(account.get("equity"))
    eq = eq if eq is not None else bal
    daily_pl = _num(stats.get("daily_pl")) or 0.0
    peak = _pos(stats.get("peak_equity")) or max(bal, eq)

    daily_line = None
    if r["daily_dd_pct"]:
        daily_line = -r["daily_dd_pct"] / 100.0 * bal
    max_line = None
    if r["max_dd_pct"]:
        base = peak if r["dd_type"] == "trailing" else bal
        max_line = -r["max_dd_pct"] / 100.0 * base

    # headroom convention: POSITIVE = room left before the line
    daily_left = (daily_pl - daily_line) if daily_line is not None else None
    # max-DD: static is measured from the starting balance, trailing from the
    # peak equity; current floating equity counts (section 6).
    change = (eq - peak) if r["dd_type"] == "trailing" else (eq - bal)
    max_left = (change - max_line) if max_line is not None else None

    con = int(stats.get("consecutive_losses") or 0)
    tighten = max(0.25, 1.0 - 0.25 * max(0, con - TIGHTEN_AFTER_LOSSES + 1))
    return {"governed": True, "label": r["label"],
            "daily_dd_line_usd": daily_line, "max_dd_line_usd": max_line,
            "daily_left_usd": daily_left, "max_left_usd": max_left,
            "profit_target_usd": (r["profit_target_pct"] / 100.0 * bal
                                  if r["profit_target_pct"] else None),
            "target_progress_pct": (round(100.0 * (eq - bal) /
                                          (r["profit_target_pct"] / 100.0 * bal), 1)
                                    if r["profit_target_pct"] and bal and eq >= bal
                                    else 0.0),
            "cap_multiplier": round(tighten, 2),
            "dd_type": r["dd_type"],
            "breach_now": bool((daily_left is not None and daily_left <= 0)
                               or (max_left is not None and max_left <= 0))}


def pre_trade_check(account: Dict[str, Any], stats: Dict[str, Any],
                    stop_usd_per_lot: float, existing_open_risk_usd: float = 0.0
                    ) -> Dict[str, Any]:
    """Maximum allowed risk for the NEXT trade (USD) and verdict.

    stop_usd_per_lot: USD lost per 1.0 lot if the SL is hit (from the
    existing calc conventions). Returns allowed=False with a reason when the
    trade must not be sent (budget exhausted or breach imminent)."""
    b = budgets(account, stats)
    if not b.get("governed"):
        return {"governed": False, "allowed": True}
    if b.get("breach_now"):
        return {"governed": True, "allowed": False,
                "reason": "drawdown budget already exhausted - no new risk",
                "budgets": b}
    caps = []
    daily_left = b.get("daily_left_usd")
    if daily_left is not None:
        usable = max(0.0, daily_left) * DAILY_RISK_FRACTION * (1 - DD_BUFFER_FRACTION)
        usable -= max(0.0, existing_open_risk_usd)
        caps.append(max(0.0, usable))
    max_left = b.get("max_left_usd")
    if max_left is not None:
        usable = max(0.0, max_left) * (1 - DD_BUFFER_FRACTION)
        usable -= max(0.0, existing_open_risk_usd)
        caps.append(max(0.0, usable))
    cap_usd = min(caps) if caps else None
    if cap_usd is None:
        return {"governed": True, "allowed": True, "budgets": b,
                "note": "no drawdown caps configured"}
    if cap_usd <= 0:
        return {"governed": True, "allowed": False,
                "reason": "remaining risk budget is zero after buffer/open risk",
                "budgets": b}
    cap_lots = cap_usd / stop_usd_per_lot if stop_usd_per_lot > 0 else 0.0
    return {"governed": True, "allowed": True, "cap_usd": round(cap_usd, 2),
            "cap_lots": round(cap_lots, 2), "budgets": b,
            "cap_multiplier": b.get("cap_multiplier", 1.0)}


def cap_lots(account: Dict[str, Any], stats: Dict[str, Any], lots: float,
             stop_usd_per_lot: float, existing_open_risk_usd: float = 0.0
             ) -> Dict[str, Any]:
    """Shrink-only lot cap for the execution path. Never enlarges."""
    chk = pre_trade_check(account, stats, stop_usd_per_lot,
                          existing_open_risk_usd)
    if not chk.get("governed") or not chk.get("allowed"):
        return {**chk, "lots": 0.0}
    capped = lots
    mult = chk.get("cap_multiplier", 1.0)
    limit = chk.get("cap_lots")
    if limit is not None:
        capped = min(capped, limit * mult)
    capped = max(0.0, capped)
    return {"governed": True, "allowed": capped > 0,
            "lots": round(capped, 2), "requested": lots,
            "shrunk": capped < lots - 1e-9,
            "reason": None if capped > 0 else "prop-firm cap leaves no size",
            "budgets": chk.get("budgets")}
