"""Per-strategy ACCOUNT-LEVEL risk rules (owner gold-pair implementation
guide 2026-11-02, section 5).

The guide is explicit: these rules "can't live inside the strategies. The
strategies don't see P&L, and both must share one counter." They live here
as data scoped by strategy id, so the strategy modules stay exactly as the
owner delivered them.

GOLD PAIR (strategy_gold_ny_pullback + strategy_gold_ny_orb):
  risk_usd                    75.0  1.5% of the $5k starting balance
  risk_halving_at_target_frac 0.7   equity >= start + 70% of the profit
                                    target -> risk halves ($75 -> $37.50)
  max_open_positions          1     ignore new signals while one is open
  max_spread_usd              0.50  skip entry above (research used 0.35)
  soft_stop_group             gold_ny  ONE daily counter shared by the pair
  daily_soft_stop_usd         150.0 no new entries after the pair's day
                                    P&L <= -$150 ...
  daily_max_losses            2     ... or 2 losing trades

The locked management plans (fixed SL, single TP, bar-count time exit,
no partials/BE/trail/AI) live in the trade manager
(aimanager.engine._GOLD_LOCKED_IDS + _gold_locked_manage). Entry refusals
from these gates are loud (EXECUTION_SKIPPED + Telegram) per the standing
missed-trade rule.

Unknown strategies get an empty rule set = the standard platform path
($50 dollar setting, Universal ladder) - nothing here touches them.
"""
from __future__ import annotations

from typing import Any, Dict, List

GOLD_NY_GROUP = "gold_ny"

STRATEGY_RISK_RULES: Dict[str, Dict[str, Any]] = {
    "strategy_gold_ny_pullback": {
        "scan_bars": 12000,   # D1 SMA200 regime needs ~10k+ 15M bars (guide section 3)
        "risk_usd": 75.0,
        "risk_halving_at_target_frac": 0.7,
        "max_open_positions": 1,
        "max_spread_usd": 0.50,
        "soft_stop_group": GOLD_NY_GROUP,
        "daily_soft_stop_usd": 150.0,
        "daily_max_losses": 2,
    },
    "strategy_gold_ny_orb": {
        "scan_bars": 12000,   # D1 SMA200 regime needs ~10k+ 15M bars (guide section 3)
        "risk_usd": 75.0,
        "risk_halving_at_target_frac": 0.7,
        "max_open_positions": 1,
        "max_spread_usd": 0.50,
        "soft_stop_group": GOLD_NY_GROUP,
        "daily_soft_stop_usd": 150.0,
        "daily_max_losses": 2,
    },
}


def rules_for(strategy_id) -> Dict[str, Any]:
    """Account-level rule set for a strategy (empty = standard path)."""
    return dict(STRATEGY_RISK_RULES.get(strategy_id or "", {}) or {})


def ids_for_group(group: str) -> List[str]:
    """Every strategy sharing a soft-stop counter (they see the same day)."""
    return [sid for sid, r in STRATEGY_RISK_RULES.items()
            if r.get("soft_stop_group") == group]


def halve_if_in_profit_protect(risk_usd: float, equity: float,
                               start_balance: float, profit_target_pct: float,
                               frac: float) -> float:
    """Guide section 5: once equity >= start + frac*target, risk halves.
    Returns the (possibly halved) risk in USD."""
    try:
        risk_usd = float(risk_usd)
        if (start_balance and profit_target_pct and frac
                and equity >= float(start_balance)
                * (1.0 + float(frac) * float(profit_target_pct) / 100.0)):
            return risk_usd / 2.0
    except Exception:
        pass
    return risk_usd
