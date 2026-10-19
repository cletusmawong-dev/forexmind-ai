"""STRICT MESSAGING POLICY (owner brief 2026-10-10, section 8 - MANDATORY).

Telegram carries ONLY:
  A. a new VALID trade (entry/SL/TP present, checks passed),
  B. meaningful, actionable open-position events (structural change affecting
     the trade, stop adjustment, partial, full exit, material execution
     failure),
  C. safety-critical failures and risk-limit events.
Everything else stays in-app or is not produced at all. No routine market
commentary, no empty status updates, no "waiting for a setup" noise.

The notify() allowlist already blocks everything not marked important; this
module (a) adds the new management-event type, (b) DENYLISTS commentary-style
types so future code cannot force them through, and (c) gates the daily
morning brief to days with actionable content only.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

MANAGEMENT_EVENT = "MANAGEMENT_EVENT"

# commentary-style categories that must never reach Telegram, even if a
# future call marks them important (section 8: mandatory silence)
DENYLIST = {
    "MARKET_SUMMARY", "DAILY_COMMENTARY", "STATUS_UPDATE", "WAITING",
    "MARKET_CONDITIONS", "AI_COMMENTARY", "ANALYSIS_POST", "MOOD",
    "SESSION_NOTE", "PRICE_LEVEL_TOUCH", "LEVEL_TOUCH",
}

_BLOCKED_ONCE: Dict[str, bool] = {}


def telegram_allowed(type_: str, meta: Optional[Dict[str, Any]]) -> bool:
    """Final say on Telegram delivery for a notification type."""
    if type_ in DENYLIST:
        _BLOCKED_ONCE[type_] = True
        return False
    if type_ == MANAGEMENT_EVENT:
        return True                      # actionable position event
    if type_ == "MORNING_BRIEF":
        return True                      # gated upstream (brief_actionable)
    return True                          # existing allowlist decides the rest


def brief_actionable(store, user_id: str) -> bool:
    """The daily brief goes out ONLY when there is actionable content:
    open positions, management events, or signals from the last 24h.
    Silent otherwise (section 8B: no valid trade -> send nothing)."""
    try:
        from ..execution.mt5 import bridge_get, user_mode, MAGIC
        if user_mode(user_id) == "vps":
            res = bridge_get(f"/positions?magic={MAGIC}")
            for p in (res or {}).get("positions") or []:
                return True
    except Exception:
        pass
    try:
        ev = store.list("ai_decisions", filters={"userId": user_id}, limit=50)
        for e in ev or []:
            if str(e.get("action", "")).upper() in ("PROTECT", "PARTIAL_PROFIT", "EXIT"):
                return True
        recent = store.list("signals", filters={"userId": user_id}, limit=20) or []
        import pandas as pd
        cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=24)).isoformat()
        for s in recent:
            if str(s.get("createdAt") or "") >= cutoff:
                return True
    except Exception:
        pass
    return False
