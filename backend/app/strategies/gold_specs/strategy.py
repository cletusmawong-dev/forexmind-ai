"""Live strategy classes for the two FOREXMIND gold specs (2026-10-10).

Thin, deterministic adapters: the engines above are the source of truth;
these classes only translate engine output into the platform's Candidate
format so the strategies appear on the Strategies screen and are scanned by
the existing signal engine. Rules are spec-FIXED: no tunable parameters, no
experiment variables, nothing added or reinterpreted.

Execution: signals flow through the EXISTING pipeline only. Per the owner's
rule 7 the strategies ship with docs created PAUSED - activation (and hence
any trading) is an explicit owner step after tests, historical validation
and forward demo testing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy, Candidate
from . import gold_orb, h1_trend_breakout


class GoldSpecStrategy(BaseStrategy):
    id = ""
    name = ""
    short_name = ""
    description = ""
    version = "1.1"
    # OWNER BRIEF 2026-10-24 (s.1): no instrument is hardcoded. The specs'
    # RULES are fixed per strategy; the INSTRUMENTS follow the user's
    # selected markets (Settings -> Allowed markets). Each selected pair is
    # scanned independently with the same deterministic rules.
    markets = None                            # None = every scanned market
    configured_rr = None                      # spec target multiple (subclass)
    timeframes = ["15M", "1H", "4H", "1D"]   # scanned tfs; the engine reads
    min_bars = 3                             # its own base frame
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    engine_id = ""

    def _frames(self, df: Optional[pd.DataFrame], timeframe: str,
                higher: Optional[Dict[str, pd.DataFrame]]) -> Dict[str, Optional[pd.DataFrame]]:
        higher = higher or {}
        return {tf: (df if timeframe == tf else higher.get(tf))
                for tf in self.required_frames}

    def _evaluate(self, frames: Dict[str, Optional[pd.DataFrame]]) -> Dict[str, Any]:
        raise NotImplementedError

    def detect_signal(self, df: Optional[pd.DataFrame], market: str, timeframe: str,
                      params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None,
                      lookback: int = 3) -> Optional[Candidate]:
        if (self.markets and market not in self.markets) \
                or timeframe not in self.timeframes:
            return None
        if df is None and not (higher_frames or {}):
            return None
        res = self._evaluate(self._frames(df, timeframe, higher_frames))
        if not isinstance(res, dict) or res.get("status") != "SIGNAL":
            return None
        direction = {"LONG": "BUY", "SHORT": "SELL"}.get(res.get("direction"))
        entry, sl, tp = res.get("entry"), res.get("sl"), res.get("tp")
        if direction is None or entry is None or sl is None or tp is None:
            return None
        entry, sl, tp = float(entry), float(sl), float(tp)
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        rr = float(res.get("tp_r", abs(tp - entry) / risk))
        if self.configured_rr and abs(rr - self.configured_rr) > 0.02:
            # spec integrity: the engine must produce the spec's own target
            # multiple - anything else is an engine bug, not a trade
            return None
        return Candidate(
            strategy_id=self.id, market=market, timeframe=self.base_tf,
            direction=direction, candle_time=str(res.get("signal_time") or ""),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=[tp],
            risk=risk, rr_primary=rr,
            score=60, score_components={"spec_rule": 60.0},
            checks=list(res.get("checks") or []),
            analysis=str(res.get("summary", "spec setup")),
            params={}, params_version=self.version,
            extra={"spec_id": self.engine_id, "be_marker_r": res.get("be_marker_r"),
                   "entry_time": res.get("entry_time"),
                   "session": (score_context or {}).get("session")})

    # -- interface completeness (engines are the entry path; backtests return
    #    no trades instead of a lookalike approximation of the spec) --
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "spec engine (detect_signal only)"}

    def detect_on_bar(self, state, i):
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        return Candidate(strategy_id=self.id, market=market, timeframe=self.base_tf,
                         direction=direction, candle_time=str(df.index[i]),
                         entry=entry, entry_zone=[entry, entry], sl=sl,
                         tps=[float(event.get("tp", entry))], risk=risk,
                         rr_primary=abs(float(event.get("tp", entry)) - entry) / risk,
                         score=60, score_components={"spec_rule": 60.0},
                         checks=[], analysis="spec setup", params={},
                         extra={"spec_id": self.engine_id})

    def calculate_risk(self, df, state, i, direction, params):
        entry = float(df["close"].iloc[i])
        sl = float(df["low"].iloc[i]) if direction == "BUY" else float(df["high"].iloc[i])
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str, params):
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk]

    def explain_signal(self, state, i, direction, params, extra):
        return ([{"label": "spec rules", "ok": True,
                  "detail": f"{self.engine_id}: all specified conditions met"}],
                f"{self.short_name}: deterministic specification rules met "
                f"on {self.base_tf} completed candles (UTC).")


class H1TrendBreakoutStrategy(GoldSpecStrategy):
    id = h1_trend_breakout.STRATEGY_ID
    name = "FOREXMIND H1 Trend Breakout"
    short_name = "H1 Trend Breakout"
    description = ("Spec v1 (2026-10-10) H1: close beyond the prior "
                   "20-bar high/low plus 0.05xATR14, EMA20>EMA50 on H1 and "
                   "EMA20>EMA50>EMA200 on H4 (short: inverse), ADX14 > 25; "
                   "entry at the next M15 open; stop 1.2xATR14; target 1.25R; "
                   "break-even at +0.5R. Long and short. Trades the markets "
                   "selected in Settings.")
    configured_rr = 1.25
    base_tf = "1H"
    required_frames = ("1H", "M15", "4H")     # M15 = the entry reference frame
    engine_id = "forexmind_h1_trend_breakout"

    def _frames(self, df, timeframe, higher):
        higher = higher or {}
        return {"1H": df if timeframe == "1H" else higher.get("1H"),
                "M15": df if timeframe == "15M" else higher.get("15M"),
                "4H": df if timeframe == "4H" else higher.get("4H")}

    def _evaluate(self, frames):
        return h1_trend_breakout.evaluate(frames.get("1H"), frames.get("M15"),
                                          frames.get("4H"))


class GoldOrbStrategy(GoldSpecStrategy):
    id = gold_orb.STRATEGY_ID
    name = "FOREXMIND Gold ORB"
    short_name = "Gold ORB"
    description = ("Spec v1 (2026-10-10) M5 UTC: opening range from "
                   "the six M5 candles 13:30-13:55; long on the FIRST candle "
                   "in 14:00-16:59 UTC closing above the range high (close > "
                   "open); entry at that close; stop at the range low; target "
                   "1.5R; break-even at +1R; one trade per opening range. "
                   "Trades the markets selected in Settings.")
    configured_rr = 1.5
    base_tf = "5M"
    required_frames = ("5M",)
    engine_id = "forexmind_gold_orb"

    def _frames(self, df, timeframe, higher):
        higher = higher or {}
        return {"5M": df if timeframe == "5M" else higher.get("5M")}

    def _evaluate(self, frames):
        return gold_orb.evaluate(frames.get("5M"))
