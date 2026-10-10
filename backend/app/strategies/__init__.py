"""Strategy registry (SPEC §8, §12, §48).

Only registered strategies exist. The AI cannot invent strategies; a new
strategy is added by dropping a module in /strategies and registering it here,
after which it automatically appears in Strategy Manager, Signals, Analytics,
Journal and the Learning Lab.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseStrategy, Candidate
from .strategy_1_zero_lag.strategy import ZeroLagStrategy  # retired 2026-09-30 - kept for signal history / replay
from .strategy_1_vp_pivots.strategy import VPPivotsStrategy
from .strategy_2_ema_atr.strategy import EmaAtrStrategy
from .strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy  # retired 2026-10-07 - kept for signal history / replay
from .strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy
from .strategy_3_fx_specs import (H1BreakoutStrategy, OpeningRangeStrategy,
                                  LiquiditySweepStrategy, Crt4h15mStrategy)

STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    ZeroLagStrategy.id: ZeroLagStrategy,   # retired: doc forced DISABLED (versions.RETIRED_STRATEGIES)
    VPPivotsStrategy.id: VPPivotsStrategy,
    EmaAtrStrategy.id: EmaAtrStrategy,
    MtfSweepBosRetestStrategy.id: MtfSweepBosRetestStrategy,  # retired: forced DISABLED
    SupplyDemandFvgStrategy.id: SupplyDemandFvgStrategy,      # new S2 (v2.0.0, LIVE)
    # Strategy 3 spec family (owner directive 2026-10-10: the new ones take
    # over). Spec-fixed XAUUSD rule engines - no tunable parameters.
    H1BreakoutStrategy.id: H1BreakoutStrategy,
    OpeningRangeStrategy.id: OpeningRangeStrategy,
    LiquiditySweepStrategy.id: LiquiditySweepStrategy,
    Crt4h15mStrategy.id: Crt4h15mStrategy,
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
