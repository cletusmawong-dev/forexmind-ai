"""Liquidity Sweep + Rejection Strategy.

15min mean-reversion after a sweep of recent high/low with strong rejection candle.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseStrategy, Candidate


class LiquiditySweep(BaseStrategy):
    id = "strategy_liquidity_sweep"
    name = "Liquidity Sweep"
    short_name = "LSWEEP"
    description = (
        "Liquidity sweep + rejection. On 15min, looks for a sweep of the "
        "recent high or low followed by a strong rejection candle. "
        "Stop beyond the sweep extreme, 1:2 RR target."
    )
    version = "1.0"
    markets = ["EURUSD", "GBPUSD", "XAUUSD"]
    timeframes = ["15M"]
    min_bars = 200

    base_params: Dict[str, Any] = {
        "lookback": 12,
        "atr_period": 14,
        "min_body_ratio": 0.55,
        "sweep_atr_min": 0.15,
        "stop_atr_buffer": 0.3,
        "rr": 2.0,
        "session_start_1": 7,
        "session_end_1": 11,
        "session_start_2": 13,
        "session_end_2": 17,
    }
    experiment_variables: Dict[str, Any] = {
        "rr": {"type": "float", "min": 1.5, "max": 3.0},
        "min_body_ratio": {"type": "float", "min": 0.4, "max": 0.7},
        "lookback": {"type": "int", "min": 8, "max": 24},
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
        lookback = int(self.base_params["lookback"])
        if i < lookback:
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

        window_high = state["high"].iloc[i-lookback:i].max()
        window_low = state["low"].iloc[i-lookback:i].min()

        body = abs(row_close - row_open)
        rng = row_high - row_low
        if rng <= 0:
            return None
        br = body / rng
        min_br = float(self.base_params["min_body_ratio"])

        # SELL sweep
        if (row_high > window_high and row_close < window_high and
            row_close < row_open and br >= min_br):
            return {"direction": "SELL", "i": i, "sweep_extreme": row_high}
        # BUY sweep
        if (row_low < window_low and row_close > window_low and
            row_close > row_open and br >= min_br):
            return {"direction": "BUY", "i": i, "sweep_extreme": row_low}
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        direction = event.get("direction")
        extreme = event.get("sweep_extreme")
        if not direction or extreme is None:
            return None

        rk = self.calculate_risk(df, state, i, direction, params, extreme)
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
            score=50,
            score_components={"liquidity_sweep": 50.0},
            checks=checks,
            analysis=analysis,
            params={},
            params_version=self.version,
            extra={"sweep_extreme": extreme},
            metadata={"source": "liquidity sweep 15m"},
        )

    def calculate_risk(self, df, state, i, direction, params, extreme=None) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        atr = float(state["atr"].iloc[i])
        buffer = float(params.get("stop_atr_buffer", self.base_params["stop_atr_buffer"]))
        if extreme is None:
            extreme = float(df["high"].iloc[i]) if direction == "SELL" else float(df["low"].iloc[i])
        if direction == "SELL":
            sl = float(extreme) + buffer * atr
        else:
            sl = float(extreme) - buffer * atr
        risk = abs(entry - sl)
        return {"entry": entry, "sl": sl, "risk": risk}

    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params: Dict[str, Any]) -> List[float]:
        rr = float(params.get("rr", self.base_params["rr"]))
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * rr]

    def explain_signal(self, state, i, direction, params, extra):
        checks = [{
            "label": "Sweep of recent high/low + rejection",
            "ok": True,
            "detail": f"{direction} after liquidity sweep",
        }]
        return checks, (
            f"{direction} liquidity sweep rejection on 15min. "
            f"Stop beyond sweep extreme + ATR buffer, target {params.get('rr', 2.0)}R."
        )

    def tp_rrs(self, params) -> List[float]:
        return [float(params.get("rr", self.base_params["rr"]))]
