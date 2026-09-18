"""Daily account-level controls (master prompt SS21-SS28, SS32).

Account-level DAILY PROFIT TARGET and DAILY LOSS LIMIT in USD, computed from
real recorded results - never fabricated:

  realized  - signals completed since the trading-day start. Dollars come
              from broker-confirmed `mt5_pl` when present, otherwise from the
              tracked R multiple x risk% x balance (clearly labeled basis).
  floating  - open-position P/L from the MT5 bridge (vps mode) or the PC
              connector's last equity-balance snapshot (manual mode). If
              neither is available floating is 0 and the basis says so.

No daily counters are stored - state is recomputed from records, so VPS /
backend restarts cannot corrupt it (SS5, SS28: nothing to reset except the
day key itself).

The trading-day boundary uses the user's session_tz (SS28: never blindly the
server timezone). Signal caps keep their existing UTC day key - untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from ..config import settings
from ..db.store import get_store

TARGET_OFF = 0.0


# ---------------------------------------------------------------------------
def trading_day_start(tz_name: Optional[str], now: Optional[datetime] = None) -> float:
    """Epoch seconds of local midnight for the user's session timezone."""
    tz = (tz_name or "UTC").strip() or "UTC"
    now = now or datetime.now(timezone.utc)
    ts = pd.Timestamp(now)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    try:
        from zoneinfo import ZoneInfo
        local = ts.tz_convert(ZoneInfo(tz))
    except Exception:
        local = ts
    midnight = local.normalize()
    return midnight.tz_convert("UTC").timestamp()


def _signal_dollars(sig: dict, balance: float, risk_pct: float) -> Tuple[float, bool]:
    """(dollars, broker_confirmed) for one completed signal."""
    pl = sig.get("mt5_pl")
    if sig.get("mt5_confirmed") and pl is not None:
        return float(pl), True
    r = float(sig.get("r_multiple") or 0.0)
    base = balance if balance > 0 else 0.0
    return round(r * (risk_pct / 100.0) * base, 2), False


