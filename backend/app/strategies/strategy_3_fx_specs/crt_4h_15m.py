"""strategy_3_crt_4h_15m - CRT 4H -> 15M (spec crt_4h_15m_v1).

Spec-fixed rules: the previous H4 candle's range is the CRT range; the
latest H4 candle sweeps a boundary and closes back inside (sweep extreme
within 1 M15 ATR14 of the boundary - key-level proxy); entry on the first
M15 close beyond the prior 5-bar structure in the reversal direction; stop
at the H4 sweep extreme; single target 1R; break-even marker at +0.5R.
"""
from ..strategy_3_fx_specs.adapter import FxSpecStrategy
from ...research.fx_engines import crt_4h_15m


class Crt4h15mStrategy(FxSpecStrategy):
    id = "strategy_3_crt_4h_15m"
    name = "CRT 4H -> 15M (XAUUSD)"
    short_name = "CRT 4H/15M"
    description = ("Specification rules v1 (2026-10-10): Power of Three / "
                   "candle-range theory - previous H4 range is swept and "
                   "reclaimed (sweep extreme within 1x M15 ATR14 of the "
                   "boundary), entry on the M15 close beyond the prior 5-bar "
                   "structure; stop at the H4 sweep extreme; target 1R; "
                   "break-even marker at +0.5R.")
    base_tf = "15M"
    extra_tfs = {"h4": "4H"}

    def _evaluate(self, frames):
        return crt_4h_15m(frames.get("h4"), frames.get("base"))
