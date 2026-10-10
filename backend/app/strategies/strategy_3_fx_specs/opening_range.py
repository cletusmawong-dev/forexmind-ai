"""strategy_3_opening_range - NY opening range breakout (spec opening_range_v1).

Spec-fixed rules: opening range = 13:30-13:59 UTC (six M5 bars) high/low;
enter 14:00-16:59 on the first break of the OR; stop at the other OR side;
single target 1.5R; break-even marker at +1R.
"""
from ..strategy_3_fx_specs.adapter import FxSpecStrategy
from ...research.fx_engines import opening_range


class OpeningRangeStrategy(FxSpecStrategy):
    id = "strategy_3_opening_range"
    name = "Opening Range Breakout (XAUUSD)"
    short_name = "ORB"
    description = ("Specification rules v1 (2026-10-10): New York opening "
                   "range from the six M5 bars 13:30-13:59 UTC; enter on the "
                   "first break of the range between 14:00-16:59 UTC; stop at "
                   "the opposite side of the range; target 1.5R; break-even "
                   "marker at +1R.")
    base_tf = "5M"

    def _evaluate(self, frames):
        return opening_range(frames.get("base"))
