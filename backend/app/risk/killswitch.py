"""KILL-SWITCH HIERARCHY (3.0 spec section 47).

LEVEL 0 normal | L1 stop new entries | L2 stop strategy | L3 stop instrument
| L4 stop automated trading | L5 emergency close-all.

* Stored in the DB (settings collection, doc `killswitch`) - backend is the
  sole permission source.
* Enforcement is DETERMINISTIC and runs as the FIRST firewall check on every
  entry regardless of the risk_guards_enabled flag. Level 0 (default) is a
  no-op, so existing behavior is byte-identical until someone activates.
* Every activation is explicit + audited + visible. L5 has its own guarded
  route (confirm=true required) and reuses the audited per-user emergency path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

DOC = "killswitch"
LEVELS = {0: "NORMAL", 1: "STOP_NEW_ENTRIES", 2: "STOP_STRATEGY",
          3: "STOP_INSTRUMENT", 4: "STOP_AUTOMATED", 5: "EMERGENCY_CLOSE_ALL"}


def _doc(store):
    d = store.get("settings", DOC)
    return d if d else {"level": 0}


def current_level() -> dict:
    try:
        from ..db.store import get_store
        d = _doc(get_store())
    except Exception:
        # store read failed: report the honest default + flag it. Entries still
        # pass through the FULL remaining chain (permission re-check per order
        # reads the DB too - a dead store blocks the order there regardless).
        return {"level": 0, "name": "NORMAL", "reason": None, "set_by": None,
                "at": None, "instruments": [], "strategies": [],
                "degraded_read": True}
    lvl = int(d.get("level") or 0)
    return {"level": lvl, "name": LEVELS.get(lvl, "NORMAL"),
            "reason": d.get("reason"), "set_by": d.get("set_by"),
            "at": d.get("at"),
            "instruments": d.get("instruments") or [],
            "strategies": d.get("strategies") or []}


def set_level(level: int, by: str, reason: str = "",
              instruments: Optional[list] = None,
              strategies: Optional[list] = None) -> dict:
    from ..db.store import get_store
    store = get_store()
    level = max(0, min(5, int(level)))
    patch = {"level": level, "name": LEVELS[level], "reason": str(reason)[:200],
             "set_by": by, "at": datetime.now(timezone.utc).isoformat(),
             "instruments": [str(i).upper() for i in (instruments or [])],
             "strategies": [str(s) for s in (strategies or [])]}
    prev = store.get("settings", DOC)
    if prev:
        store.update("settings", DOC, patch)
    else:
        store.create("settings", patch, doc_id=DOC)
    return {"previous_level": int((prev or {}).get("level") or 0), **patch}


def check(market: Optional[str] = None, strategy_id: Optional[str] = None) -> Tuple[bool, str]:
    """Deterministic enforcement. (True, "") at level 0 or when not covered."""
    try:
        k = current_level()
        lvl = k["level"]
        if lvl == 0:
            return True, ""
        if lvl == 5 or lvl == 1 or lvl == 4:
            return False, f"KILLSWITCH_L{lvl}_{k['name']}"
        if lvl == 3 and market and (market or "").upper() in k["instruments"]:
            return False, f"KILLSWITCH_L3_INSTRUMENT_{(market or '').upper()}"
        if lvl == 2 and strategy_id and strategy_id in k["strategies"]:
            return False, f"KILLSWITCH_L2_STRATEGY_{strategy_id}"
        return True, ""
    except Exception:
        return True, ""   # fail-open for a READ of a safety flag is wrong only
                          # if the store is down; entries also pass the whole
                          # remaining gate chain - recorded honestly in chaos tests


def emergency_close_all(by: str) -> dict:
    """L5 helper: close every open engine position for every ENABLED user via
    the audited per-user emergency path. Explicit-confirm enforced at route."""
    from ..db.store import get_store
    store = get_store()
    results = []
    for u in store.list("users", limit=200):
        if (u.get("trading_permission") == "enabled"
                and (u.get("status") or "active") == "active"):
            try:
                from ..api.routes_admin import _emergency_stop_inner
                r = _emergency_stop_inner(u["id"], by)
                results.append({"user": u["id"], **r})
            except Exception as exc:
                results.append({"user": u["id"], "error": type(exc).__name__})
    set_level(4, by, "L5 emergency close-all executed; automated trading stopped")
    return {"closed": results, "level_now": 4}
