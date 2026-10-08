"""Home overview API - ONE aggregate endpoint so the Home screen answers the
five owner questions with a single cached call instead of many pollers:

  1. account    - broker truth (balance, equity, open P/L), today's P/L,
                  drawdown usage, trading status (walls / auto-execution)
  2. market     - live forex sessions (UTC), agent scan state
  3. findings   - per-symbol what FOREXMIND finds: 9/21 EMA last signal + the
                  Supply & Demand + FVG machine state (persisted docs only -
                  never manufactured, never a live re-scan)
  4. ai         - compact insight hook (the AI manager's real latest note)

Read-only. Everything is derived from the same stores the strategies write;
missing data is reported honestly (null / "NO DATA"), never faked.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from .deps import get_user_id
from ..state import State

router = APIRouter(tags=["home"])

_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_CACHE_TTL = 5.0          # seconds - protects DB + bridge from Home polling

# UTC hour ranges (approx, standard time) - honest labels, no fake precision
_SESSIONS = [
    ("Sydney", 21, 6),
    ("Tokyo", 0, 9),
    ("London", 7, 16),
    ("New York", 12, 21),
]

_S1_ID = "strategy_2_ema_atr"
_S2_ID = "strategy_2_supply_demand_fvg"

_S2_WORDS = {
    "NO_SETUP": "NO SETUP",
    "DISPLACEMENT_CONFIRMED": "DISPLACEMENT",
    "WAITING_RETEST": "WATCHING",
    "FVG_RETEST": "RETESTING",
    "RESET": "NO SETUP",
    "SIGNAL": "SIGNAL READY",
}


def _sessions_now() -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60.0
    out = []
    for name, start, end in _SESSIONS:
        active = (h >= start or h < end) if start > end else (start <= h < end)
        # hours until this session opens
        opens_in = (start - h) % 24
        out.append({"name": name, "active": active,
                    "opens_in_hours": round(opens_in, 1) if not active else 0.0})
    return out


def _findings() -> Dict[str, Any]:
    """Per-symbol strategy overview from PERSISTED state only."""
    store = _store()
    # S2 machine docs: doc_id "<market>|<tf>" - keep the entry TF (15M)
    s2: Dict[str, Dict[str, Any]] = {}
    try:
        for doc in store.list("strategy2_sd_fvg_state", limit=1000):
            raw = str(doc.get("id") or "")
            if "|" not in raw:
                continue
            market, tf = raw.split("|", 1)
            if tf != "15M":                       # overview shows the entry TF
                continue
            state = str(doc.get("state") or "NO_SETUP")
            updatedAt = doc.get("updatedAt")
            s2[market.upper()] = {
                "state": state,
                "word": _S2_WORDS.get(state, "NO SETUP"),
                "direction": ("BUY" if doc.get("direction") == "bull"
                              else "SELL" if doc.get("direction") == "bear" else None),
                "zone_type": (doc.get("zone") or {}).get("type"),
                "updatedAt": updatedAt,
            }
    except Exception:
        pass
    # S1 (9/21 EMA) most recent signal per market - real records only
    s1: Dict[str, Dict[str, Any]] = {}
    try:
        rows = store.list("signals", filters={"strategy_id": _S1_ID}, limit=300)
        rows.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
        for d in rows:
            mkt = str(d.get("market") or "").upper()
            if not mkt or mkt in s1:
                continue
            s1[mkt] = {
                "direction": d.get("direction"),
                "outcome": d.get("outcome"),
                "status": d.get("status"),
                "createdAt": d.get("createdAt"),
                "active": (d.get("status") == "ACTIVE" and not d.get("completed")),
            }
    except Exception:
        pass
    return {"s2": s2, "s1": s1}


def _store():
    from ..db.store import get_store
    return get_store()


def _account_and_status(user_id: str) -> Dict[str, Any]:
    """Broker-truth account + engine walls + execution mode (one snapshot)."""
    from ..engine.daily import daily_state
    from ..execution.mt5 import status as exec_status
    ex = exec_status(user_id)
    acct = ex.get("account") or {}
    balance = float(acct["balance"]) if acct.get("balance") is not None else None
    equity = float(acct["equity"]) if acct.get("equity") is not None else None
    open_pl = (equity - balance) if (equity is not None and balance is not None) else None
    d = daily_state(user_id)
    dd_pct = None
    if equity is not None and balance and balance > 0:
        dd_pct = round(max(0.0, (balance - equity) / balance * 100.0), 2)
    wall = d.get("status") or "NORMAL"
    if wall == "TARGET_HIT" or d.get("hit_target"):
        status_word = "TARGET HIT - ENTRIES PAUSED"
    elif wall == "LOSS_LIMIT_HIT" or d.get("hit_loss"):
        status_word = "LOSS LIMIT HIT - ENTRIES PAUSED"
    elif ex.get("mode") == "off":
        status_word = "ADVISORY ONLY"
    else:
        status_word = "ACTIVE - SCANNING"
    # goal/agent context (the non-bridge fallback + the goal bar on Home)
    from ..agent.core import compute_progress
    prog = compute_progress(user_id)
    return {
        "balance": balance,
        "equity": equity,
        "currency": acct.get("currency") or "USD",
        "open_pl": round(open_pl, 2) if open_pl is not None else None,
        "floating_usd": d.get("floating_usd"),
        "drawdown_pct": dd_pct,
        "today_pl_usd": d.get("total_usd"),
        "daily_target_usd": d.get("daily_profit_target_usd"),
        "daily_limit_usd": d.get("daily_loss_limit_usd"),
        "remaining_target_usd": d.get("remaining_target_usd"),
        "hit_target": d.get("hit_target"),
        "hit_loss": d.get("hit_loss"),
        "open_positions": d.get("open_positions"),
        "trading_status": status_word,
        "execution_mode": ex.get("mode"),
        "execution_enabled": ex.get("enabled"),
        "bridge_online": (ex.get("bridge") or {}).get("online"),
        "server": acct.get("server"),
        "realized_basis": d.get("realized_basis"),
        "floating_source": d.get("floating_source"),
        # objective context (documented R-based assumption when no bridge)
        "goal_balance": _goal_balance(user_id),
        "daily_pl_pct": prog.get("daily_pl_pct"),
        "objective_pct": prog.get("objective_pct"),
        "risk_per_trade_pct": prog.get("risk_per_trade_pct"),
        "trades_today": ex.get("trades_today"),
        "max_per_day": ex.get("max_per_day"),
    }


def _goal_balance(user_id: str) -> Optional[float]:
    """Stored objective balance (NOT broker truth; used only as fallback)."""
    try:
        from ..agent.core import get_goals
        g = get_goals(user_id)
        v = g.get("account_balance")
        return float(v) if v is not None else None
    except Exception:
        return None


def _ai_note() -> Optional[Dict[str, Any]]:
    """The AI manager's latest REAL note (ai_decisions), never generated here."""
    try:
        rows = _store().list("ai_decisions", limit=25)
        rows.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
        for r in rows:
            brain = r.get("brain") or {}
            dec = r.get("decision") or {}
            msg = str(brain.get("answer") or dec.get("continuation_assessment") or "").strip()
            if msg:
                return {"message": msg[:280], "at": r.get("createdAt"),
                        "symbol": r.get("symbol"),
                        "action": dec.get("action"),
                        "confidence": brain.get("confidence_pct")}
        return None
    except Exception:
        return None


@router.get("/home/overview")
def home_overview(user_id: str = Depends(get_user_id)):
    now = time.time()
    if _CACHE["payload"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        payload = _CACHE["payload"]
    else:
        try:
            payload = {                       # GLOBAL parts only (shared cache)
                "sessions": _sessions_now(),
                "findings": _findings(),
                "ai": _ai_note(),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            _CACHE["payload"] = payload
            _CACHE["ts"] = now
        except Exception as exc:            # never fake - report the failure
            return {"ok": False, "error": f"overview unavailable: {exc}",
                    "sessions": _sessions_now(), "findings": {"s1": {}, "s2": {}},
                    "ts": datetime.now(timezone.utc).isoformat()}
    # account/engine parts are per-user - recompute those cheap parts per caller
    out = dict(payload)
    try:
        out["account"] = _account_and_status(user_id)
    except Exception as exc:
        out["account"] = {"error": str(exc)}
    out["ok"] = True
    return out
