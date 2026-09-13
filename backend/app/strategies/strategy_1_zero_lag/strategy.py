"""Strategy 1 - AlgoAlpha Zero Lag Trend Signals (MTF)

Source-of-truth translation of the supplied Pine Script (SPEC §9). The logic
is NOT reinterpreted:

    length = 70, band multiplier = 1.2
    lag    = floor((length - 1) / 2)
    zlema  = EMA(src + (src - src[lag]), length)
    volatility = highest(ATR(length), length) * 3 * bandMultiplier
    trend  = 1 when close crosses ABOVE zlema + volatility
             -1 when close crosses BELOW zlema - volatility (else carries)

    BULLISH ENTRY: close crosses above zlema  AND trend == 1  AND trend[1] == 1
    BEARISH ENTRY: close crosses below zlema  AND trend == -1 AND trend[1] == -1

These are the SMALL ARROW entries - not the larger trend-change signals.
MTF trend is read across 5M / 15M / 1H / 4H / 1D and displayed.

Exit parameters (declared, versioned defaults - the Pine source defines the
entry only): SL at the opposite volatility band; TP1 = 1.5R, TP2 = 2.5R
(matching the 1:2.5 reference in the specification).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ...core.indicators import atr, crossover, crossunder, ema, highest, trend_series
from ..base import BaseStrategy, Candidate

TREND_LABEL = {1: "Bullish", -1: "Bearish", 0: "Neutral"}


class ZeroLagStrategy(BaseStrategy):
    id = "strategy_1_zero_lag"
    name = "AlgoAlpha Zero Lag Trend Signals (MTF)"
    short_name = "Zero Lag Trend"
    description = ("Zero-lag EMA with ATR volatility bands. Small-arrow entries: "
                   "close crossing the Zero-Lag EMA in the direction of an "
                   "established trend, multi-timeframe trend displayed.")
    version = "1.0"
    base_params = {
        "length": 70,
        "band_mult": 1.2,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "expire_bars": 200,
    }
    experiment_variables = {
        "length":    {"type": "int",   "min": 20,  "max": 200, "step": 5,
                      "description": "Zero-lag EMA length"},
        "band_mult": {"type": "float", "min": 0.5, "max": 3.0, "step": 0.1,
                      "description": "Volatility band multiplier"},
        "tp1_rr":    {"type": "float", "min": 0.5, "max": 5.0, "step": 0.25,
                      "description": "TP1 risk-reward"},
        "tp2_rr":    {"type": "float", "min": 0.5, "max": 5.0, "step": 0.25,
                      "description": "TP2 risk-reward"},
    }
    markets = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["5M", "15M", "1H", "4H", "1D"]
    min_bars = 260

    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        length = int(params["length"]); mult = float(params["band_mult"])
        src = df["close"]
        lag = (length - 1) // 2
        delagged = src + (src - src.shift(lag))
        zlema = ema(delagged, length)
        _atr = atr(df, length)
        vol = highest(_atr, length) * 3.0 * mult
        upper = zlema + vol
        lower = zlema - vol
        trend = trend_series(src, upper, lower)
        cross_up = crossover(src, zlema)
        cross_dn = crossunder(src, zlema)
        prev_trend = trend.shift(1).fillna(0).astype(int)
        bull = cross_up & (trend == 1) & (prev_trend == 1)
        bear = cross_dn & (trend == -1) & (prev_trend == -1)
        return {
            "zlema": zlema, "upper": upper, "lower": lower, "vol": vol,
            "atr": _atr, "trend": trend, "prev_trend": prev_trend,
            "bull": bull, "bear": bear, "params": params,
        }

    def detect_on_bar(self, state, i: int) -> Optional[Dict[str, Any]]:
        if i < 2:
            return None
        if bool(state["bull"].iloc[i]):
            return {"direction": "BUY", "i": i}
        if bool(state["bear"].iloc[i]):
            return {"direction": "SELL", "i": i}
        return None

    # ------------------------------------------------------------------
    def mtf_trend(self, higher_frames: Dict[str, pd.DataFrame],
                  params: Dict[str, Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for tf, df in higher_frames.items():
            try:
                if df is None or len(df) < self.min_bars:
                    out[tf] = 0
                    continue
                st = self.compute(df.dropna(), params)
                out[tf] = int(st["trend"].iloc[-1])
            except Exception:
                out[tf] = 0
        return out

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        close = float(df["close"].iloc[i])
        zl = float(state["zlema"].iloc[i]); vol = float(state["vol"].iloc[i])
        if direction == "BUY":
            sl = float(state["lower"].iloc[i])
            risk = close - sl
            zone = [close - 0.03 * risk, close + 0.03 * risk] if risk > 0 else [close, close]
        else:
            sl = float(state["upper"].iloc[i])
            risk = sl - close
            zone = [close - 0.03 * risk, close + 0.03 * risk] if risk > 0 else [close, close]
        return {"entry": close, "sl": sl, "risk": risk, "entry_zone": zone}

    def tp_rrs(self, params) -> List[float]:
        return [float(params["tp1_rr"]), float(params["tp2_rr"])]

    def calculate_targets(self, entry, risk, direction, params) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float(params["tp1_rr"]),
                entry + sign * risk * float(params["tp2_rr"])]

    # ------------------------------------------------------------------
    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, params)
        if rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, params)
        rr_primary = float(params["tp2_rr"])
        mtf = self.mtf_trend(higher_frames, params)
        checks, analysis = self.explain_signal(
            state, i, direction, params,
            {"mtf": mtf, "rr_primary": rr_primary, "risk": rk["risk"]})
        score_context = dict(score_context or {})
        score_context.setdefault("close", float(df["close"].iloc[i]))
        score, comps = self.score(state, i, direction, mtf, rr_primary, score_context)
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=direction, candle_time=str(df.index[i]),
            entry=rk["entry"], entry_zone=rk.get("entry_zone", [rk["entry"], rk["entry"]]),
            sl=rk["sl"], tps=tps, risk=rk["risk"], rr_primary=rr_primary,
            score=score, score_components=comps, checks=checks, analysis=analysis,
            mtf=mtf, params=dict(params), params_version=self.version,
        )

    def score(self, state, i, direction, mtf, rr_primary, ctx) -> tuple:
        sgn = 1 if direction == "BUY" else -1
        mtf_agree = sum(1 for v in (mtf or {}).values() if v == sgn)
        mtf_neutral = sum(1 for v in (mtf or {}).values() if v == 0)
        n_tf = max(len(mtf) if mtf else 5, 1)
        c1 = round(40.0 * (mtf_agree + 0.5 * mtf_neutral) / n_tf, 1)

        close = float(state["zlema"].index[i] and 0)  # placeholder, replaced below
        zl = float(state["zlema"].iloc[i]); vol = float(state["vol"].iloc[i])
        px = float(ctx.get("close", zl))
        dist = abs(px - zl) / vol if vol > 0 else 9
        c2 = round(max(0.0, 20.0 * (1.0 - min(dist / 1.5, 1.0))), 1)

        c3 = round(min(rr_primary / 2.5, 1.0) * 20.0, 1)

        vr = 0.5
        try:
            vr_series = self._volatility_rank(state)
            if len(vr_series) > i and not pd.isna(vr_series.iloc[i]):
                vr = float(vr_series.iloc[i])
        except Exception:
            pass
        c4 = round(20.0 * (1.0 - abs(vr - 0.6) * 1.6) if vr > 0 else 0, 1)
        c4 = max(0.0, min(20.0, c4))
        total = int(round(min(100.0, c1 + c2 + c3 + c4)))
        return total, {"MTF alignment": c1, "Band proximity": c2,
                       "Risk/Reward": c3, "Volatility regime": c4}

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        sgn = 1 if direction == "BUY" else -1
        trend = int(state["trend"].iloc[i]); prev = int(state["prev_trend"].iloc[i])
        mtf = extra.get("mtf") or {}
        agree = sum(1 for v in mtf.values() if v == sgn)
        rr = float(extra.get("rr_primary", 0))
        checks = [
            {"label": "Trend condition confirmed",
             "ok": trend == sgn and prev == sgn,
             "detail": f"trend = {TREND_LABEL.get(trend, 'Neutral')} (previous: "
                       f"{TREND_LABEL.get(prev, 'Neutral')})"},
            {"label": "Entry condition confirmed",
             "ok": True,
             "detail": f"close crossed {'above' if sgn > 0 else 'below'} the Zero-Lag EMA "
                       "on the last closed bar"},
            {"label": "Higher-timeframe conditions",
             "ok": agree >= 3,
             "detail": " / ".join(f"{tf} {TREND_LABEL.get(mtf.get(tf, 0), 'N/A')[:4]}"
                                  for tf in ["5M", "15M", "1H", "4H", "1D"]) if mtf else "n/a"},
            {"label": "Risk/reward acceptable",
             "ok": rr >= 1.5,
             "detail": f"1 : {rr:.1f} to TP2"},
        ]
        dir_word = "bullish" if sgn > 0 else "bearish"
        analysis = (
            f"The signal qualifies because the strategy's {dir_word} entry condition has been "
            f"confirmed (close crossed {'above' if sgn > 0 else 'below'} the Zero-Lag EMA) while "
            f"the {dir_word} volatility-band trend remains established, with {agree} of 5 "
            f"timeframes aligned {dir_word}."
        )
        return checks, analysis
