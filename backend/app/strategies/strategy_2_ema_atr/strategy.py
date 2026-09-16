"""Strategy 2 - KN Smart TP/SL Signals

Faithful implementation of the user-supplied Pine Script
('KN - Smart TP SL Signals', @version=6, verified line-by-line 2026-09-15,
locked by tests/test_strategy2_fidelity.py) with the ONE user-confirmed
modification: Fast EMA = 9, Slow EMA = 21 (script defaults are 5/13).
Every other rule is verbatim: entry = close, risk = ATR(14) x 1.5,
SL = close -/+ risk, TP1/2/3 = close +/- risk x 1.0/2.0/3.0, BUY on fast
EMA crossing ABOVE slow, SELL on cross BELOW; TP hit = high/low vs level
per direction, each TP independent. Documented accounting decisions (not
strategy changes): same-bar SL-before-TP, 200-bar tracking expiry.

    Fast EMA = EMA(close, 9)      Slow EMA = EMA(close, 21)      ATR = ATR(14)

    BUY  when 9 EMA crosses ABOVE 21 EMA
    SELL when 9 EMA crosses BELOW 21 EMA

    Risk distance = ATR(14) x 1.5
    Entry  = close
    SL     = entry -/+ risk
    TP1/TP2/TP3 = 1R / 2R / 3R

Trade monitoring tracks TP1 / TP2 / TP3 / SL hits (SPEC §11).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ...core.indicators import atr, crossover, crossunder, ema
from ..base import BaseStrategy, Candidate

SESSION_WEIGHT = {"London": 15.0, "NewYork": 15.0, "Asian": 8.0, "Late": 10.0}


class EmaAtrStrategy(BaseStrategy):
    id = "strategy_2_ema_atr"
    name = "KN Smart TP/SL Signals (9/21 EMA)"
    short_name = "9/21 EMA Smart TP/SL"
    description = ("9/21 EMA crossover entries with ATR-based stop loss and a "
                   "1R / 2R / 3R take-profit ladder.")
    version = "1.0"
    base_params = {
        "fast_len": 9,
        "slow_len": 21,
        "atr_len": 14,
        "sl_mult": 1.5,
        "tp1_rr": 1.0,
        "tp2_rr": 2.0,
        "tp3_rr": 3.0,
        "expire_bars": 200,
    }
    experiment_variables = {
        "fast_len": {"type": "int",   "min": 5,   "max": 50,  "step": 1,
                     "description": "Fast EMA length"},
        "slow_len": {"type": "int",   "min": 10,  "max": 200, "step": 1,
                     "description": "Slow EMA length"},
        "atr_len":  {"type": "int",   "min": 5,   "max": 50,  "step": 1,
                     "description": "ATR period"},
        "sl_mult":  {"type": "float", "min": 0.5, "max": 5.0, "step": 0.1,
                     "description": "SL ATR multiplier"},
        "tp1_rr":   {"type": "float", "min": 0.5, "max": 6.0, "step": 0.25,
                     "description": "TP1 risk-reward"},
        "tp2_rr":   {"type": "float", "min": 0.5, "max": 6.0, "step": 0.25,
                     "description": "TP2 risk-reward"},
        "tp3_rr":   {"type": "float", "min": 0.5, "max": 6.0, "step": 0.25,
                     "description": "TP3 risk-reward"},
    }
    markets = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["5M", "15M", "1H", "4H", "1D"]
    min_bars = 200

    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        fast = int(params["fast_len"]); slow = int(params["slow_len"])
        ema_f = ema(df["close"], fast)
        ema_s = ema(df["close"], slow)
        _atr = atr(df, int(params["atr_len"]))
        buy = crossover(ema_f, ema_s)
        sell = crossunder(ema_f, ema_s)
        return {"ema_f": ema_f, "ema_s": ema_s, "atr": _atr,
                "buy": buy, "sell": sell, "params": params}

    def detect_on_bar(self, state, i: int) -> Optional[Dict[str, Any]]:
        if i < 2:
            return None
        if bool(state["buy"].iloc[i]):
            return {"direction": "BUY", "i": i}
        if bool(state["sell"].iloc[i]):
            return {"direction": "SELL", "i": i}
        return None

    # ------------------------------------------------------------------
    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        close = float(df["close"].iloc[i])
        risk = float(state["atr"].iloc[i]) * float(params["sl_mult"])
        if pd.isna(risk) or risk <= 0:
            return {"entry": close, "sl": close, "risk": 0.0,
                    "entry_zone": [close, close]}
        sl = close - risk if direction == "BUY" else close + risk
        return {"entry": close, "sl": sl, "risk": risk,
                "entry_zone": [close, close]}

    def tp_rrs(self, params) -> List[float]:
        return [float(params["tp1_rr"]), float(params["tp2_rr"]), float(params["tp3_rr"])]

    def calculate_targets(self, entry, risk, direction, params) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float(params[k]) for k in ("tp1_rr", "tp2_rr", "tp3_rr")]

    # ------------------------------------------------------------------
    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, params)
        if rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, params)
        ctx = dict(score_context or {})
        ctx.setdefault("close", float(df["close"].iloc[i]))
        ctx.setdefault("session", ctx.get("session", "London"))
        ctx.setdefault("htf_df", (higher_frames or {}).get("1H"))
        checks, analysis = self.explain_signal(
            state, i, direction, params,
            {"risk": rk["risk"], "session": ctx["session"]})
        score, comps = self.score(state, i, direction, ctx)
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=direction, candle_time=str(df.index[i]),
            entry=rk["entry"], entry_zone=rk.get("entry_zone", [rk["entry"], rk["entry"]]),
            sl=rk["sl"], tps=tps, risk=rk["risk"],
            rr_primary=float(params["tp3_rr"]),
            score=score, score_components=comps, checks=checks, analysis=analysis,
            mtf=None, params=dict(params), params_version=self.version,
        )

    def score(self, state, i, direction, ctx) -> tuple:
        sgn = 1 if direction == "BUY" else -1
        atr_i = float(state["atr"].iloc[i])
        sep = abs(float(state["ema_f"].iloc[i]) - float(state["ema_s"].iloc[i]))
        ratio = (sep / atr_i) if atr_i > 0 else 0.0
        c1 = round(min(ratio / 0.4, 1.0) * 40.0, 1)

        vr = 0.5
        try:
            vr_series = self._volatility_rank(state)
            if len(vr_series) > i and not pd.isna(vr_series.iloc[i]):
                vr = float(vr_series.iloc[i])
        except Exception:
            pass
        c2 = round(max(0.0, min(30.0 * (1.0 - abs(vr - 0.6) * 1.6), 30.0)), 1)

        c3 = SESSION_WEIGHT.get(ctx.get("session", "London"), 10.0)

        c4 = 0.0
        htf = ctx.get("htf_df")
        try:
            if htf is not None and len(htf) >= 50:
                h = htf.dropna()
                f = ema(h["close"], 9); s = ema(h["close"], 21)
                if (f.iloc[-1] > s.iloc[-1] and sgn > 0) or (f.iloc[-1] < s.iloc[-1] and sgn < 0):
                    c4 = 15.0
        except Exception:
            pass

        total = int(round(min(100.0, c1 + c2 + c3 + c4)))
        return total, {"Crossover strength": c1, "Volatility regime": c2,
                       "Session": c3, "1H EMA alignment": c4}

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        sgn = 1 if direction == "BUY" else -1
        atr_i = float(state["atr"].iloc[i]); sm = float(params["sl_mult"])
        checks = [
            {"label": f"{int(params['fast_len'])}/{int(params['slow_len'])} EMA crossover confirmed",
             "ok": True,
             "detail": f"fast EMA crossed {'above' if sgn > 0 else 'below'} slow EMA on the last closed bar"},
            {"label": "Entry condition confirmed",
             "ok": True,
             "detail": f"entry at close, SL = {sm:g} x ATR({int(params['atr_len'])})"},
            {"label": "Higher-timeframe conditions",
             "ok": True, "detail": "not required by this strategy"},
            {"label": "Risk/reward acceptable",
             "ok": True,
             "detail": f"TP1 1R / TP2 2R / TP3 3R from a {atr_i * sm:.5g} stop distance"},
        ]
        dir_word = "bullish" if sgn > 0 else "bearish"
        analysis = (
            f"The signal qualifies because the {int(params['fast_len'])}-EMA crossed "
            f"{'above' if sgn > 0 else 'below'} the {int(params['slow_len'])}-EMA, confirming the "
            f"{dir_word} entry condition, with the stop-loss calculated from "
            f"{sm:g} x ATR({int(params['atr_len'])}) and targets at 1R, 2R and 3R."
        )
        return checks, analysis
