"""ForexMind AI - FastAPI application.

The agent work loop (SPEC §15) runs here as a replay task:
GOAL -> OBSERVE -> ANALYZE -> CHECK STRATEGIES -> VALIDATE -> GENERATE SIGNAL
-> NOTIFY -> (user enters manually) -> TRACK OUTCOME -> ANALYZE -> LEARN.
"""
from __future__ import annotations

import asyncio
import contextlib

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent import core as agent_core
from .config import INITIAL_MARKETS, settings
from .seed import seed_if_empty
from .state import State, init_state
from .api import (routes_agent, routes_auth, routes_journal, routes_learning,
                  routes_markets, routes_misc, routes_signals, routes_strategies,
                  routes_trades)

app = FastAPI(title="ForexMind AI", version="0.1.0",
              description="AI trading research & signal agent - signals only, never execution.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

for r in (routes_auth, routes_markets, routes_signals, routes_trades, routes_agent,
          routes_learning, routes_strategies, routes_journal, routes_misc):
    app.include_router(r.router, prefix=settings.api_prefix)


_scan_counter = 0
_current_task = "Initializing"


@app.on_event("startup")
async def startup():
    init_state()
    seed_if_empty()
    agent_core.ensure_user_docs("demo-user")
    if settings.replay_enabled:
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
                            State.engine.scan(uid, market, tf, log_activity=False)
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


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name}


@app.get("/api/agent/current-task")
def current_task():
    return {"task": _current_task}
