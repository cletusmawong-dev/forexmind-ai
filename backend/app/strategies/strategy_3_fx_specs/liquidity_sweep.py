"""strategy_3_liquidity_sweep - liquidity sweep reversal (spec liquidity_sweep_v1).

Spec-fixed rules: ranging regime only (H1 ADX14 <= 20); a single M15 candle
sweeps a 20-bar extreme and closes back inside; enter in the rejection
direction on the next M15 open; stop at the sweep extreme; single target
0.75R; no break-even marker.
"""
from ..strategy_3_fx_specs.adapter import FxSpecStrategy
from ...research.fx_engines import liquidity_sweep


class LiquiditySweepStrategy(FxSpecStrategy):
    id = "strategy_3_liquidity_sweep"
    name = "Liquidity Sweep Reversal (XAUUSD)"
    short_name = "Liq Sweep"
    description = ("Specification rules v1 (2026-10-10): fade liquidity "
                   "grabs in a ranging regime (H1 ADX14 <= 20) - one M15 "
                   "candle sweeps the 20-bar high/low and closes back inside; "
                   "entry against the sweep; stop beyond the sweep extreme; "
                   "target 0.75R; no break-even marker.")
    base_tf = "15M"

    def _evaluate(self, frames):
        return liquidity_sweep(frames.get("base"))
