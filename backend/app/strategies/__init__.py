"""Strategy registry (SPEC §8, §12, §48).

Only registered strategies exist. The AI cannot invent strategies; a new
strategy is added by dropping a module in /strategies and registering it here,
after which it automatically appears in Strategy Manager, Signals, Analytics,
Journal and the Learning Lab.
"""
from __future__ import annotations

from typing import Any, Dict, Type

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
# OWNER WIPE 2 (2026-11-02), then the owner supplied strategies with
# implementation notes (same day) - registered exactly as delivered:
#   strategy_mrms_scalper     MRMS Scalper      5M  mean-reversion ~1.35R
#   strategy_pullback_trend   Pullback Trend    15M H1 bias + pullback 1:2
#   strategy_liquidity_sweep  Liquidity Sweep   15M sweep + rejection 1:2
#   strategy_gold_ny_pullback Gold NY Pullback  15M XAUUSD-only 3R (LOCKED
#                             management plan, boots PAUSED per the guide -
#                             demo forward test before live)
#   strategy_gold_ny_orb      Gold NY ORB       15M XAUUSD-only 2R (LOCKED
#                             plan, boots PAUSED per the guide)
# The FX three run the Universal ladder post-entry; the gold pair carries
# its own locked plan block in the trade manager (guide section 4).
from .strategy_mrms_scalper import MRMSScalper
from .strategy_pullback_trend import PullbackInTrend
from .strategy_liquidity_sweep import LiquiditySweep
from .gold_ny_pullback import GoldNYPullbackStrategy
from .gold_ny_orb import GoldNYORBStrategy
from .gold_asia_range_bo import GoldAsiaRangeBOStrategy

# canonical registry ids - the tests assert against THIS (single source of
# truth, so adding a strategy never means re-pinning eight files)
OWNER_STRATEGY_IDS = frozenset({
    MRMSScalper.id, PullbackInTrend.id, LiquiditySweep.id,
    GoldNYPullbackStrategy.id, GoldNYORBStrategy.id,
    GoldAsiaRangeBOStrategy.id,
})

STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    MRMSScalper.id: MRMSScalper,
    PullbackInTrend.id: PullbackInTrend,
    LiquiditySweep.id: LiquiditySweep,
    GoldNYPullbackStrategy.id: GoldNYPullbackStrategy,
    GoldNYORBStrategy.id: GoldNYORBStrategy,
    GoldAsiaRangeBOStrategy.id: GoldAsiaRangeBOStrategy,
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


# ---------------------------------------------------------------------------
# Architecture (owner spec 2026-11-02): ONE strategy = ONE protected module.
# The registry is the ONLY door to existence; the state of a strategy lives
# in learning.versions.LIFECYCLE (never in the module); new rule logic is a
# NEW module + registry entry approved by a human - never an edit of a live
# file. Promotion doctrine: strategies/versions/README.md.
# ---------------------------------------------------------------------------


def manifest() -> Dict[str, Dict[str, Any]]:
    """id -> {module, class, version, state, executable} for every registered
    strategy. Lazy lifecycle import - learning.versions sits on top of this
    registry; importing it at module level would be circular."""
    from ..learning.versions import can_execute, lifecycle_of
    out: Dict[str, Dict[str, Any]] = {}
    for sid, cls in STRATEGY_CLASSES.items():
        out[sid] = {"module": cls.__module__, "class": cls.__name__,
                    "version": cls.version, "state": lifecycle_of(sid),
                    "executable": can_execute(sid)}
    return out


def strategies_by_state(state: str) -> Dict[str, BaseStrategy]:
    """All registered strategies currently in a lifecycle state
    (LIVE / SHADOW / RESEARCH / SLEEPING / RETIRED)."""
    return {sid: get_strategy(sid) for sid, m in manifest().items()
            if m["state"] == state}
