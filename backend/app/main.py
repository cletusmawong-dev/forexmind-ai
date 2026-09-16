"""ForexMind AI - FastAPI application.

The agent work loop (SPEC §15) runs here as a replay task:
GOAL -> OBSERVE -> ANALYZE -> CHECK STRATEGIES -> VALIDATE -> GENERATE SIGNAL
-> NOTIFY -> (user enters manually) -> TRACK OUTCOME -> ANALYZE -> LEARN.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent import core as agent_core
from .config import INITIAL_MARKETS, settings
from .seed import seed_if_empty
from .db.store import get_store
from .state import State, init_state
from .api import (routes_agent, routes_auth, routes_journal, routes_learning,
                  routes_markets, routes_misc, routes_signals, routes_strategies,
                  routes_trades, routes_telegram,
           routes_intelligence)

app = FastAPI(title="ForexMind AI", version="0.1.0",
              description="AI trading research & signal agent - signals only, never execution.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

for r in (routes_auth, routes_markets, routes_signals, routes_trades, routes_agent,
          routes_learning, routes_strategies, routes_journal, routes_misc, routes_telegram,
          routes_intelligence):
    app.include_router(r.router, prefix=settings.api_prefix)


_scan_counter = 0
_current_task = "Initializing"


@app.on_event("startup")
async def startup():
    try:
        from .keep_warm import start as keep_warm_start
        keep_warm_start()
    except Exception:
        pass
    init_state()
    try:
        from .seed import ensure_strategy_docs
        ensure_strategy_docs()   # strategy registry only - never demo users/signals
    except Exception as e:
        # Quota gate or transient DB issue: boot anyway so market-data and
        # health endpoints stay up; DB-backed features degrade honestly.
        print(f"[startup] database init degraded: {e}")
    if not State.provider.is_demo:
        asyncio.create_task(live_loop())
        asyncio.create_task(warm_cache())
    elif settings.replay_enabled:
        asyncio.create_task(replay_loop())


async def replay_loop():
    """Slow-motion replay of the labeled historical dataset + live agent loop."""
    global _scan_counter, _current_task
    step = 0
    while True:
        try:
            await asyncio.sleep(settings.replay_interval_sec)
            State.provider.step(1)
            step += 1

            # 1) follow-up on active signals (TP/SL tracking)
            _current_task = "Tracking active signals"
            for market in State.provider.markets():
                State.tracker.update_market(market)

            # 2) observe + analyze every time a new 15M candle closes
            if step % 3 == 0:
                user_ids = [u["id"] for u in State.store.list("users", limit=50)]
                for market in INITIAL_MARKETS:
                    _current_task = f"Scanning {market}"
                    for uid in user_ids:
                        for tf in ("15M", "1H"):
                            await asyncio.to_thread(State.engine.scan, uid, market, tf, log_activity=False)
                    agent_core.log_scanning([market], "15M")

            # 3) periodic learning pass over newly completed signals
            if step % 40 == 0:
                _current_task = "Reviewing completed signals"
                from .learning.analysis import analyze_closed_signals
                from .learning.hypotheses import propose_from_lessons
                for uid in {u["id"] for u in State.store.list("users", limit=50)}:
                    analyze_closed_signals(uid)
                    propose_from_lessons(uid, max_new=1)

            _current_task = "Monitoring markets"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the loop die
            agent_core.log(f"Agent loop warning: {e}", kind="WARN")
            await asyncio.sleep(2)


_users_cache: list = []
_users_cache_ts = 0.0


def _users_cached():
    """User id list, cached 10 min (avoids a Firestore read every loop tick)."""
    global _users_cache, _users_cache_ts
    if not _users_cache or time.time() - _users_cache_ts > 600:
        try:
            _users_cache[:] = [u["id"] for u in State.store.list("users", limit=50)]
            _users_cache_ts = time.time()
        except Exception:
            pass
    return _users_cache or []   # no users -> nothing to scan (never invent demo data)


async def warm_cache():
    """Pre-fetch the working set (respects provider rate limits) so the first
    UI request is served from cache."""
    for market in INITIAL_MARKETS:
        for tf in ("5M", "15M", "1H", "4H"):
            try:
                await asyncio.to_thread(State.provider.get_candles, market, tf, 400)
            except Exception:
                pass


async def live_loop():
    """Real-market agent loop: track active signals every minute, scan on
    every closed 15M/1H candle boundary, run a learning pass every ~6h."""
    global _scan_counter, _current_task, last_deal_sync
    last_scan_15m = 0
    last_scan_1h = 0
    last_learning = 0.0
    last_deal_sync = 0.0
    while True:
        try:
            await asyncio.sleep(60)
            now = time.time()
            _current_task = "Tracking active signals"
            for market in INITIAL_MARKETS:
                await asyncio.to_thread(State.tracker.update_market, market)

            # news pre-alerts (Telegram) + daily morning brief - best effort, off the event loop
            def _news_job():
                from .market_data.calendar import pre_alerts, label as cal_label
                from .notifications.service import notify as _notify
                for ev in pre_alerts():
                    for uid in _users_cached():
                        _notify(uid, "NEWS_ALERT", "⚠️ High-impact news in ~15 min", cal_label(ev))

            def _brief_job():
                from .agent.brief import maybe_push_daily
                for uid in _users_cached():
                    maybe_push_daily(uid)

            try:
                await asyncio.to_thread(_news_job)
            except Exception:
                pass
            try:
                await asyncio.to_thread(_brief_job)
            except Exception:
                pass

            # MT5 deal sync every ~5 min: confirm closed trades from the broker
            try:
                if now - last_deal_sync > 300:
                    last_deal_sync = now
                    from .execution.mt5 import expire_stale_commands, sync_deals
                    await asyncio.to_thread(expire_stale_commands)
                    for uid in _users_cached():
                        await asyncio.to_thread(sync_deals, uid)
            except Exception:
                pass

            cur_15m = int(now // 900)
            if cur_15m != last_scan_15m:
                last_scan_15m = cur_15m
                user_ids = _users_cached()
                from .market_data.calendar import is_blackout
                for market in INITIAL_MARKETS:
                    _current_task = f"Scanning {market}"
                    try:  # candle storage - runs even in blackout, warms provider cache for scans
                        from .market_data import candle_store
                        df15 = await asyncio.to_thread(State.provider.get_candles, market, "15M", candle_store.KEEP)
                        await asyncio.to_thread(candle_store.record, market, df15)
                        if df15 is not None:
                            from .learning import regime as regime_mod
                            await asyncio.to_thread(regime_mod.record, market, df15)
                    except Exception:
                        pass
                    try:
                        blocked, ev = is_blackout(market)
                    except Exception:
                        blocked, ev = False, None
                    if blocked:
                        agent_core.log(f"News blackout - skipping {market} "
                                       f"({ev['country']} {ev['title']})", kind="NEWS", market=market)
                        continue
                    for uid in user_ids:
                        for tf in ("15M", "1H"):
                            await asyncio.to_thread(State.engine.scan, uid, market, tf, log_activity=False)
                    agent_core.log_scanning([market], "15M")
                _current_task = "Monitoring markets"

            cur_1h = int(now // 3600)
            if cur_1h != last_scan_1h:
                last_scan_1h = cur_1h
                _current_task = "Tracking active signals"

            if now - last_learning > 6 * 3600:
                last_learning = now
                try:  # Research Lab: discover new one-variable-testable patterns
                    for _uid in (await _user_ids()):
                        await asyncio.to_thread(
                            __import__("app.learning.research", fromlist=["discover"]).discover,
                            _uid)
                except Exception:
                    pass
                _current_task = "Reviewing completed signals"
                from .learning.analysis import analyze_closed_signals
                from .learning.hypotheses import propose_from_lessons
                for uid in {u["id"] for u in State.store.list("users", limit=50)}:
                    analyze_closed_signals(uid)
                    propose_from_lessons(uid, max_new=1)
                _current_task = "Monitoring markets"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the loop die
            agent_core.log(f"Agent loop warning: {e}", kind="WARN")
            await asyncio.sleep(5)


# Firestore daily-quota exhaustion -> honest 503 everywhere, never a 500.
_QE = getattr(type(get_store()), "_QuotaExhausted", None)
if _QE is not None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(_QE)
    async def quota_exhausted_handler(request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": "Database daily quota reached - the app pauses until the daily reset. All data is safe."},
        )


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name,
            "provider": State.provider.name if State.provider else None,
            "demo": State.provider.is_demo if State.provider else True}


@app.get("/api/agent/current-task")
def current_task():
    return {"task": _current_task}
