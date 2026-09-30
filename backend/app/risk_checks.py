"""Deterministic pre-trade risk guards (Master Upgrade Phase 10 / Stage 4).

Extends the existing wall/cap guards with four honest, database/env-driven
checks applied to every automatic ENTRY before the order is sent:

    NEWS blackout     high-impact event inside the blackout window
    SPREAD            live broker spread above the cap (vps mode only)
    VOL spike         current volatility rank at/above the extreme threshold
    CORRELATION       too many open engine positions sharing currency roots

Every refusal is LOUD: SKIPPED_* status on the signal, lifecycle-ledger
event, agent log and a Telegram notice. Config comes from env (config.py)
so behavior is reproducible and auditable. These guards never touch
management of already-open positions.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _pip_size(market: str) -> float:
    from .execution.mt5 import PIP_SIZE
    return PIP_SIZE.get(market, 0.0001)


# ---------------------------------------------------------------------------
def _news_ok(market: str) -> Tuple[bool, str]:
    if not settings_value("risk_news_guard"):
        return True, ""
    try:
        from .market_data.calendar import is_blackout
        blackout, ev = is_blackout(market)
        if blackout:
            return False, f"high-impact news window ({(ev or {}).get('title') or 'event'})"
    except Exception:
        pass        # calendar unavailable -> check passes, never fabricates
    return True, ""


def _spread_ok(market: str) -> Tuple[bool, str]:
    try:
        from .execution.mt5 import bridge_get, user_mode
        if user_mode(settings_value("owner_user_id")) != "vps":
            return True, ""
        data = bridge_get("/rates", timeout=4) or {}
        row = ((data.get("rates") or data.get("bars") or {}).get(market)
               or (data.get(market) if isinstance(data.get(market), dict) else None))
        if not row:
            return True, ""                       # no live quote -> not invented
        spread = row.get("spread")
        if spread is None and row.get("ask") and row.get("bid"):
            spread = float(row["ask"]) - float(row["bid"])
        if spread is None:
            return True, ""
        pip = _pip_size(market)
        spread_pips = float(spread) / pip if pip else 0.0
        cap = float(settings_value("risk_max_spread_pips"))
        if spread_pips > cap:
            return False, f"spread {spread_pips:.1f} pips > cap {cap:g}"
    except Exception:
        pass
    return True, ""


def _vol_ok(market: str) -> Tuple[bool, str]:
    try:
        import time as _t
        from datetime import datetime as _dt
        from .learning.regime import current
        cur = current(market)
        rank = cur.get("volatility_rank")
        # freshness window: a stale cached classification is NOT current
        # evidence - the guard only acts on data computed recently.
        cat = cur.get("computed_at")
        if rank is not None and cat:
            try:
                age = _t.time() - _dt.fromisoformat(
                    str(cat).replace("Z", "+00:00")).timestamp()
                if age > 1800:
                    return True, ""
            except Exception:
                pass
        if rank is not None and float(rank) >= float(
                settings_value("risk_vol_rank_max")):
            return False, (f"volatility rank {float(rank):.2f} at/above "
                           f"extreme threshold "
                           f"{float(settings_value('risk_vol_rank_max')):.2f}")
    except Exception:
        pass
    return True, ""


def _norm_symbol(market: str) -> str:
    """Broker-suffix normalization: USDJPYm -> USDJPY, XAUUSDm -> XAUUSD.
    Without this, suffixed and unsuffixed symbols never correlate."""
    m = str(market or "").upper().strip()
    while m and (m[-1] in "MC." or m[-1].isdigit()):
        m = m[:-1]
    return m


def _roots(market: str) -> set:
    m = _norm_symbol(market)
    if len(m) == 6 and m.isalpha():
        return {m[:3], m[3:]}
    return {m}          # indices/metals keep their own root


def _open_engine_positions(user_id: str) -> list:
    """Broker-truth open engine positions. RAISES on failure - trading guards
    treat that as fail-closed; reporting callers catch their own."""
    from .execution.mt5 import MAGIC, bridge_get, user_mode
    if user_mode(user_id) != "vps":
        return []                    # nothing engine-owned on the broker
    pos = (bridge_get("/positions", timeout=6) or {}).get("positions") or []
    return [p for p in pos if int(p.get("magic") or 0) == MAGIC]


def _correlation_ok(user_id: str, market: str) -> Tuple[bool, str]:
    try:
        mine = _open_engine_positions(user_id)
        roots = _roots(market)
        related = 0
        for p in mine:
            if roots & _roots(p.get("symbol") or p.get("app_market") or ""):
                related += 1
        cap = int(settings_value("risk_max_correlated"))
        if related >= cap:
            return False, (f"{related} open position(s) share currency roots "
                           f"with {market} (cap {cap})")
    except Exception as exc:
        # FAIL-CLOSED (spec: a failed critical gate blocks the order). The old
        # fail-open behavior let stacked duplicates through whenever the DB or
        # bridge read errored - the 2026-09-29 3x-USDJPY incident.
        return False, (f"position check unavailable ({type(exc).__name__}) "
                       f"- entry blocked (fail-closed)")
    return True, ""


def _stacking_ok(user_id: str, market: str,
                 direction: Optional[str]) -> Tuple[bool, str]:
    """Hard rule (2026-09-29 incident): never open a SECOND position on the
    same instrument in the same direction while one is already open. Same
    instrument opposite direction (hedge) is left to the correlation cap."""
    try:
        mine = _open_engine_positions(user_id)
        m0 = _norm_symbol(market)
        d = (direction or "").upper()
        for p in mine:
            if _norm_symbol(p.get("symbol") or "") != m0:
                continue
            pdir = str(p.get("type") or "").upper()
            if pdir in ("BUY", "SELL") and (not d or pdir == d):
                return False, (f"already holding {m0} {pdir} "
                               f"(ticket {p.get('ticket')}) - no stacking")
    except Exception as exc:
        return False, (f"position check unavailable ({type(exc).__name__}) "
                       f"- entry blocked (fail-closed)")
    return True, ""


# ---------------------------------------------------------------------------
def settings_value(name: str):
    """Env-driven defaults via config (import lazily to avoid cycles)."""
    from .config import settings
    return getattr(settings, name)


def check_entry(user_id: str, market: str,
                strategy_id: Optional[str] = None,
                direction: Optional[str] = None) -> Tuple[bool, str, str]:
    """Run all guards. Returns (ok, guard_name, reason). First failure wins.

    The kill-switch hierarchy (3.0 spec section 47) is evaluated FIRST and
    regardless of risk_guards_enabled. Level 0 (default) is a no-op.
    CORRELATION and STACKING are FAIL-CLOSED: if the broker-position check
    cannot be performed, the entry is blocked, never waved through."""
    try:
        from .risk.killswitch import check as ks_check
        ok, why = ks_check(market, strategy_id)
        if not ok:
            return False, "KILLSWITCH", why
    except Exception:
        pass
    if not settings_value("risk_guards_enabled"):
        return True, "", ""
    for name, fn in (("NEWS", lambda uid, mkt: _news_ok(mkt)),
                     ("SPREAD", lambda uid, mkt: _spread_ok(mkt)),
                     ("VOL", lambda uid, mkt: _vol_ok(mkt)),
                     ("CORRELATION", _correlation_ok),
                     ("STACKING", lambda uid, mkt: _stacking_ok(uid, mkt, direction))):
        ok, why = fn(user_id, market)
        if not ok:
            return False, name, why
    return True, "", ""
