"""Shared plumbing that turns the four SPEC-FIXED XAUUSD rule engines
(app.research.fx_engines) into LIVE registry strategies.

Owner directive 2026-10-10: "don't put them research - put the old ones to
sleep and let the new ones take over." The engines were already live as
research candidates; this adapter family registers the SAME deterministic
rules as real strategies so they:

  * appear on the Strategies screen (owner can see/pause each one),
  * are scanned by the EXISTING signal engine on every candle boundary,
  * create regular signals and flow through the EXISTING execution core
    (mode gates, risk walls, MT5 bridge, journal, TP tracking) - none of
    which is modified here.

Rules are SPEC-FIXED: no tunable parameters, no experiment variables, no
invented variants (owner spec: "do not invent, rewrite, optimize or
simplify"). Every adapter evaluates ONLY its own base timeframe frame,
wherever the scheduler happened to find it:

  * scanned tf == base tf        -> use the passed df
  * otherwise                    -> use higher_frames[base_tf]

The Candidate carries the BASE timeframe (not the scanned one), so the
signal store's (user, strategy, market, timeframe, candle_time) dedupe
collapses the same setup however many scan-timeframes saw it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy, Candidate

_DIRECTION = {"LONG": "BUY", "SHORT": "SELL"}


class FxSpecStrategy(BaseStrategy):
    """Base class for one spec-fixed FX engine (subclass sets `engine`)."""

    id = ""
    name = ""
    short_name = ""
    description = ""
    version = "1.0"                    # matches the engine spec version
    markets = ["XAUUSD"]
    # All scheduler-valid scan timeframes: the engine itself reads its base
    # frame, so it is evaluated exactly once per scan tick whatever the
    # user's signal_timeframes setting is (5M is never scanned by the
    # scheduler - frames for it always come from higher_frames).
    timeframes = ["15M", "1H", "4H", "1D"]
    min_bars = 3
    base_tf = ""                       # e.g. "1H"
    extra_tfs: Dict[str, str] = {}     # role -> timeframe (e.g. {"h4": "4H"})

    # The rules are fixed by the specification - deliberately no tunables.
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}

    # Subclasses: the engine function from app.research.fx_engines
    def _evaluate(self, frames: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        raise NotImplementedError

    # ---------------------------------------------------------------- resolve
    def _frames(self, df: pd.DataFrame, timeframe: str,
                higher_frames: Optional[Dict[str, pd.DataFrame]]) -> Dict[str, pd.DataFrame]:
        higher = higher_frames or {}
        frames: Dict[str, pd.DataFrame] = {"base": df if timeframe == self.base_tf
                                           else higher.get(self.base_tf)}
        for role, tf in self.extra_tfs.items():
            frames[role] = df if timeframe == tf else higher.get(tf)
        return frames

    # ---------------------------------------------------------------- detection
    def detect_signal(self, df: pd.DataFrame, market: str, timeframe: str,
                      params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None,
                      lookback: int = 3) -> Optional[Candidate]:
        if market not in self.markets or timeframe not in self.timeframes:
            return None
        if df is None and not (higher_frames or {}):
            return None
        frames = self._frames(df, timeframe, higher_frames)
        res = self._evaluate(frames)
        # RESEARCH_CANDIDATE is the only actionable status: NO_SIGNAL and
        # INSUFFICIENT_DATA mean "nothing to say", SKIP_RISK means the spec's
        # fail-closed sizing rejected the setup - none of them may trade.
        if not isinstance(res, dict) or res.get("status") != "RESEARCH_CANDIDATE":
            return None
        direction = _DIRECTION.get(res.get("direction"))
        entry, sl, tp = res.get("entry"), res.get("sl"), res.get("tp")
        if direction is None or entry is None or sl is None or tp is None:
            return None
        entry, sl, tp = float(entry), float(sl), float(tp)
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        base_df = frames.get("base")
        candle_time = str(base_df.index[-1]) if base_df is not None \
            else str(res.get("signal_time") or "")
        return Candidate(
            strategy_id=self.id, market=market, timeframe=self.base_tf,
            direction=direction, candle_time=candle_time,
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=[tp],
            risk=risk, rr_primary=float(res.get("r_multiple") or 0.0),
            score=60, score_components={"spec_rule": 60.0},
            checks=[{"label": "rules", "ok": True, "detail": str(res.get("reason", ""))}],
            analysis=str(res.get("reason", "spec rule setup")),
            params={}, params_version=str(res.get("version", self.version)),
            extra={"spec_id": res.get("id"), "spec_version": res.get("version"),
                   "r_multiple": res.get("r_multiple"),
                   "break_even_marker_r": res.get("break_even_marker_r"),
                   "spec_signal_time": res.get("signal_time"),
                   "research_status": res.get("status"),
                   "session": (score_context or {}).get("session")})

    # ---------------------------------------------------------------- interface
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        from ...core.indicators import atr
        try:
            return {"atr": atr(df, 14)}
        except Exception:
            return {"atr": None}

    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        # Entries are evaluated by detect_signal only (session windows span
        # multiple bars). Backtests therefore honestly return no trades
        # instead of a lookalike approximation of the spec.
        return None

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        lo = float(df["low"].iloc[i])
        hi = float(df["high"].iloc[i])
        sl = lo if direction == "BUY" else hi
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params) -> list:
        # Spec: exactly ONE target per strategy (1R / 1.25R / 1.5R depending
        # on the engine - the engine's tp is authoritative; this is only the
        # interface-completeness fallback).
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk]

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        """Interface completeness (detect_signal is the real entry path)."""
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        sign = 1.0 if direction == "BUY" else -1.0
        tps = [float(event.get("tp", entry + sign * risk))]
        return Candidate(
            strategy_id=self.id, market=market, timeframe=self.base_tf,
            direction=direction, candle_time=str(df.index[i]),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=tps,
            risk=risk, rr_primary=abs(tps[0] - entry) / risk,
            score=60, score_components={"spec_rule": 60.0}, checks=[],
            analysis="spec rule setup", params={},
            extra={"spec_id": self.id.replace("strategy_3_", "") + "_v1"})

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        checks = [{"label": "spec rules", "ok": True,
                   "detail": f"{extra.get('spec_id')} v{extra.get('spec_version')} "
                             f"all conditions met"}]
        return checks, (f"{self.short_name}: deterministic specification rules "
                        f"met on {self.base_tf} closed candles "
                        f"(R multiple {extra.get('r_multiple')}).")
