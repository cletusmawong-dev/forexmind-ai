"""strategy_3_h1_breakout - H1 session breakout (spec h1_breakout_v1).

Spec-fixed rules: Mon-Fri 17:00-21:59 UTC, long when the H1 candle closes
above the prior 20-bar high; stop = min(prior 10-bar low, entry - ATR14);
single target 1.25R; move stop to entry at +0.5R (marker only).
"""
from ..strategy_3_fx_specs.adapter import FxSpecStrategy
from ...research.fx_engines import h1_breakout


class H1BreakoutStrategy(FxSpecStrategy):
    id = "strategy_3_h1_breakout"
    name = "H1 Session Breakout (XAUUSD)"
    short_name = "H1 Breakout"
    description = ("Specification rules v1 (2026-10-10): long-only London/NY "
                   "session breakout - H1 close above the prior 20-bar high "
                   "between 17:00-21:59 UTC; stop at the prior 10-bar low or "
                   "1x ATR14, whichever is closer; target 1.25R; break-even "
                   "marker at +0.5R.")
    base_tf = "1H"

    def _evaluate(self, frames):
        return h1_breakout(frames.get("base"))
