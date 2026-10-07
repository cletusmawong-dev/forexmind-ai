"""Insight API (master upgrade §10, §12, §13): session dashboard, strategy
scorecard, and the WHY-NOT-TRADE explainer.

Every answer is computed from the caller's OWN stored data + real system
state. Nothing is fabricated; small samples are labelled INSUFFICIENT.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..agent import core as agent_core
from ..db.store import get_store
from ..learning.health import strategy_health
from ..learning.versions import LIFECYCLE
from ..market_data.calendar import currencies_for, is_blackout
from ..state import State
from ..strategies.strategy_2_mtf_sweep_bos_retest import state as s2_state
from .deps import get_user_id

router = APIRouter(tags=["insight"])

SESSION_ORDER = ["Asian", "London", "NewYork", "Late"]


def _closed(user_id: str, limit: int = 400) -> list:
    rows = get_store().list("signals", filters={"userId": user_id, "completed": True},
                            limit=limit)
    return rows


# ---------------------------------------------------------------------------
# §10 session dashboard
# ---------------------------------------------------------------------------
@router.get("/analytics/sessions")
def sessions(user_id: str = Depends(get_user_id)):
    """Per-session performance from the caller's closed signals. Sessions with
    fewer than MIN_SAMPLE trades are reported INSUFFICIENT - never judged."""
    MIN_SAMPLE = 5
    buckets: dict = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
    for d in _closed(user_id):
        mc = d.get("market_conditions") or {}
        s = mc.get("session") or "Unknown"
        b = buckets[s]
        b["n"] += 1
        if d.get("outcome") == "WIN":
            b["wins"] += 1
        try:
            b["r"] += float(d.get("r_multiple") or 0.0)
        except Exception:
            pass
    out = []
    for s in SESSION_ORDER + [k for k in buckets if k not in SESSION_ORDER]:
        if s not in buckets:
            continue
        b = buckets[s]
        enough = b["n"] >= MIN_SAMPLE
        out.append({"session": s, "trades": b["n"], "wins": b["wins"],
                    "win_rate": round(100 * b["wins"] / b["n"], 1) if enough else None,
                    "net_r": round(b["r"], 2),
                    "sample": "OK" if enough else "INSUFFICIENT"})
    return {"sessions": out, "min_sample": MIN_SAMPLE}


# ---------------------------------------------------------------------------
# §12 strategy scorecard
# ---------------------------------------------------------------------------
def _lifecycle_to_health_state(lifecycle: str, health: dict) -> tuple:
    """Lifecycle dominates: sleeping strategies are SHADOW_ONLY, retired are
    PAUSED regardless of stats (they cannot trade)."""
    if lifecycle == "SLEEPING":
        return "SHADOW_ONLY", "research-only by lifecycle registry - never executes"
    if lifecycle == "RETIRED":
        return "PAUSED", "retired - hidden from scanning and execution"
    st = health.get("state", "INSUFFICIENT")
    return st, health.get("note", "")


@router.get("/scorecard")
def scorecard(user_id: str = Depends(get_user_id)):
    store = get_store()
    from ..strategies import all_strategies
    out = []
    for sid, s in all_strategies().items():
        lifecycle = LIFECYCLE.get(sid, "LIVE")
        health = strategy_health(user_id, sid)
        state, note = _lifecycle_to_health_state(lifecycle, health)
        # breakdowns from the caller's own closed signals
        by_pair: dict = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
        by_session: dict = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
        recent = sorted([], key=lambda x: 0)
        rows = [d for d in _closed(user_id) if d.get("strategy_id") == sid]
        rows.sort(key=lambda d: d.get("completedAt") or d.get("createdAt") or "")
        n = wins = 0
        net_r = 0.0
        streak = 0
        best = None
        for d in rows:
            n += 1
            win = d.get("outcome") == "WIN"
            wins += int(win)
            try:
                r = float(d.get("r_multiple") or 0.0)
            except Exception:
                r = 0.0
            net_r += r
            mkt = d.get("market") or "?"
            by_pair[mkt]["n"] += 1
            by_pair[mkt]["wins"] += int(win)
            by_pair[mkt]["r"] += r
            mc = d.get("market_conditions") or {}
            ses = mc.get("session") or "?"
            by_session[ses]["n"] += 1
            by_session[ses]["wins"] += int(win)
            by_session[ses]["r"] += r
        out.append({
            "strategy_id": sid, "name": getattr(s, "name", sid),
            "version": getattr(s, "version", getattr(s, "VERSION", "1.0")),
            "lifecycle": lifecycle,
            "health_state": state, "note": note,
            "metrics": health.get("metrics", {}),
            "trades": n, "wins": wins,
            "win_rate": round(100 * wins / n, 1) if n >= 10 else None,
            "net_r": round(net_r, 2),
            "sample": "OK" if n >= 10 else "INSUFFICIENT",
            "by_pair": [{"market": k, **{ "trades": v["n"], "wins": v["wins"],
                                          "net_r": round(v["r"], 2)}}
                        for k, v in sorted(by_pair.items())],
            "by_session": [{"session": k, "trades": v["n"], "wins": v["wins"],
                            "net_r": round(v["r"], 2)}
                           for k, v in sorted(by_session.items())],
        })
    return {"strategies": out,
            "note": "win_rate only shown with >= 10 completed trades (no tiny-sample claims)"}


# ---------------------------------------------------------------------------
# §13 WHY NOT TRADE
# ---------------------------------------------------------------------------
def _daily_cap_state(user_id: str) -> dict:
    risk = agent_core.get_risk(user_id) or {}
    cap = int(risk.get("max_signals_per_day") or 6)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = get_store().list("signals", filters={"userId": user_id}, limit=300)
    used = sum(1 for d in rows
               if d.get("day") == today and d.get("execution_status") == "SUBMITTED")
    return {"gate": "daily cap", "ok": used < cap,
            "detail": f"{used}/{cap} executed today"}


def _session_state(user_id: str) -> dict:
    risk = agent_core.get_risk(user_id) or {}
    allowed = set(risk.get("sessions") or [])
    hour = datetime.now(timezone.utc).hour
    current = ("Asian" if 0 <= hour < 8 else
               "London" if 8 <= hour < 13 else
               "NewYork" if 13 <= hour < 21 else "Late")
    ok = current in allowed
    return {"gate": "session", "ok": ok,
            "detail": f"{current} session; enabled: {', '.join(sorted(allowed)) or 'none'}"}


def _blackout_state(market: str) -> dict:
    try:
        blocked, ev = is_blackout(market)
    except Exception:
        blocked, ev = False, None
    if blocked and ev:
        return {"gate": "news blackout", "ok": False,
                "detail": f"{ev.get('country')} {ev.get('title')} - high-impact window"}
    cur = ", ".join(currencies_for(market))
    return {"gate": "news blackout", "ok": True, "detail": f"clear ({cur})"}


def _lifecycle_state(strategy_id: str) -> dict:
    from ..learning.versions import lifecycle_of
    lc = lifecycle_of(strategy_id)
    ok = lc == "LIVE"
    return {"gate": "strategy lifecycle", "ok": ok,
            "detail": f"{strategy_id} is {lc}"}


@router.get("/whynot")
def whynot(symbol: str, strategy_id: str = Query("strategy_2_mtf_sweep_bos_retest"),
           user_id: str = Depends(get_user_id)):
    """Why is there no trade right now on this symbol? Composed ONLY from the
    real machine state and the caller's real permission gates."""
    sym = symbol.upper()
    checks: list = []

    # 1) the setup itself (persisted S2 machine)
    st = s2_state.load(sym, "15M")
    waiting = bool(st.get("waiting_bull_retest") or st.get("waiting_bear_retest"))
    swept = st.get("swept_level") is not None
    if waiting:
        checks.append({"gate": "setup", "ok": False,
                       "detail": "retest confirmed - signal conditions evaluated on close"})
    elif swept and (st.get("broken_high") or st.get("broken_low")):
        checks.append({"gate": "setup", "ok": False,
                       "detail": "structure break confirmed, but retest has not occurred"})
    elif swept:
        checks.append({"gate": "setup", "ok": False,
                       "detail": "liquidity sweep detected, but structure confirmation is missing"})
    else:
        checks.append({"gate": "setup", "ok": False,
                       "detail": "no valid liquidity sweep yet - engine keeps watching"})

    # 2-5) the caller's real gates
    checks.append(_daily_cap_state(user_id))
    checks.append(_session_state(user_id))
    checks.append(_blackout_state(sym))
    checks.append(_lifecycle_state(strategy_id))

    # 6) account permission (multi-account §18-19)
    try:
        accts = [a for a in get_store().list("trading_accounts",
                                             filters={"userId": user_id}, limit=50)]
        active = next((a for a in accts if a.get("is_active")), None)
        if active is None:
            checks.append({"gate": "account", "ok": True, "detail": "no trading account selected"})
        else:
            ok = bool(active.get("trading_enabled"))
            checks.append({"gate": "account", "ok": ok,
                           "detail": f"{active.get('label')}: trading "
                                     f"{'ENABLED' if ok else 'OFF (signals-only)'}"})
    except Exception:
        pass

    blocking = [c for c in checks if not c["ok"]]
    reason = blocking[0]["detail"] if blocking else "all gates open - signal would execute on a valid setup"
    return {"symbol": sym, "strategy_id": strategy_id,
            "can_trade": not blocking, "reason": reason, "checks": checks,
            "as_of": datetime.now(timezone.utc).isoformat()}
