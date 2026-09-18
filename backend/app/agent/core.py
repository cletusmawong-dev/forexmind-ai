"""Agent core - objective system, status, activity (SPEC §13, §14, §33, §34).

"My objective is +1.5% today." - NOT "I must make +1.5% today."
The goal never forces trading (SPEC §1, §55).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..config import settings
from ..db.store import get_store
from ..learning.versions import active_version


DEFAULT_GOALS = {
    "account_balance": 20.0,
    "daily_objective_pct": 1.5,
    "weekly_objective_pct": 5.0,
}
DEFAULT_RISK = {
    "risk_per_trade_pct": 1.0,
    "max_daily_loss_pct": 3.0,
    "max_consecutive_losses": 4,
    "max_signals_per_day": 6,
    "min_rr": 1.5,
    "sessions": ["London", "NewYork", "Asian", "Late"],
    "market_sessions": {},
    "daily_profit_target_usd": 0.0,
    "daily_loss_limit_usd": 0.0,
    "on_loss_limit": "stop_entries",
    "session_hours": {},
    "session_tz": "UTC",
    "allowed_markets": ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"],
    "signal_timeframes": ["15M"],
    "account_type": "personal",
    "prop_rules": None,
}


def ensure_user_docs(user_id: str) -> None:
    store = get_store()
    if not store.list("agent_goals", filters={"userId": user_id}, limit=1):
        store.create("agent_goals", {"userId": user_id, **DEFAULT_GOALS})
    if not store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1):
        store.create("settings", {"userId": user_id, "kind": "risk", **DEFAULT_RISK})


def get_goals(user_id: str) -> Dict[str, Any]:
    store = get_store()
    doc = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    if not doc:
        ensure_user_docs(user_id)
        doc = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    return {k: v for k, v in doc[0].items() if k not in ("id", "userId", "createdAt", "updatedAt")}


def get_risk(user_id: str) -> Dict[str, Any]:
    store = get_store()
    doc = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
    if not doc:
        ensure_user_docs(user_id)
        doc = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
    fields = {k: v for k, v in doc[0].items() if k not in ("id", "userId", "kind", "createdAt", "updatedAt")}
    # defaults for fields added after the doc was created (e.g. signal_timeframes)
    return {**{k: v for k, v in DEFAULT_RISK.items() if k not in fields}, **fields}


def patch_goals(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    store = get_store()
    doc = store.list("agent_goals", filters={"userId": user_id}, limit=1)[0]
    allowed = {"account_balance", "daily_objective_pct", "weekly_objective_pct"}
    return store.update("agent_goals", doc["id"],
                        {k: float(v) for k, v in patch.items() if k in allowed})


def patch_risk(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    store = get_store()
    if not store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1):
        ensure_user_docs(user_id)
    doc = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)[0]
    allowed = {"risk_per_trade_pct", "max_daily_loss_pct", "max_consecutive_losses",
               "max_signals_per_day", "min_rr", "sessions", "allowed_markets",
               "market_sessions", "session_hours", "session_tz",
               "daily_profit_target_usd", "daily_loss_limit_usd", "on_loss_limit",
               "signal_timeframes", "account_type", "prop_rules"}
    clean_keys = {k: v for k, v in patch.items() if k in allowed}
    if "signal_timeframes" in clean_keys:
        tfs = clean_keys["signal_timeframes"]
        if not isinstance(tfs, list) or not tfs or \
                any(t not in ("15M", "1H", "4H", "1D") for t in tfs):
            raise ValueError("signal_timeframes must be a non-empty list from: 15M, 1H, 4H, 1D")
    if "account_type" in clean_keys:
        at = str(clean_keys["account_type"] or "personal").strip().lower()
        if at not in ("personal", "propfirm"):
            raise ValueError("account_type must be 'personal' or 'propfirm'")
        clean_keys["account_type"] = at
    if "prop_rules" in clean_keys:
        pr = clean_keys["prop_rules"]
        if pr is None:
            clean_keys["prop_rules"] = None
        else:
            if not isinstance(pr, dict):
                raise ValueError("prop_rules must be an object with prop-firm fields")
            clean: Dict[str, Any] = {}
            for k in ("daily_drawdown_pct", "max_total_drawdown_pct", "profit_target_pct",
                      "daily_dd_buffer_pct", "account_start_balance"):
                if pr.get(k) is None:
                    continue
                try:
                    clean[k] = float(pr[k])
                except (TypeError, ValueError):
                    raise ValueError(f"prop_rules.{k} must be a number")
            if "daily_drawdown_pct" in clean and not (0 < clean["daily_drawdown_pct"] <= 50):
                raise ValueError("prop_rules.daily_drawdown_pct must be within (0, 50]")
            if "max_total_drawdown_pct" in clean and not (0 < clean["max_total_drawdown_pct"] <= 90):
                raise ValueError("prop_rules.max_total_drawdown_pct must be within (0, 90]")
            if "profit_target_pct" in clean and not (0 < clean["profit_target_pct"] <= 200):
                raise ValueError("prop_rules.profit_target_pct must be within (0, 200]")
            if "daily_dd_buffer_pct" in clean and not (0 <= clean["daily_dd_buffer_pct"] <= 50):
                raise ValueError("prop_rules.daily_dd_buffer_pct must be within [0, 50]")
            if "account_start_balance" in clean and clean["account_start_balance"] < 0:
                raise ValueError("prop_rules.account_start_balance cannot be negative")
            clean_keys["prop_rules"] = clean or None
    clean = {}
    numeric = {"risk_per_trade_pct", "max_daily_loss_pct", "min_rr"}
    ints = {"max_consecutive_losses", "max_signals_per_day"}
    for k, v in clean_keys.items():
        if k in ints:
            clean[k] = int(v)
        elif k in numeric:
            clean[k] = float(v)
        else:  # lists, dicts (prop_rules), strings (account_type), None
            clean[k] = v
    return store.update("settings", doc["id"], clean)


def user_signal_timeframes(user_id: str) -> List[str]:
    """Entry timeframes the scanner scans for THIS user (user directive
    2026-09-16: default 15M only). Existing signals always finish tracking
    regardless of this setting."""
    try:
        risk = get_risk(user_id)
        tfs = risk.get("signal_timeframes") or ["15M"]
        valid = [t for t in tfs if t in ("15M", "1H", "4H", "1D")]
        return valid or ["15M"]
    except Exception:
        return ["15M"]


def log(message: str, kind: str = "INFO", market: Optional[str] = None) -> None:
    store = get_store()
    store.create("agent_activity", {"userId": None, "kind": kind,
                                    "message": message, "market": market})


def log_scanning(markets: list, timeframe: str) -> None:
    for m in markets:
        log(f"Scanning {m} {timeframe}...", kind="SCAN", market=m)


# ----------------------------------------------------------------------
def compute_progress(user_id: str) -> Dict[str, Any]:
    """Objective progress from completed signal outcomes, expressed in % of
    account using risk_per_trade_pct (documented assumption, no fabrication)."""
    store = get_store()
    goals = get_goals(user_id)
    risk = get_risk(user_id)
    risk_pct = float(risk.get("risk_per_trade_pct", 1.0))

    now = pd.Timestamp.utcnow()
    day_start = now.strftime("%Y-%m-%d")
    week_start = (now - pd.Timedelta(days=now.dayofweek)).strftime("%Y-%m-%d")

    completed = store.list("signals", filters={"userId": user_id, "completed": True},
                           limit=1000)
    daily_r = sum(s.get("r_multiple", 0.0) for s in completed
                  if s.get("createdAt", "")[:10] >= day_start)
    weekly_r = sum(s.get("r_multiple", 0.0) for s in completed
                   if s.get("createdAt", "")[:10] >= week_start)
    return {
        "daily_pl_pct": round(daily_r * risk_pct, 2),
        "weekly_pl_pct": round(weekly_r * risk_pct, 2),
        "daily_progress_pct": round(daily_r * risk_pct, 2),
        "objective_pct": goals["daily_objective_pct"],
        "objective_progress": (
            f"{daily_r * risk_pct:+.2f}% / {goals['daily_objective_pct']:+.1f}%"),
        "risk_per_trade_pct": risk_pct,
    }


def agent_status(user_id: str, current_task: str = "Monitoring markets",
                 strategies_active: int = 2) -> Dict[str, Any]:
    store = get_store()
    goals = get_goals(user_id)
    progress = compute_progress(user_id)
    today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
    todays = store.list("signals", filters={"userId": user_id, "day": today}, limit=500)
    completed = [s for s in todays if s.get("completed")]
    lessons = store.count("lessons")
    experiments = store.count("experiments")
    pending = store.count("hypotheses", filters={"status": "AWAITING_APPROVAL"})

    return {
        "agent_status": "ACTIVE",
        "current_objective": f"+{goals['daily_objective_pct']}%",
        "goals": goals,
        "progress": progress,
        "current_task": current_task,
        "strategies_active": strategies_active,
        "signals_today": len(todays),
        "wins": sum(1 for s in completed if s.get("outcome") == "WIN"),
        "losses": sum(1 for s in completed if s.get("outcome") == "LOSS"),
        "lessons": lessons,
        "experiments": experiments,
        "pending_approvals": pending,
        "strategy_versions": {
            "strategy_1_zero_lag": active_version("strategy_1_zero_lag"),
            "strategy_2_ema_atr": active_version("strategy_2_ema_atr"),
        },
    }
