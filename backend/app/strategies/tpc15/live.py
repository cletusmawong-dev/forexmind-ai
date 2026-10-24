"""TPC-15 LIVE adapter - registered platform strategy (owner directive
2026-10-24: "Make the tpc-15 live").

The pure engine in .strategy stays untouched (rules, tests, isolation).
This adapter ONLY translates the engine's structured result into the
platform Candidate format so the EXISTING pipeline does everything else:
per-user guards, notifications, journal, execution routing, risk walls.

Execution mapping (documented, no rule changes): the guide's
NEXT_CANDLE_OPEN entry is honored by platform timing - scans run right
after an M15 candle closes, so the immediate market order fills at (or
within spread of) the next candle's open. The submitted SL/TP are the
reference-level levels (confirmation-close based, 1.25R exactly); the
execution layer records intended_rr / executed_rr / entry_slippage on
every fill, and the pre-trade entry-drift guard refuses orders when the
market has moved beyond 0.25R of the reference entry.

No instruments are pinned: the strategy evaluates every market the
owner selected in Settings (markets = None). Session (14:00-18:00 UTC),
EMAs, pullback, confirmation, five-candle stop and the 1.25R target are
exactly the engine's - zero rules were changed to go live.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy, Candidate
from . import strategy as engine
from .strategy import CONFIG, evaluate_setup


class TPC15PullbackStrategy(BaseStrategy):
    id = "strategy_tpc15_pullback"
    name = "TPC-15 Trend Pullback"
    short_name = "TPC-15 Pullback"
    description = (
        "TPC-15: Trend Pullback Confirmation (owner guide, made live "
        "2026-10-24). M15, 14:00-18:00 UTC: price beyond EMA 200 with "
        "EMA 9 over/under EMA 21, pullback touches EMA 21, confirmation "
        "candle closes beyond the previous candle's high (BUY) / low "
        "(SELL); entry at the next candle's open; stop beyond the lowest "
        "low / highest high of the five completed candles before the "
        "confirmation; target 1.25R. Trades the markets selected in "
        "Settings.")
    version = "1.0"
    markets = None                       # no instrument pin - owner selection rules
    timeframes = ["15M"]                 # the guide's entry timeframe
    min_bars = 210                       # EMA 200 warmup + evaluation window
    base_params: Dict[str, Any] = {}     # rule-fixed, no tunables
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    configured_rr = 1.25

    # ------------------------------------------------------------------
    def detect_signal(self, df: Optional[pd.DataFrame], market: str,
                      timeframe: str, params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None
                      ) -> Optional[Candidate]:
        if timeframe not in self.timeframes:
            return None
        res = evaluate_setup(df)         # closed candles only; session + EMAs inside
        if not isinstance(res, dict) or res.get("status") != engine.S_SIGNAL:
            return None
        direction = res.get("direction")
        entry, sl, tp = (res.get("reference_entry"), res.get("stop_loss"),
                         res.get("take_profit"))
        if direction not in ("BUY", "SELL") or entry is None or sl is None or tp is None:
            return None
        entry, sl, tp = float(entry), float(sl), float(tp)
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        if self.configured_rr and abs(res.get("tp_multiple", 0) - self.configured_rr) > 1e-9:
            return None                  # engine must produce the guide's own multiple
        return Candidate(
            strategy_id=self.id, market=market, timeframe="15M",
            direction=direction,
            candle_time=str(res.get("confirmation_candle") or ""),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=[tp],
            risk=risk, rr_primary=self.configured_rr,
            score=60, score_components={"tpc15_rule": 60.0},
            checks=list(res.get("checks") or []),
            analysis="; ".join(res.get("reasons") or []),
            params={}, params_version=self.version,
            extra={"spec_id": "TPC-15", "setup_id": res.get("setup_id"),
                   "entry_rule": "NEXT_CANDLE_OPEN",
                   "stop_window": res.get("stop_window"),
                   "session": (score_context or {}).get("session")})

    # -- interface completeness (the TPC-15 engine is the entry path;
    #    backtests return no trades instead of a lookalike approximation) --
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "tpc15 engine (detect_signal only)"}

    def detect_on_bar(self, state, i):
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        tp = float(event.get("tp", entry + (risk * CONFIG["take_profit_r"]
                                            if direction == "BUY"
                                            else -risk * CONFIG["take_profit_r"])))
        return Candidate(strategy_id=self.id, market=market, timeframe="15M",
                         direction=direction, candle_time=str(df.index[i]),
                         entry=entry, entry_zone=[entry, entry], sl=sl,
                         tps=[tp], risk=risk, rr_primary=self.configured_rr,
                         score=60, score_components={"tpc15_rule": 60.0},
                         checks=[], analysis="tpc15 setup", params={},
                         params_version=self.version, extra={"spec_id": "TPC-15"})

    def calculate_risk(self, df, state, i, direction, params):
        entry = float(df["close"].iloc[i])
        extreme = df.iloc[max(0, i - engine.LOOKBACK):i]
        if direction == "BUY":
            sl = float(extreme["low"].min())
        else:
            sl = float(extreme["high"].max())
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str, params):
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * CONFIG["take_profit_r"]]

    def explain_signal(self, state, i, direction, params, extra):
        reasons = list(engine.REASON_BUY if direction == "BUY" else engine.REASON_SELL)
        return ([{"label": "TPC-15 rules", "ok": True, "detail": r} for r in reasons],
                "TPC-15: trend + EMA21 pullback + confirmation close "
                "(guide rules, unmodified).")
