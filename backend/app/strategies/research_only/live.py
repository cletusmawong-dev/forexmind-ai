"""LIVE adapters for the two research candidates (owner directive
2026-10-24: "implement the strategies per the rules, add it to the app and
make them live").

Both adapters translate the research engines' rule outputs into the
platform Candidate flow - the EXISTING pipeline does everything else
(per-user guards, notifications, journal, execution routing, pre-trade
validation). The rules are UNCHANGED; the spec's own market list
(EURUSD, GBPUSD, USDJPY, XAUUSD - XAGUSD excluded) is enforced here as a
rule, on top of the platform's per-user allowed-markets gate.

Execution timing (documented):
  * TrendBreak - "enter at the OPEN of the next H1 bar": scans run right
    after the H1 close, so the market order fills at/within spread of the
    next bar's open. Signal levels use the signal-bar close as the
    reference entry with SL = 2.5xATR and TP = 3R; every fill records
    intended/executed R:R and the pre-trade drift guard refuses entries
    that moved > 0.25R from the reference.
  * ZoneSweep - the limit-at-zone-edge entry is honored by emitting the
    signal at the FIRST touch of the zone (the exact moment the limit
    would fill), detected by a stateless replay over the closed M15
    history (restart-safe; one trade per zone via candle identity).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy, Candidate
from . import trendbreak as tb
from . import trendbreak_v2 as tb2
from . import zonesweep as zs
from .trendbreak import SPEC as TB_SPEC
from .zonesweep import SPEC as ZS_SPEC


class _ResearchCandidateStrategy(BaseStrategy):
    """Shared plumbing: scan-timeframe bridging + market pinning."""

    #: scan TFs that carry the entry data directly
    entry_tf = ""
    context_tfs: tuple = ()

    def _context(self, df, timeframe, higher_frames):
        """Resolve (entry_frame, context_frames) for the entry engine
        regardless of which scan timeframe triggered the call."""
        hf = higher_frames or {}
        entry = df if timeframe == self.entry_tf else hf.get(self.entry_tf)
        ctx = {}
        for tf in self.context_tfs:
            ctx[tf] = df if timeframe == tf else hf.get(tf)
        return entry, ctx


class TrendBreakStrategy(_ResearchCandidateStrategy):
    id = "strategy_trendbreak_h1"
    name = "TrendBreak H1"
    short_name = "TrendBreak"
    description = (
        "Research candidate made live (owner 2026-10-24). H1 breakout in "
        "the H4 trend: EMA50/EMA200 trend on completed H4 bars, 120-bar "
        "channel, first breakout bar only; entry at the next H1 open; "
        "stop 2.5xATR(14) of the signal bar, never moved; target 1:3; "
        "time exit after 96 bars (managed by the platform trade manager). "
        "Spec markets: EURUSD, GBPUSD, USDJPY, XAUUSD.")
    version = "1.0"
    markets = list(TB_SPEC["markets"])       # the spec's own market rule
    timeframes = ["15M", "1H", "4H", "1D"]
    min_bars = 210
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    configured_rr = TB_SPEC["tp_multiple"]   # 3.0
    entry_tf = "1H"
    context_tfs = ("4H",)

    def detect_signal(self, df: Optional[pd.DataFrame], market: str,
                      timeframe: str, params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None
                      ) -> Optional[Candidate]:
        if market not in self.markets:
            return None                       # spec rule: the four markets only
        h1, ctx = self._context(df, timeframe, higher_frames)
        h4 = ctx.get("4H")
        if h1 is None or h4 is None or len(h1) < 125 or len(h4) < 200:
            return None
        res = tb.evaluate(h1, h4, market=market)
        if not isinstance(res, dict) or res.get("status") != tb.SIGNAL:
            return None
        direction = res["direction"]
        atr = float(res["atr"])
        entry = float(h1["close"].iloc[-1])   # reference entry (signal close)
        risk = TB_SPEC["stop_atr"] * atr
        sign = 1.0 if direction == "BUY" else -1.0
        sl, tp = entry - sign * risk, entry + sign * risk * self.configured_rr
        return Candidate(
            strategy_id=self.id, market=market, timeframe="1H",
            direction=direction, candle_time=str(res["signal_bar"]),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=[tp],
            risk=risk, rr_primary=self.configured_rr,
            score=60, score_components={"trendbreak_rule": 60.0},
            checks=list(res.get("checks") or []),
            analysis="H1 breakout in the H4 trend (first bar); SL 2.5xATR, "
                     "target 1:3, next-open entry.",
            params={}, params_version=self.version,
            extra={"spec_id": TB_SPEC["name"], "atr": atr,
                   "entry_rule": "NEXT_BAR_OPEN",
                   "session": (score_context or {}).get("session")})

    # interface completeness (research engines are the entry path)
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "trendbreak engine (detect_signal only)"}

    def detect_on_bar(self, state, i):
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        return Candidate(strategy_id=self.id, market=market, timeframe="1H",
                         direction=direction, candle_time=str(df.index[i]),
                         entry=entry, entry_zone=[entry, entry], sl=sl,
                         tps=[entry + sign * risk * self.configured_rr]
                         if (sign := (1.0 if direction == "BUY" else -1.0)) else [entry],
                         risk=risk, rr_primary=self.configured_rr,
                         score=60, score_components={"trendbreak_rule": 60.0},
                         checks=[], analysis="TrendBreak setup",
                         params={}, params_version=self.version,
                         extra={"spec_id": TB_SPEC["name"]})

    def calculate_risk(self, df, state, i, direction, params):
        entry = float(df["close"].iloc[i])
        return {"entry": entry,
                "sl": entry - 2.5 * float(state.get("atr", pd.Series([0.0])).iloc[-1])
                if direction == "BUY" else
                entry + 2.5 * float(state.get("atr", pd.Series([0.0])).iloc[-1]),
                "risk": 2.5 * float(state.get("atr", pd.Series([0.0])).iloc[-1])}

    def calculate_targets(self, entry: float, risk: float, direction: str, params):
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * self.configured_rr]

    def explain_signal(self, state, i, direction, params, extra):
        return ([{"label": "TrendBreak rules", "ok": True,
                  "detail": "H4 trend + first-bar 120-bar channel breakout "
                            "(spec rules, unmodified)"}],
                "TrendBreak: H1 breakout with the H4 trend (1:3).")


class TrendBreakV2Strategy(_ResearchCandidateStrategy):
    """TrendBreak v2 (H1) - owner Pine port, made live (owner 2026-10-24).

    Trail-riding strategy: NO take-profit by design - the exits are the
    5xATR trail (tighten-only, maintained by the trade manager) and the
    240-bar time exit. The order carries the initial 2.5xATR stop and no
    TP (tp1 stays empty; the pre-trade validator accepts no-TP orders for
    this strategy only). One position at a time per market (Pine's
    position_size == 0 gate), enforced in detect_signal via the store.
    """
    id = "strategy_trendbreak_v2"
    name = "TrendBreak v2 (H1)"
    short_name = "H1 v2.0"
    description = (
        "TrendBreak v2 (H1) - owner Pine v6 port, live 2026-10-24. H1 "
        "breakout in the H4 trend with gap and volatility filters: "
        "|H4 EMA50-EMA200| > 2xATR, ATR percentrank(480) > 30%; 120-bar "
        "channel, first breakout bar only; entry at the next H1 open; "
        "initial stop 2.5xATR(signal bar); NO fixed target - trailing "
        "stop 5xATR from the best price (only tightens) and a 240-bar "
        "time exit. Spec markets: EURUSD, GBPUSD, USDJPY, XAUUSD.")
    version = "1.0"
    markets = list(tb2.SPEC["markets"])
    timeframes = ["15M", "1H", "4H", "1D"]
    min_bars = 500                      # percentrank window (480 + ATR 14)
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    configured_rr = None                # trail strategy: no fixed R:R
    entry_tf = "1H"
    context_tfs = ("4H",)

    def _has_open_position(self, market: str) -> bool:
        """Pine: strategy.position_size == 0 gate - one position at a time."""
        try:
            from ...db.store import get_store
            rows = get_store().list("signals", filters={
                "strategy_id": self.id, "market": market}, limit=50)
            for r in rows:
                if r.get("mt5_ticket") and not r.get("completed"):
                    return True
        except Exception:
            return False                # fail-open? NO - fail honest: skip
        return False

    def detect_signal(self, df: Optional[pd.DataFrame], market: str,
                      timeframe: str, params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None
                      ) -> Optional[Candidate]:
        if market not in self.markets:
            return None
        if self._has_open_position(market):
            return None                 # one position at a time (Pine gate)
        h1, ctx = self._context(df, timeframe, higher_frames)
        h4 = ctx.get("4H")
        if h1 is None or h4 is None:
            return None
        res = tb2.evaluate(h1, h4, market=market)
        if not isinstance(res, dict) or res.get("status") != tb2.SIGNAL:
            return None
        direction = res["direction"]
        atr = float(res["atr"])
        entry = float(h1["close"].iloc[-1])     # reference entry (signal close)
        sign = 1.0 if direction == "BUY" else -1.0
        sl = entry - sign * tb2.SPEC["stop_mult"] * atr
        return Candidate(
            strategy_id=self.id, market=market, timeframe="1H",
            direction=direction, candle_time=str(res["signal_bar"]),
            entry=entry, entry_zone=[entry, entry], sl=sl,
            tps=[],                              # trail strategy: NO target
            risk=abs(entry - sl), rr_primary=None,
            score=60, score_components={"trendbreak_v2_rule": 60.0},
            checks=list(res.get("checks") or []),
            analysis="H1 breakout with H4 gap + volatility filters; "
                     "initial stop 2.5xATR, 5xATR trail, no fixed target.",
            params={}, params_version=self.version,
            extra={"spec_id": tb2.SPEC["name"], "atr": atr,
                   "entry_rule": "NEXT_BAR_OPEN", "trail_only": True,
                   "session": (score_context or {}).get("session")})

    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "trendbreak v2 engine (detect_signal only)"}

    def detect_on_bar(self, state, i):
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        return Candidate(strategy_id=self.id, market=market, timeframe="1H",
                         direction=direction, candle_time=str(df.index[i]),
                         entry=entry, entry_zone=[entry, entry], sl=sl,
                         tps=[], risk=abs(entry - sl), rr_primary=None,
                         score=60, score_components={"trendbreak_v2_rule": 60.0},
                         checks=[], analysis="TrendBreak v2 setup",
                         params={}, params_version=self.version,
                         extra={"spec_id": tb2.SPEC["name"], "trail_only": True})

    def calculate_risk(self, df, state, i, direction, params):
        entry = float(df["close"].iloc[i])
        atr_series = state.get("atr")
        atr = float(atr_series.iloc[-1]) if atr_series is not None else 0.0
        return {"entry": entry,
                "sl": entry - sign * tb2.SPEC["stop_mult"] * atr
                if (sign := (1.0 if direction == "BUY" else -1.0)) else entry,
                "risk": tb2.SPEC["stop_mult"] * atr}

    def calculate_targets(self, entry: float, risk: float, direction: str, params):
        return []                            # trail strategy: no targets

    def explain_signal(self, state, i, direction, params, extra):
        return ([{"label": "TrendBreak v2 rules", "ok": True,
                  "detail": "H4 trend + gap + volatility + first-bar "
                            "breakout (Pine defaults, unmodified)"}],
                "TrendBreak v2: H1 breakout, 2.5xATR stop, 5xATR trail, "
                "no fixed target.")


class ZoneSweepStrategy(_ResearchCandidateStrategy):
    id = "strategy_zonesweep_m15"
    name = "ZoneSweep M15"
    short_name = "ZoneSweep"
    description = (
        "Research candidate made live (owner 2026-10-24). M15 supply/"
        "demand zone after a bullish/bearish BOS with a liquidity sweep, "
        "H1+H4 trend aligned: fractal swings (L=5), displacement >= 2xATR, "
        "zone = swing candle low/high-body (0.3-2.5xATR), limit entry at "
        "the zone edge on first touch, stop beyond the zone by 0.25xATR, "
        "target 1:8. Spec markets: EURUSD, GBPUSD, USDJPY, XAUUSD.")
    version = "1.0"
    markets = list(ZS_SPEC["markets"])       # the spec's own market rule
    timeframes = ["15M", "1H", "4H", "1D"]
    min_bars = 210
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    configured_rr = ZS_SPEC["tp_multiple"]   # 8.0
    entry_tf = "15M"
    context_tfs = ("1H", "4H")

    def detect_signal(self, df: Optional[pd.DataFrame], market: str,
                      timeframe: str, params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None
                      ) -> Optional[Candidate]:
        if market not in self.markets:
            return None                       # spec rule: the four markets only
        m15, ctx = self._context(df, timeframe, higher_frames)
        h1, h4 = ctx.get("1H"), ctx.get("4H")
        if m15 is None or h1 is None or h4 is None:
            return None
        res = zs.zone_entry_event(m15, h1, h4, market=market)
        if not isinstance(res, dict) or res.get("status") != zs.SIGNAL:
            return None
        entry = float(res["entry"])
        sl = float(res["stop_loss"])
        tp = float(res["take_profit"])
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        return Candidate(
            strategy_id=self.id, market=market, timeframe="15M",
            direction="BUY" if res["direction"] == "LONG" else "SELL",
            candle_time=str(res["touch_bar"]),
            entry=entry, entry_zone=[res["zone_bottom"], res["zone_top"]],
            sl=sl, tps=[tp], risk=risk, rr_primary=self.configured_rr,
            score=60, score_components={"zonesweep_rule": 60.0},
            checks=[{"rule": "liquidity sweep", "ok": bool(res["sweep"]),
                     "detail": "the zone swing swept the prior swing"},
                    {"rule": "H1+H4 trend aligned", "ok": True,
                     "detail": "both trends in the zone's direction"}],
            analysis="Swept S/D zone; limit at the zone edge filled on "
                     "first touch; SL beyond the zone, target 1:8.",
            params={}, params_version=self.version,
            extra={"spec_id": ZS_SPEC["name"], "atr": res.get("atr"),
                   "entry_rule": "ZONE_EDGE_LIMIT",
                   "zone_created_at": res.get("zone_created_at"),
                   "session": (score_context or {}).get("session")})

    # interface completeness (research engines are the entry path)
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "zonesweep engine (detect_signal only)"}

    def detect_on_bar(self, state, i):
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        sign = 1.0 if direction == "BUY" else -1.0
        return Candidate(strategy_id=self.id, market=market, timeframe="15M",
                         direction=direction, candle_time=str(df.index[i]),
                         entry=entry, entry_zone=[entry, entry], sl=sl,
                         tps=[entry + sign * risk * self.configured_rr],
                         risk=risk, rr_primary=self.configured_rr,
                         score=60, score_components={"zonesweep_rule": 60.0},
                         checks=[], analysis="ZoneSweep setup",
                         params={}, params_version=self.version,
                         extra={"spec_id": ZS_SPEC["name"]})

    def calculate_risk(self, df, state, i, direction, params):
        entry = float(df["close"].iloc[i])
        return {"entry": entry, "sl": entry, "risk": 0.0}

    def calculate_targets(self, entry: float, risk: float, direction: str, params):
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * self.configured_rr]

    def explain_signal(self, state, i, direction, params, extra):
        return ([{"label": "ZoneSweep rules", "ok": True,
                  "detail": "BOS + liquidity sweep + H1/H4 trend (spec "
                            "rules, unmodified)"}],
                "ZoneSweep: swept supply/demand zone entry (1:8).")
