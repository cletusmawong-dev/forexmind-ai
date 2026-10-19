"""Strategy registry (SPEC §8, §12, §48).

Only registered strategies exist. The AI cannot invent strategies; a new
strategy is added by dropping a module in /strategies and registering it here,
after which it automatically appears in Strategy Manager, Signals, Analytics,
Journal and the Learning Lab.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseStrategy, Candidate

# OWNER WIPE (2026-10-10): every strategy removed at the owner's explicit
# request ("clear all the strategies ... don't leave a single strategy
# there"). The registry is intentionally EMPTY: nothing is scanned, nothing
# generates signals, nothing can trade, and the Strategies screen shows no
# strategies. The strategy modules remain in the repo UNREGISTERED purely so
# historical signals / journal entries keep their meaning; they never run.
# ensure_strategy_docs() purges any strategy docs left in the database on
# every boot. Re-adding a strategy = registering its class here again.
#
# 2026-10-10, later the same day: the owner supplied TWO new gold strategy
# specifications (FOREXMIND H1 Trend Breakout + FOREXMIND Gold ORB) with the
# instruction "Implement these two Gold strategies as written." They are the
# only registered strategies. Per the specs' own rule 7 the docs are created
# PAUSED (see learning.versions.START_PAUSED) - activation is an explicit
# owner step after tests, historical validation and forward demo testing.
from .gold_specs import GoldOrbStrategy, H1TrendBreakoutStrategy

STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    H1TrendBreakoutStrategy.id: H1TrendBreakoutStrategy,
    GoldOrbStrategy.id: GoldOrbStrategy,
}

_INSTANCES: Dict[str, BaseStrategy] = {}



def get_strategy(strategy_id: str) -> BaseStrategy:
    if strategy_id not in STRATEGY_CLASSES:
        raise KeyError(f"Unknown strategy '{strategy_id}'")
    if strategy_id not in _INSTANCES:
        _INSTANCES[strategy_id] = STRATEGY_CLASSES[strategy_id]()
    return _INSTANCES[strategy_id]


def all_strategies() -> Dict[str, BaseStrategy]:
    return {sid: get_strategy(sid) for sid in STRATEGY_CLASSES}
