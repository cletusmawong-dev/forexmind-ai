"""Market News & Events (master upgrade §7) - REAL releases only.

Source: the existing ForexFactory weekly calendar (calendar.py - the same
feed that powers news blackouts). The AI may EXPLAIN an event from its real
fields; it may never invent events or numbers it was not given.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import settings
from ..market_data import calendar as _cal
from ..market_data.calendar import currencies_for
from .deps import get_user_id

router = APIRouter(tags=["news"])

# app markets whose currencies each event touches - derived, never guessed
ALL_MARKETS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
               "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD",
               "GBPJPY", "GBPCHF", "AUDJPY", "CHFJPY", "CADJPY", "AUDCAD",
               "NZDJPY", "XAGUSD", "BTCUSD", "ETHUSD"]


def _affected_markets(ev: dict) -> list:
    cur = ev.get("country") or ev.get("currency") or ""
    cur = str(cur).strip().upper()
    out = []
    for m in ALL_MARKETS:
        if cur and cur in currencies_for(m):
            out.append(m)
    return out


@router.get("/news/events")
def events(user_id: str = Depends(get_user_id)):
    """This week's high/medium-impact releases from the live calendar feed."""
    try:
        evs = _cal._fetch()
    except Exception:
        evs = []
    out = []
    for ev in evs:
        out.append({"title": ev.get("title"), "country": ev.get("country"),
                    "date": ev.get("date"), "impact": ev.get("impact"),
                    "forecast": ev.get("forecast"), "previous": ev.get("previous"),
                    "affected_markets": _affected_markets(ev)})
    return {"events": out, "count": len(out),
            "source": "ForexFactory weekly calendar (live feed, cached 1h)"}


class ExplainIn(BaseModel):
    title: str
    country: str
    date: str
    impact: str = ""
    forecast: Optional[str] = None
    previous: Optional[str] = None


@router.post("/news/explain")
def explain(body: ExplainIn, user_id: str = Depends(get_user_id)):
    """Explain ONE real event: what it is, which instruments may move, why
    volatility may rise, what to watch out for. Grounded ONLY on the event
    fields + market mapping - no fabricated numbers, no trade commands."""
    ev = body.model_dump()
    affected = _affected_markets(ev)
    context = {
        "event": ev,
        "affected_app_markets": affected,
        "note": ("FOREXMIND skips automatic entries +/-30 minutes around "
                 "high-impact releases (news blackout)."),
    }
    prompt = (f"Explain this economic event for a retail trader: what it is, "
              f"which instruments may be affected and why, why volatility can "
              f"increase, and key risk considerations. Use ONLY the provided "
              f"context - do not invent numbers. Event: {ev['title']} "
              f"({ev['country']}, {ev['date']}).")
    answer = None
    provider = "unavailable"
    try:
        from ..agent.ai_provider import get_ai_provider
        p = get_ai_provider()
        answer = p.complete(prompt, context, timeout=30, max_tokens=420) \
            if hasattr(p, "complete") and p.name == "xkiro" else None
        provider = getattr(p, "name", "unknown")
    except Exception:
        answer = None
    if not answer:
        provider = "grounded_facts"
        f = []
        if ev.get("previous"):
            f.append(f"previous release: {ev['previous']}")
        if ev.get("forecast"):
            f.append(f"forecast: {ev['forecast']}")
        answer = (f"{ev['title']} ({ev['country']}) is scheduled for {ev['date']}. "
                  + (f"{'; '.join(f)}. " if f else "")
                  + f"Markets that reference {ev['country']} may see higher "
                  f"volatility around the release: {', '.join(affected) or 'n/a'}. "
                  + "FOREXMIND keeps automatic entries paused +/-30 minutes "
                    "around high-impact releases and resumes normal scanning "
                    "after the window.")
    return {"explanation": answer, "provider": provider,
            "affected_markets": affected}
