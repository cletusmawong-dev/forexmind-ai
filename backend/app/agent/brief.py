"""AI Morning Brief (SPEC §15 spirit: the agent works FOR the trader).

Once a day at ~07:05 GMT (pre-London) the agent composes a short, grounded
briefing from real data and pushes it to Telegram + the Home screen:

  * yesterday's completed signals (W/L/R)
  * open signals being tracked
  * objective progress
  * today's high-impact news
  * current prices

XKiro writes it when configured; otherwise an honest template is rendered.
Never fabricates: numbers come from the store, nothing else.
"""
from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..config import INITIAL_MARKETS
from ..db.store import get_store
from ..notifications.service import notify
from .ai_provider import get_ai_provider
from . import core as agent_core
from ..market_data.calendar import high_impact, label as cal_label
from ..state import State


_cache: Dict[str, str] = {}
_lock = threading.Lock()


def _counters(user_id: str) -> dict:
    store = get_store()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    sigs = store.list("signals", filters={"userId": user_id, "completed": True}, limit=300)
    y_w = y_l = y_r = 0
    for s in sigs:
        t = s.get("completed_at") or s.get("updatedAt") or ""
        if str(t) >= cutoff:
            if s.get("outcome") == "WIN":
                y_w += 1
            elif s.get("outcome") == "LOSS":
                y_l += 1
            r = s.get("r_multiple") or 0
            try:
                y_r += float(r)
            except Exception:
                pass
    open_sigs = [s for s in store.list("signals", filters={"userId": user_id}, limit=2000)
                 if not s.get("completed")]
    by_market = Counter(s.get("market", "?") for s in open_sigs)
    return {
        "yesterday_wins": y_w, "yesterday_losses": y_l,
        "yesterday_r": round(y_r, 2),
        "open_signals": len(open_sigs),
        "open_by_market": dict(by_market),
    }


def _objective_line(user_id: str) -> str:
    try:
        st = agent_core.agent_status(user_id)
        p = st.get("progress", {})
        return (f"Objective: {p.get('daily_pl_pct', 0):+.2f}% of +{p.get('objective_pct')}% "
                f"(week {p.get('weekly_pl_pct', 0):+.1f}%)")
    except Exception:
        return "Objective: unavailable"


def _news_line() -> str:
    evs = [e for e in high_impact(hours=24)]
    if not evs:
        return "News: no high-impact releases in the next 24h."
    tops = "; ".join(cal_label(e) for e in evs[:3])
    return f"News (24h): {tops}"


def _prices_line(budget_s: float = 20.0) -> str:
    """Best-effort prices within a time budget - never blocks the brief."""
    import time as _time

    parts = []
    deadline = _time.monotonic() + budget_s
    for m in INITIAL_MARKETS[:5]:
        if _time.monotonic() > deadline:
            break
        try:
            px = State.provider.latest_price(m)
            if px:
                parts.append(f"{m} {px:g}")
        except Exception:
            pass
    return "Prices: " + (", ".join(parts) if parts else "unavailable")


def _template(ctx: dict, objective: str, news: str, prices: str) -> str:
    w, l = ctx["yesterday_wins"], ctx["yesterday_losses"]
    wr = f"{(100 * w / (w + l)):.0f}%" if (w + l) else "-"
    open_by = ", ".join(f"{k} {v}" for k, v in list(ctx["open_by_market"].items())[:4]) or "none"
    return (
        "☀️ Morning Brief - ForexMind AI\n"
        f"• Last 24h: {w}W/{l}L ({wr}) - {ctx['yesterday_r']:+.2f}R\n"
        f"• Open signals: {ctx['open_signals']} ({open_by})\n"
        f"• {objective}\n"
        f"• {news}\n"
        f"• {prices}\n"
        "Signals fire only on valid setups - the objective guides, never forces."
    )


def build(user_id: str) -> str:
    """Compose today's brief (cached per day)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        if _cache.get(today):
            return _cache[today]

    ctx = _counters(user_id)
    objective = _objective_line(user_id)
    news = _news_line()
    prices = _prices_line()

    text = None
    if get_ai_provider().name == "xkiro":
        context = {"PERFORMANCE_24H": ctx, "OBJECTIVE": objective, "NEWS": news, "PRICES": prices}
        try:
            text = get_ai_provider().complete(
                "Write a concise morning trading briefing for the user in plain English. Maximum 6 short "
                "lines, warm but professional, use the exact numbers provided, end with "
                "one practical focus for the day. Never invent data.",
                context,
            )
        except Exception:
            text = None
    if not text:
        text = _template(ctx, objective, news, prices)

    with _lock:
        _cache[today] = text
    return text


_pushed_today: set = set()   # (user_id, day) - process-level guard, never double-push


def maybe_push_daily(user_id: str, now: Optional[datetime] = None) -> bool:
    """Called by the live loop each minute; pushes at most ONCE per user per day.

    The persisted flag lives on the user's agent_goals doc (looked up by
    userId - the doc id is NOT the user id), plus an in-process guard so
    even a failed flag write can never cause a second push."""
    now = now or datetime.now(timezone.utc)
    day = today_key()
    if now.hour != 7:   # push window: the 07:00 hour (free tier may wake late)
        return False
    if (user_id, day) in _pushed_today:
        return False
    key = f"pushed:{day}"
    store = get_store()
    try:
        docs = store.list("agent_goals", filters={"userId": user_id}, limit=1)
        goals = docs[0] if docs else {}
        if goals.get(key):
            _pushed_today.add((user_id, day))
            return False
        text = build(user_id)
        notify(user_id, "MORNING_BRIEF", "📰 Morning Brief", text)
        try:   # persist the flag; a failed write must not cause a re-push
            if docs:
                store.update("agent_goals", docs[0]["id"], {key: True})
            else:
                store.create("agent_goals", {"userId": user_id, key: True})
        except Exception:
            pass
        _pushed_today.add((user_id, day))
        return True
    except Exception:
        return False


def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
