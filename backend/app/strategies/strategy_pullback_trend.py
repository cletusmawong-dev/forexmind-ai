"""Pullback in Trend Strategy.

H1 trend bias + 15min pullback to H1 EMA50 with rejection confirmation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseStrategy, Candidate


class PullbackInTrend(BaseStrategy):
    id = "strategy_pullback_trend"
    name = "Pullback in Trend"
    short_name = "PBT"
    description = (
        "Higher-timeframe trend pullback. Uses H1 EMA50/EMA200 for bias, "
        "enters on 15min when price pulls back to H1 EMA50 and prints a "
        "rejection candle in the trend direction. 1:2 RR target."
    )
    version = "1.0"
    markets = ["EURUSD", "GBPUSD", "XAUUSD"]
    timeframes = ["15M"]
    min_bars = 300

    base_params: Dict[str, Any] = {
        "ema_fast_h1": 50,
        "ema_slow_h1": 200,
        "atr_period": 14,
        "pullback_buffer": 0.002,
        "stop_atr_extra": 0.4,
        "rr": 2.0,
        "min_body_ratio": 0.55,
        "session_start_1": 7,
        "session_end_1": 11,
        "session_start_2": 13,
        "session_end_2": 17,
    }
    experiment_variables: Dict[str, Any] = {
        "rr": {"type": "float", "min": 1.5, "max": 3.0},
        "min_body_ratio": {"type": "float", "min": 0.4, "max": 0.7},
        "stop_atr_extra": {"type": "float", "min": 0.2, "max": 0.8},
    }

    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(int(params["atr_period"])).mean()
        return {
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

        body = abs(row_close - row_open)
        rng = row_high - row_low
        if rng <= 0:
            return None
        br = body / rng
        min_br = float(self.base_params["min_body_ratio"])

        if row_close > row_open and br >= min_br:
            return {"direction": "BUY", "i": i, "potential": True}
        if row_close < row_open and br >= min_br:
            return {"direction": "SELL", "i": i, "potential": True}
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        direction = event.get("direction")
        if not direction:
            return None

        h1_df = higher_frames.get("1H") if higher_frames else None
        if h1_df is None or len(h1_df) < 200:
            return None

        h1_close = h1_df["close"]
        ema50 = h1_close.ewm(span=int(params.get("ema_fast_h1", 50)), adjust=False).mean()
        ema200 = h1_close.ewm(span=int(params.get("ema_slow_h1", 200)), adjust=False).mean()
        last_ema50 = float(ema50.iloc[-1])
        last_ema200 = float(ema200.iloc[-1])
        last_close = float(h1_close.iloc[-1])

        if direction == "BUY":
            if not (last_ema50 > last_ema200 and last_close > last_ema50):
                return None
            if float(df["low"].iloc[i]) > last_ema50 * (1 + float(params.get("pullback_buffer", 0.002))):
                return None
        else:
            if not (last_ema50 < last_ema200 and last_close < last_ema50):
                return None
            if float(df["high"].iloc[i]) < last_ema50 * (1 - float(params.get("pullback_buffer", 0.002))):
                return None

        rk = self.calculate_risk(df, state, i, direction, params)
        if rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, params)
        checks, analysis = self.explain_signal(state, i, direction, params, {})
        return Candidate(
            strategy_id=self.id,
            market=market,
            timeframe=timeframe,
            direction=direction,
            candle_time=str(df.index[i]),
            entry=rk["entry"],
            entry_zone=[rk["entry"], rk["entry"]],
            sl=rk["sl"],
            tps=tps,
            risk=rk["risk"],
            rr_primary=(abs(tps[0] - rk["entry"]) / rk["risk"]) if tps else 0.0,
            score=55,
            score_components={"trend_pullback": 55.0},
            checks=checks,
            analysis=analysis,
            params={},
            params_version=self.version,
            extra={},
            metadata={"source": "pullback in trend 15m"},
        )

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        atr = float(state["atr"].iloc[i])
        extra = float(params.get("stop_atr_extra", self.base_params["stop_atr_extra"]))
        if direction == "BUY":
            sl = float(df["low"].iloc[i]) - extra * atr
        else:
            sl = float(df["high"].iloc[i]) + extra * atr
        risk = abs(entry - sl)
        return {"entry": entry, "sl": sl, "risk": risk}

    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params: Dict[str, Any]) -> List[float]:
        rr = float(params.get("rr", self.base_params["rr"]))
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * rr]

    def explain_signal(self, state, i, direction, params, extra):
        checks = [{
            "label": "H1 trend + 15min pullback rejection",
            "ok": True,
            "detail": f"{direction} pullback to H1 EMA50",
        }]
        return checks, (
            f"{direction} pullback in H1 trend on 15min. "
            f"Rejection candle, stop beyond swing + ATR buffer, target {params.get('rr', 2.0)}R."
        )

    def tp_rrs(self, params) -> List[float]:
        return [float(params.get("rr", self.base_params["rr"]))]
