"""Economic calendar (ForexFactory weekly JSON) + news blackout logic.

High-impact releases (NFP, CPI, FOMC...) are the #1 cause of stop-hunts and
fake signals. The agent therefore:

  * skips signal generation for a market while a high-impact event for its
    currencies is within ±30 minutes,
  * sends a Telegram pre-alert 15 minutes before such an event.

The feed is free (nfs.faireconomy.media), cached for an hour, and every
failure degrades gracefully: no calendar -> no blackout -> normal scanning.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

# which currencies matter per app market
MARKET_CURRENCIES: Dict[str, Tuple[str, ...]] = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "XAUUSD": ("USD",),
    "NAS100": ("USD",),
}

BLACKOUT_MIN = 30       # skip signals +/- this many minutes around the event
PRE_ALERT_MIN = 15      # telegram pre-alert this many minutes before

_lock = threading.RLock()
_cache: List[dict] = []
_cache_ts = 0.0
_alerted: set = set()   # ids of events already pre-alerted this process


def _parse_dt(raw: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch(force: bool = False) -> List[dict]:
    global _cache, _cache_ts
    with _lock:
        if not force and _cache and (time.monotonic() - _cache_ts) < 3600:
            return _cache
    try:
        r = requests.get(FEED, headers=UA, timeout=15)
        events = r.json() if r.status_code == 200 else []
    except Exception:
        events = []
    out = []
    for e in events:
        dt = _parse_dt(e.get("date", ""))
        if not dt:
            continue
        out.append({
            "id": f'{e.get("country","")}-{e.get("title","")}-{dt.isoformat()}',
            "title": e.get("title", ""),
            "country": e.get("country", ""),
            "impact": e.get("impact", ""),
            "time": dt.isoformat(),
            "ts": dt,
        })
    with _lock:
        if out:  # keep old cache on empty/failed fetch
            _cache, _cache_ts = out, time.monotonic()
        return _cache or out


def high_impact(market: Optional[str] = None, hours: float = 48) -> List[dict]:
    """Upcoming high-impact events (optionally filtered to a market's currencies)."""
    now = datetime.now(timezone.utc)
    cur = MARKET_CURRENCIES.get(market or "", None)
    out = []
    for e in _fetch():
        if e["impact"] != "High":
            continue
        if cur and e["country"] not in cur:
            continue
        delta_h = (e["ts"] - now).total_seconds() / 3600
        if -0.5 <= delta_h <= hours:
            out.append(e)
    out.sort(key=lambda e: e["ts"])
    return out


def is_blackout(market: str, now: Optional[datetime] = None) -> Tuple[bool, Optional[dict]]:
    """True while a high-impact event for this market is within +/- 30 minutes."""
    now = now or datetime.now(timezone.utc)
    for e in high_impact(market, hours=1):
        if abs((e["ts"] - now).total_seconds()) <= BLACKOUT_MIN * 60:
            return True, e
    return False, None


def pre_alerts(window_min: int = PRE_ALERT_MIN) -> List[dict]:
    """High-impact events entering the pre-alert window; each id returns once."""
    now = datetime.now(timezone.utc)
    out = []
    with _lock:
        for e in high_impact(hours=2):
            delta = (e["ts"] - now).total_seconds() / 60
            if 0 < delta <= window_min and e["id"] not in _alerted:
                _alerted.add(e["id"])
                out.append(e)
    return out


def label(e: dict) -> str:
    mins = int((e["ts"] - datetime.now(timezone.utc)).total_seconds() / 60)
    when = f"in {mins}m" if mins >= 0 else f"{-mins}m ago"
    return f'{e["country"]} {e["title"]} · {when}'
