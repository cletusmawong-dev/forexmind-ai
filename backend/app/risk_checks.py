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


def _roots(market: str) -> set:
    m = (market or "").upper()
    if len(m) == 6 and m.isalpha():
        return {m[:3], m[3:]}
    return {m}          # indices/metals keep their own root


def _correlation_ok(user_id: str, market: str) -> Tuple[bool, str]:
    try:
        from .execution.mt5 import MAGIC, bridge_get, user_mode
        if user_mode(user_id) != "vps":
            return True, ""
        pos = (bridge_get("/positions", timeout=6) or {}).get("positions") or []
        mine = [p for p in pos if int(p.get("magic") or 0) == MAGIC]
        roots = _roots(market)
        related = 0
        for p in mine:
            if roots & _roots(p.get("symbol") or p.get("app_market") or ""):
                related += 1
        cap = int(settings_value("risk_max_correlated"))
        if related >= cap:
            return False, (f"{related} open position(s) share currency roots "
                           f"with {market} (cap {cap})")
    except Exception:
        pass
    return True, ""


# ---------------------------------------------------------------------------
def settings_value(name: str):
    """Env-driven defaults via config (import lazily to avoid cycles)."""
    from .config import settings
    return getattr(settings, name)


def check_entry(user_id: str, market: str) -> Tuple[bool, str, str]:
    """Run all guards. Returns (ok, guard_name, reason). First failure wins."""
    if not settings_value("risk_guards_enabled"):
        return True, "", ""
    for name, fn in (("NEWS", _news_ok), ("SPREAD", _spread_ok),
                     ("VOL", _vol_ok), ("CORRELATION", _correlation_ok)):
        if name == "CORRELATION":
            ok, why = fn(user_id, market)
        else:
            ok, why = fn(market)
        if not ok:
            return False, name, why
    return True, "", ""
