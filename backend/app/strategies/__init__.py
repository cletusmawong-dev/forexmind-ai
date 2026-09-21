"""Strategy registry (SPEC §8, §12, §48).

Only registered strategies exist. The AI cannot invent strategies; a new
strategy is added by dropping a module in /strategies and registering it here,
after which it automatically appears in Strategy Manager, Signals, Analytics,
Journal and the Learning Lab.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseStrategy, Candidate
from .strategy_1_zero_lag.strategy import ZeroLagStrategy
from .strategy_2_ema_atr.strategy import EmaAtrStrategy
from .strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy

STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    ZeroLagStrategy.id: ZeroLagStrategy,
    EmaAtrStrategy.id: EmaAtrStrategy,
    MtfSweepBosRetestStrategy.id: MtfSweepBosRetestStrategy,
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