def daily_state(user_id: str, risk: Optional[dict] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Full account-level daily picture (realized + floating + walls)."""
    store = get_store()
    if risk is None:
        docs = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
        risk = docs[0] if docs else {}
    goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    goals = goals[0] if goals else {}

    tz_name = str(risk.get("session_tz") or "UTC")
    day_start = trading_day_start(tz_name, now)
    day_str = datetime.fromtimestamp(day_start, tz=timezone.utc).strftime("%Y-%m-%d")

    acct = goals.get("mt5_account") or {}
    balance = float(goals.get("account_balance") or acct.get("balance") or 0.0)
    risk_pct = float(risk.get("risk_per_trade_pct", 1.0) or 1.0)

    realized = 0.0
    confirmed = 0
    estimated = 0
    seen = set()
    for coll_filter in ({"userId": user_id, "completed": True},):
        for sig in store.list("signals", filters=coll_filter, limit=1000):
            if sig.get("id") in seen:
                continue
            ended = sig.get("mt5_closed_at") or sig.get("completed_at") or sig.get("createdAt")
            try:
                ended_ts = pd.Timestamp(ended)
                if ended_ts.tzinfo is None:
                    ended_ts = ended_ts.tz_localize("UTC")
                if ended_ts.timestamp() < day_start:
                    continue
            except Exception:
                continue
            seen.add(sig.get("id"))
            usd, broker = _signal_dollars(sig, balance, risk_pct)
            realized += usd
            confirmed += 1 if broker else 0
            estimated += 0 if broker else 1

    floating = 0.0
    floating_source = "unavailable"
    if (goals.get("execution_mode") or user_mode_safe(user_id)) == "vps":
        try:  # lazy import - avoids an execution <-> daily import cycle
            from ..execution.mt5 import bridge_get
            pos = bridge_get("/positions", timeout=6) or {}
            profits = [float(p.get("profit") or 0) for p in (pos.get("positions") or [])
                       if int(p.get("magic") or 0) == settings_magic()]
            floating = round(sum(profits), 2)
            floating_source = "bridge_positions" if pos else "bridge_unreachable"
        except Exception:
            floating_source = "bridge_error"
    elif acct.get("equity") is not None and acct.get("balance") is not None:
        floating = round(float(acct["equity"]) - float(acct["balance"]), 2)
        floating_source = "connector_equity"

    realized = round(realized, 2)
    state = {
        "userId": user_id,
        "day": day_str,
        "tz": tz_name,
        "realized_usd": realized,
        "floating_usd": floating,
        "total_usd": round(realized + floating, 2),
        "realized_basis": (f"{confirmed} broker-confirmed, {estimated} R-estimated"
                           if estimated else f"{confirmed} broker-confirmed"),
        "floating_source": floating_source,
        "balance_usd": balance,
        "open_positions": len(_open_positions_safe(user_id)),
    }
    state.update(wall_view(state, risk))
    return state


def settings_magic() -> int:
    from ..execution.mt5 import MAGIC
    return MAGIC


def user_mode_safe(user_id: str) -> str:
    try:
        from ..execution.mt5 import user_mode
        return user_mode(user_id)
    except Exception:
        return "off"


def _open_positions_safe(user_id: str) -> list:
    if user_mode_safe(user_id) != "vps":
        return []
    try:
        from ..execution.mt5 import bridge_get
        return (bridge_get("/positions", timeout=6) or {}).get("positions") or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
def wall_view(state: Dict[str, Any], risk: dict) -> Dict[str, Any]:
    """Configured walls + which are hit (SS21-SS26). 0 = wall off."""
    target = abs(float(risk.get("daily_profit_target_usd") or 0.0))
    loss_limit = abs(float(risk.get("daily_loss_limit_usd") or 0.0))
    total = float(state.get("total_usd") or 0.0)
    hit_target = bool(target > 0 and total >= target)
    hit_loss = bool(loss_limit > 0 and total <= -loss_limit)
    status = "NORMAL"
    if hit_loss:
        status = "LOSS_LIMIT_HIT"
    if hit_target:
        status = "TARGET_HIT" if not hit_loss else "TARGET_AND_LIMIT_HIT"
    return {
        "daily_profit_target_usd": target,
        "daily_loss_limit_usd": loss_limit,
        "remaining_target_usd": round(max(0.0, target - total), 2) if target else None,
        "remaining_loss_usd": round(max(0.0, total + loss_limit), 2) if loss_limit else None,
        "hit_target": hit_target,
        "hit_loss": hit_loss,
        "status": status,
        "on_loss_limit": on_loss_limit_mode(risk),
    }


def on_loss_limit_mode(risk: dict) -> str:
    mode = str(risk.get("on_loss_limit") or "stop_entries").strip().lower()
    return mode if mode in ("stop_entries", "stop_and_close") else "stop_entries"


def wall_reason(state: Dict[str, Any], risk: Optional[dict] = None) -> Optional[str]:
    """The reason automatic entries are blocked, or None (SS22/SS26).
    risk=None falls back to the wall config embedded in the state dict
    (daily_state always merges wall_view into its result)."""
    if risk is None:
        risk = {k: state.get(k) for k in
                ("daily_profit_target_usd", "daily_loss_limit_usd", "on_loss_limit")}
    v = wall_view(state, risk)
    if v["hit_loss"]:
        return "daily loss limit reached"
    if v["hit_target"]:
        return "daily profit target reached"
    return None


def is_entry_blocked(user_id: str, risk: Optional[dict] = None) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Gate for the EXECUTOR (SS22: stop automatic NEW entries only).

    Signal GENERATION is never blocked by this module - signals keep flowing
    and are labeled EXTRA (see signal_engine.EXTRA_WALL_REASONS)."""
    st = daily_state(user_id, risk=risk)
    reason = wall_reason(st, risk)
    return (reason is not None), reason, st
