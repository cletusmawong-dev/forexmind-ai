"""STRATEGY MODULE TEMPLATE (owner spec 2026-11-02).

One strategy = ONE protected Python module. This file is the format every
new strategy follows. To create a real strategy:

  1. Copy this file -> strategies/my_strategy.py   (NEVER edit another
     strategy's module - each file is the single source of truth for that
     strategy's rules).
  2. Rename the class, fill in the identity block and YOUR exact rules.
  3. Register it in strategies/__init__.py:
         from .my_strategy import MyStrategy
         STRATEGY_CLASSES = {..., MyStrategy.id: MyStrategy}
  4. Set its state in learning/versions.py (LIFECYCLE dict) - states live
     OUTSIDE the strategy code. Add the id to START_PAUSED there if it
     must boot paused and be activated by the owner explicitly.
  5. Run the tests:  pytest tests/test_strategy_architecture.py
                     pytest tests/test_strategy_regression.py

Contract notes:
  * detect_signal() (inherited) walks back over closed bars, calls
    detect_on_bar, stamps strategy_version + timestamp, and REFUSES any
    candidate with incoherent levels (entry/sl must be positive, sl must
    differ from entry, every TP on the correct side of entry).
  * A trail-only strategy (no fixed targets, exits managed by the trade
    manager like TrendBreak v2) returns tps=[] - that is valid.
  * backtest() is inherited: a deterministic walk-forward over real
    candles using detect_on_bar + calculate_risk + calculate_targets +
    tp_rrs. Implement tp_rrs (the R multiple of each TP leg).
  * Never look ahead: compute() works on completed bars only; i in
    detect_on_bar is a fully closed bar.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseStrategy, Candidate


class MyStrategy(BaseStrategy):
    # ------------------------------------------------------------------
    # IDENTITY - the signal contract carries id + version on every signal
    # ------------------------------------------------------------------
    id = "strategy_my_name"          # unique, stable, never reused
    name = "My Strategy"
    short_name = "MYSTRAT"
    description = ("One paragraph: the exact entry idea, stop rule and "
                   "target rule, in plain English.")
    version = "1.0"                  # changes ONLY via approved versions
    markets = ["XAUUSD"]             # the instruments this strategy may trade
    timeframes = ["15M"]             # the entry timeframe(s)
    min_bars = 300                   # history required before it scans

    # The strategy's own numbers (the RULES). Data docs may override via
    # learning.active_params(); the AI can only change declared
    # experiment_variables, one at a time, with approval.
    base_params: Dict[str, Any] = {
        "ema_fast": 9,
        "ema_slow": 21,
        "stop_atr": 1.5,
        "rr": 2.0,
    }
    # research knobs (ONE-VARIABLE rule, validated + audited)
    experiment_variables: Dict[str, Any] = {
        "ema_fast": {"type": "int", "min": 5, "max": 50},
        "ema_slow": {"type": "int", "min": 20, "max": 200},
        "stop_atr": {"type": "float", "min": 0.5, "max": 5.0},
        "rr": {"type": "float", "min": 1.0, "max": 5.0},
    }

    # ------------------------------------------------------------------
    # INDICATORS - vectorised state on the closed-bar frame (no lookahead)
    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        close = df["close"]
        return {
            "ema_fast": close.ewm(span=int(params["ema_fast"])).mean(),
            "ema_slow": close.ewm(span=int(params["ema_slow"])).mean(),
            "atr": (df["high"] - df["low"]).rolling(14).mean(),
        }

    # ------------------------------------------------------------------
    # ENTRY RULE - the sacred part. Exact conditions on CLOSED bar i.
    # Return {"direction": "BUY"|"SELL", "i": i} or None.
    # ------------------------------------------------------------------
    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        f, s = state["ema_fast"].iloc[i], state["ema_slow"].iloc[i]
        f1, s1 = state["ema_fast"].iloc[i - 1], state["ema_slow"].iloc[i - 1]
        if f1 <= s1 and f > s:                      # bullish cross on bar i
            return {"direction": "BUY", "i": i}
        if f1 >= s1 and f < s:                      # bearish cross on bar i
            return {"direction": "SELL", "i": i}
        return None                                 # no valid setup

    # ------------------------------------------------------------------
    # ASSEMBLE THE SIGNAL - levels validated automatically afterwards
    # ------------------------------------------------------------------
    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        direction = event["direction"]
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
            candle_time=str(df.index[i]),            # the signal bar
            entry=rk["entry"],
            entry_zone=[rk["entry"], rk["entry"]],
            sl=rk["sl"],
            tps=tps,                                 # [] for trail-only
            risk=rk["risk"],
            rr_primary=(abs(tps[0] - rk["entry"]) / rk["risk"]) if tps else 0.0,
            score=50,
            score_components={"my_rule": 50.0},
            checks=checks,
            analysis=analysis,
            params={},
            params_version=self.version,
            extra={},                                # strategy-owned payload
            metadata={"source": "owner spec <date>"},
        )
        # strategy_version + timestamp are stamped for you by the base flow;
        # signal_id is assigned by the signal engine (SIG-YYYYMMDD-NNN).

    # ------------------------------------------------------------------
    # STOP-LOSS RULE
    # ------------------------------------------------------------------
    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        atr = float(state["atr"].iloc[i])
        sl = entry - float(params["stop_atr"]) * atr if direction == "BUY" \
            else entry + float(params["stop_atr"]) * atr
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    # ------------------------------------------------------------------
    # TAKE-PROFIT RULE (price ladder, nearest first)
    # ------------------------------------------------------------------
    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params: Dict[str, Any]) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float(params["rr"])]

    # ------------------------------------------------------------------
    # REASONING - observable conditions only, human-readable
    # ------------------------------------------------------------------
    def explain_signal(self, state, i, direction, params, extra):
        checks = [{
            "label": "EMA fast above slow",
            "ok": bool(state["ema_fast"].iloc[i] > state["ema_slow"].iloc[i]),
            "detail": (f"fast={state['ema_fast'].iloc[i]:.5g} "
                       f"slow={state['ema_slow'].iloc[i]:.5g}"),
        }]
        return checks, (f"{direction} on the {int(params['ema_fast'])}/"
                        f"{int(params['ema_slow'])} cross, stop "
                        f"{params['stop_atr']}xATR, target {params['rr']}R.")

    # ------------------------------------------------------------------
    # BACKTEST SUPPORT - R multiple of each TP leg (banked in sequence)
    # ------------------------------------------------------------------
    def tp_rrs(self, params) -> List[float]:
        return [float(params["rr"])]
