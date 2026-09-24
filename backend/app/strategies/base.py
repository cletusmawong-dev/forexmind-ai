"""Strategy interface (SPEC §47) + shared simulation utilities.

Every strategy supports:
    get_metadata() get_parameters() detect_signal() calculate_risk()
    calculate_targets() explain_signal() backtest() run_experiment()

A future strategy is added as a new module registered in strategies/__init__.py
and automatically appears in Strategy Manager, Signals, Analytics, Journal and
the Learning Lab (SPEC §48). The AI may NEVER invent strategies (SPEC §12, §57).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ..core.indicators import TF_RULES


@dataclass
class Candidate:
    """A validated setup produced by a strategy on the last closed bar."""
    strategy_id: str
    market: str
    timeframe: str
    direction: str            # BUY | SELL
    candle_time: str
    entry: float
    entry_zone: List[float]
    sl: float
    tps: List[float]
    risk: float
    rr_primary: float
    score: int
    score_components: Dict[str, float]
    checks: List[Dict[str, Any]]
    analysis: str
    mtf: Optional[Dict[str, int]] = None
    params: Dict[str, Any] = field(default_factory=dict)
    params_version: str = "1.0"
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Common strategy contract. One module per strategy (SPEC §8)."""

    id: str = ""
    name: str = ""
    short_name: str = ""
    description: str = ""
    version: str = "1.0"
    base_params: Dict[str, Any] = {}
    experiment_variables: Dict[str, Dict[str, Any]] = {}
    markets: List[str] = []
    timeframes: List[str] = ["5M", "15M", "1H", "4H", "1D"]
    min_bars: int = 300

    # ---------------------------------------------------------------- metadata
    def get_metadata(self) -> dict:
        return {
            "id": self.id, "name": self.name, "short_name": self.short_name,
            "description": self.description, "version": self.version,
            "markets": self.markets, "timeframes": self.timeframes,
            "experiment_variables": self.experiment_variables,
        }

    def get_parameters(self, params: Optional[Dict[str, Any]] = None) -> dict:
        return dict(self.base_params if params is None else params)

    # ---------------------------------------------------------------- detection
    @abstractmethod
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        """Vectorised indicator state for a candle frame (no lookahead)."""

    @abstractmethod
    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        """Raw entry event on bar i (i is a fully closed bar). Returns
        {direction, i} or None. These are the SMALL ARROW entries (SPEC §9)."""

    def detect_signal(self, df: pd.DataFrame, market: str, timeframe: str,
                      params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None,
                      lookback: int = 3) -> Optional[Candidate]:
        """Evaluate the last bars (newest first) and build a full Candidate.

        LOOKBACK (missed-signal fix, 2026-09-24): the candle cache can lag
        1-2 bars behind wall-clock at scan time, so an entry that fired on
        the second-newest bar was never seen (crossovers are single-bar
        events - the replay audit showed ~2 of 3 cluster entries lost).
        We now walk back a few CLOSED bars; _create_signal dedupes on
        (user, strategy, market, tf, candle_time) so a bar can never
        produce a second signal. Bounded staleness: at most `lookback`
        bars, never an ancient resurrection after long outages."""
        params = self.get_parameters(params)
        if df is None or len(df) < self.min_bars:
            return None
        df = df.dropna()
        state = self.compute(df, params)
        newest = len(df) - 1
        for i in range(newest, max(newest - max(1, lookback), 0), -1):
            event = self.detect_on_bar(state, i)
            if event:
                cand = self.build_candidate(state, i, event, df, market, timeframe, params,
                                            higher_frames or {}, score_context or {})
                if cand is not None:
                    return cand
        return None

    @abstractmethod
    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        """calculate_risk + calculate_targets + explain_signal assembled."""

    # ------------------------------------------------------- risk & targets
    @abstractmethod
    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        """Returns {entry, sl, risk}."""

    @abstractmethod
    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params: Dict[str, Any]) -> List[float]:
        """TP ladder in price terms."""

    # ------------------------------------------------------- explanation
    @abstractmethod
    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        """Returns (checks: List[{label, ok, detail}], analysis: str).
        Concise reasoning and observable conditions ONLY - no chain of
        thought, no guarantees (SPEC §7, §56, §57)."""

    # ------------------------------------------------------- backtesting
    def backtest(self, df: pd.DataFrame, params: Optional[Dict[str, Any]] = None,
                 max_hold_bars: int = 200) -> List[Dict[str, Any]]:
        """Deterministic walk-forward simulation on REAL candles.

        Model (documented, identical for original and experimental versions):
          * one open position per strategy/market/timeframe
          * conservative intrabar rule: if SL and a TP are touched by the same
            candle, SL is assumed first
          * TPs bank in sequence: finishing at TP1 banks tp1_rr, at TP2 banks
            tp2_rr, at TP3 banks tp3_rr; SL before any TP = -1R
          * positions untouched after max_hold_bars expire at 0R (EXPIRED)
        """
        params = self.get_parameters(params)
        if df is None or len(df) < self.min_bars:
            return []
        df = df.dropna()
        state = self.compute(df, params)
        trades: List[Dict[str, Any]] = []
        n = len(df)
        open_pos: Optional[Dict[str, Any]] = None
        c = df["close"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy()
        t = df.index
        for i in range(self.min_bars // 2, n):
            if open_pos is not None:
                res = self._advance_position(open_pos, i, h[i], l[i], t[i], params, max_hold_bars)
                if res is not None:
                    trades.append(res)
                    open_pos = None
                continue
            event = self.detect_on_bar(state, i)
            if event:
                rk = self.calculate_risk(df, state, i, event["direction"], params)
                if rk["risk"] <= 0:
                    continue
                tps = self.calculate_targets(rk["entry"], rk["risk"], event["direction"], params)
                open_pos = {
                    "direction": event["direction"], "entry_i": i,
                    "entry": rk["entry"], "sl": rk["sl"], "tps": tps,
                    "risk": rk["risk"], "entry_time": str(t[i]),
                    "tp_rrs": self.tp_rrs(params),
                }
        return trades

    def tp_rrs(self, params) -> List[float]:
        raise NotImplementedError

    def _advance_position(self, pos, i, hi, lo, ts, params, max_hold):
        long = pos["direction"] == "BUY"
        sl_hit = (lo <= pos["sl"]) if long else (hi >= pos["sl"])
        # conservative: SL evaluated first
        if sl_hit:
            banked = pos.get("tp_hits", 0)
            r = pos["tp_rrs"][banked - 1] if banked else -1.0
            return self._close(pos, i, ts, pos["sl"], r, "SL_HIT", banked)
        for k, tp in enumerate(pos["tps"]):
            if k < pos.get("tp_hits", 0):
                continue
            touched = (hi >= tp) if long else (lo <= tp)
            if touched:
                pos["tp_hits"] = k + 1
                if k + 1 == len(pos["tps"]):
                    return self._close(pos, i, ts, tp, pos["tp_rrs"][k], f"TP{k+1}_HIT", k + 1)
        if i - pos["entry_i"] >= max_hold:
            return self._close(pos, i, ts, None, 0.0, "EXPIRED", pos.get("tp_hits", 0))
        return None

    def _close(self, pos, i, ts, exit_px, r, status, tp_hits) -> dict:
        outcome = "WIN" if r > 0 else ("LOSS" if r < 0 else "EXPIRED")
        return {
            "market": "", "timeframe": "", "strategy_id": self.id,
            "direction": pos["direction"], "entry": pos["entry"], "sl": pos["sl"],
            "tps": pos["tps"], "risk": pos["risk"], "entry_time": pos["entry_time"],
            "exit_time": str(ts), "exit_price": exit_px, "r_multiple": r,
            "status": status, "tp_hits": tp_hits, "outcome": outcome,
            "duration_bars": i - pos["entry_i"],
        }

    # ------------------------------------------------------- experiment
    def run_experiment(self, df: pd.DataFrame, base_params: Dict[str, Any],
                       new_params: Dict[str, Any], max_hold_bars: int = 200):
        """Same dataset, only declared params differ (SPEC §39). The engine
        validates the ONE-VARIABLE rule before calling this."""
        return {
            "original": self.backtest(df, base_params, max_hold_bars),
            "experimental": self.backtest(df, new_params, max_hold_bars),
        }

    # ------------------------------------------------------- helpers
    def _validate_param(self, key: str, value: Any) -> Any:
        spec = self.experiment_variables.get(key)
        if not spec:
            raise ValueError(f"Unknown experiment variable '{key}' for {self.id}")
        if spec.get("type") == "int":
            value = int(value)
        else:
            value = float(value)
        if "min" in spec:
            value = max(spec["min"], value)
        if "max" in spec:
            value = min(spec["max"], value)
        return value

    def _volatility_rank(self, state) -> pd.Series:
        """Causal (expanding) ATR percentile rank - no lookahead in backtests."""
        atr = state.get("atr")
        if atr is None:
            return pd.Series(0.5, index=range(0))
        return atr.expanding(min_periods=50).rank(pct=True)
