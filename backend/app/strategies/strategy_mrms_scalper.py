"""MRMS Scalper Strategy (Mean Reversion Micro Scalper).

Fast mean-reversion scalper on 5-minute timeframe.
Enters when price is stretched from EMA8 by ATR and shows rejection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseStrategy, Candidate


class MRMSScalper(BaseStrategy):
    id = "strategy_mrms_scalper"
    name = "MRMS Scalper"
    short_name = "MRMS"
    description = (
        "Mean-reversion micro scalper. Enters on 5min when price is "
        "stretched >= 1.05 ATR from EMA8 and prints a rejection candle. "
        "Targets ~1.35R with tight ATR stop."
    )
    version = "1.0"
    markets = ["EURUSD", "GBPUSD", "XAUUSD"]
    timeframes = ["5M"]
    min_bars = 200

    base_params: Dict[str, Any] = {
        "ema_period": 8,
        "atr_period": 14,
        "stretch_atr": 1.05,
        "stop_atr": 0.85,
        "tp_atr": 1.15,
        "min_body_ratio": 0.48,
        "session_start_1": 7,
        "session_end_1": 11,
        "session_start_2": 13,
        "session_end_2": 17,
    }
    experiment_variables: Dict[str, Any] = {
        "stretch_atr": {"type": "float", "min": 0.8, "max": 1.5},
        "stop_atr": {"type": "float", "min": 0.6, "max": 1.2},
        "tp_atr": {"type": "float", "min": 0.8, "max": 1.8},
        "min_body_ratio": {"type": "float", "min": 0.35, "max": 0.7},
    }

    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema = close.ewm(span=int(params["ema_period"]), adjust=False).mean()
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(int(params["atr_period"])).mean()
        return {
            "ema": ema,
            "atr": atr,
            "close": close,
            "open": df["open"],
            "high": high,
            "low": low,
        }

    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        if i < 30:
            return None
        row_close = state["close"].iloc[i]
        row_open = state["open"].iloc[i]
        row_high = state["high"].iloc[i]
        row_low = state["low"].iloc[i]
        ema = state["ema"].iloc[i]
        atr = state["atr"].iloc[i]
        if pd.isna(atr) or atr <= 0:
            return None

        ts = state["close"].index[i]
        hr = ts.hour
        in_session = (
            (self.base_params["session_start_1"] <= hr <= self.base_params["session_end_1"]) or
            (self.base_params["session_start_2"] <= hr <= self.base_params["session_end_2"])
        )
        if not in_session:
            return None

        dist = row_close - ema
        body = abs(row_close - row_open)
        rng = row_high - row_low
        if rng <= 0:
            return None
        br = body / rng
        min_br = float(self.base_params["min_body_ratio"])
        stretch = float(self.base_params["stretch_atr"]) * atr

        if dist <= -stretch and row_close > row_open and br >= min_br:
            return {"direction": "BUY", "i": i}
        if dist >= stretch and row_close < row_open and br >= min_br:
            return {"direction": "SELL", "i": i}
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        # standard assembly from the platform template (integration plumbing
        # - the entry/risk/target RULES above are the owner's, untouched)
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, params)
        if rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, params)
        checks, analysis = self.explain_signal(state, i, direction, params, {})
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=direction, candle_time=str(df.index[i]),
            entry=rk["entry"], entry_zone=[rk["entry"], rk["entry"]],
            sl=rk["sl"], tps=tps, risk=rk["risk"],
            rr_primary=(abs(tps[0] - rk["entry"]) / rk["risk"]) if tps else 0.0,
            score=50, score_components={"mrms_mean_reversion": 50.0},
            checks=checks, analysis=analysis, params={},
            params_version=self.version, extra={},
            metadata={"source": "MRMS scalper 5m (owner 2026-11-02)"})

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        atr = float(state["atr"].iloc[i])
        stop_mult = float(params.get("stop_atr", self.base_params["stop_atr"]))
        if direction == "BUY":
            sl = entry - stop_mult * atr
        else:
            sl = entry + stop_mult * atr
        risk = abs(entry - sl)
        return {"entry": entry, "sl": sl, "risk": risk}

    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params: Dict[str, Any]) -> List[float]:
        tp_mult = float(params.get("tp_atr", self.base_params["tp_atr"]))
        stop_mult = float(params.get("stop_atr", self.base_params["stop_atr"]))
        rr = tp_mult / stop_mult if stop_mult > 0 else 1.3
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * rr]

    def explain_signal(self, state, i, direction, params, extra):
        atr = state["atr"].iloc[i]
        ema = state["ema"].iloc[i]
        close = state["close"].iloc[i]
        checks = [{
            "label": "Stretched from EMA8",
            "ok": True,
            "detail": f"close={close:.5g} ema={ema:.5g} atr={atr:.5g}",
        }]
        return checks, (
            f"{direction} mean-reversion on 5min. "
            f"Stretched from EMA8, rejection candle, "
            f"stop {params.get('stop_atr', 0.85)}xATR."
        )

    def tp_rrs(self, params) -> List[float]:
        tp = float(params.get("tp_atr", self.base_params["tp_atr"]))
        stop = float(params.get("stop_atr", self.base_params["stop_atr"]))
        return [tp / stop if stop > 0 else 1.35]
