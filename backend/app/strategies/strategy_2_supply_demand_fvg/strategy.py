"""Strategy 2 v2.0.0 - Supply & Demand + Fair Value Gap (owner brief 2026-10-07).

Replaces the retired sweep->BOS->retest machine. Core concept:
IMPULSE -> ZONE IDENTIFICATION -> FVG -> RETEST -> ENTRY.

No AI approval per entry (AI stays downstream). SL = zone +/- buffer through
the EXISTING risk engine; TP 1R/2R/3R through the EXISTING TP/SL engine; TP
events only ever originate from confirmed real MT5 tickets.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy, Candidate


class SupplyDemandFvgStrategy(BaseStrategy):
    id = "strategy_2_supply_demand_fvg"
    name = "Supply & Demand + FVG"
    short_name = "S/D + FVG"
    description = ("Demand/supply zone + bullish/bearish fair-value gap: a base "
                   "forms the zone, a meaningful displacement leaves an FVG, and "
                   "the entry fires when price returns into the gap (Impulse - "
                   "Zone - FVG - Retest - Entry).")
    version = "2.0.0"
    markets = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "NAS100"]
    timeframes = ["15M", "30M", "1H"]
    min_bars = 40

    base_params: Dict[str, Any] = {
        # zone / displacement detection (researchable)
        "displacement_body_ratio": 0.60,     # body >= 60% of the candle range
        "displacement_atr_mult": 1.10,       # body >= 1.1 * ATR
        "zone_base_lookback": 3,             # base search window before impulse
        # FVG (researchable)
        "fvg_min_atr": 0.0,                  # min gap as ATR multiple (0 = all)
        "fvg_entry_method": "midpoint",      # touch | midpoint | deep
        "fvg_deep_fraction": 0.25,           # depth used by the 'deep' method
        # freshness / invalidation (researchable)
        "zone_max_age_bars": 96,             # setup expires after this many bars
        "invalidation_buffer_atr": 0.30,     # decisive-break buffer
        # risk (through the EXISTING engine; these shape the candidate levels)
        "sl_buffer_atr": 0.25,               # SL beyond the zone
        "tp1_r": 1.0, "tp2_r": 2.0, "tp3_r": 3.0,
        "atr_length": 14,
    }

    experiment_variables: Dict[str, Dict[str, Any]] = {
        "displacement_body_ratio": {"min": 0.4, "max": 0.85, "step": 0.05},
        "displacement_atr_mult": {"min": 0.6, "max": 2.0, "step": 0.1},
        "zone_base_lookback": {"min": 1, "max": 6, "step": 1},
        "fvg_min_atr": {"min": 0.0, "max": 1.0, "step": 0.05},
        "fvg_entry_method": {"choices": ["touch", "midpoint", "deep"]},
        "fvg_deep_fraction": {"min": 0.1, "max": 0.45, "step": 0.05},
        "zone_max_age_bars": {"min": 24, "max": 192, "step": 12},
        "sl_buffer_atr": {"min": 0.1, "max": 0.6, "step": 0.05},
        "invalidation_buffer_atr": {"min": 0.15, "max": 0.6, "step": 0.05},
    }

    # ---------------------------------------------------------------- interface
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        from ..base import atr as _  # noqa: F401  (kept for tooling symmetry)
        from ...core.indicators import atr
        return {"atr": atr(df, int(params.get("atr_length", 14))),
                "state": "machine-driven (see detect_signal)"}

    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        return None   # entries only via the persisted S/D+FVG machine

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        """Zone +/- buffer through the EXISTING risk engine conventions."""
        try:
            from ...core.indicators import atr as _atr
            a = float(_atr(df, int(params.get("atr_length", 14))).iloc[i])
        except Exception:
            a = 0.0
        entry = float(df["close"].iloc[i])
        buf = float(params.get("sl_buffer_atr", 0.25)) * max(a, 1e-9)
        sl = entry - buf if direction == "BUY" else entry + buf
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str,
                          params) -> list:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * float(params.get(k, r)) * risk
                for k, r in (("tp1_r", 1.0), ("tp2_r", 2.0), ("tp3_r", 3.0))]

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        checks = [{"label": "zone", "ok": bool(extra.get("zone_type")),
                   "detail": f"{extra.get('zone_type')} zone"},
                  {"label": "fvg", "ok": extra.get("fvg_high") is not None,
                   "detail": "FVG retested"},
                  {"label": "entry", "ok": True,
                   "detail": f"{extra.get('entry_method', 'midpoint')} method"}]
        analysis = ("Demand/Supply -> Displacement -> FVG -> Retest -> Entry: "
                    f"{extra.get('zone_type')} zone with FVG retest, "
                    f"{extra.get('entry_method', 'midpoint')} entry.")
        return checks, analysis

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        """Base-class contract. detect_signal() assembles candidates from the
        machine directly; this is provided for interface completeness."""
        direction = str(event.get("direction", "BUY"))
        entry = float(event.get("entry", df["close"].iloc[i]))
        sl = float(event.get("sl", entry))
        risk = abs(entry - sl) or 1e-9
        sign = 1.0 if direction == "BUY" else -1.0
        tps = [entry + sign * float(params.get(k, r) ) * risk
               for k, r in (("tp1_r", 1.0), ("tp2_r", 2.0), ("tp3_r", 3.0))]
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=direction, candle_time=str(df.index[i]),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=tps,
            risk=risk, rr_primary=abs(tps[0] - entry) / risk,
            score=int(event.get("score", 60)),
            score_components={"machine": float(event.get("score", 60))},
            checks=[], analysis=event.get("analysis", "S/D + FVG machine setup"),
            params=dict(params),
            extra={k: v for k, v in event.items() if k not in
                   ("direction", "entry", "sl", "tps", "risk", "score")})

    # ---------------------------------------------------------------- detection
    def detect_signal(self, df: pd.DataFrame, market: str, timeframe: str,
                      params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None) -> Optional[Candidate]:
        params = self.get_parameters(params)
        if timeframe not in self.timeframes or len(df) < self.min_bars:
            return None

        from . import machine
        from . import state as state_store

        st = state_store.load(market, timeframe)
        session = (score_context or {}).get("session", "")
        cand, st, events = machine.evaluate(df, market, timeframe, st, params,
                                            session=session)
        try:
            state_store.save(market, timeframe, st)
        except Exception:
            pass
        if not cand or market not in self.markets:
            return None

        entry = float(cand["entry"]); sl = float(cand["sl"])
        risk = float(cand["risk"]); tps = [float(t) for t in cand["tps"]]
        fvg = cand.get("fvg") or {}
        zone = cand.get("zone") or {}
        extra = {
            "setup_state": "SIGNAL",
            "setup_sequence": "Zone -> Displacement -> FVG -> Retest -> Entry",
            "strategy_alias": self.id,
            "zone_type": zone.get("type"),
            "zone_high": zone.get("zone_high"),
            "zone_low": zone.get("zone_low"),
            "displacement_time": fvg.get("displacement_time"),
            "displacement_index": fvg.get("displacement_index"),
            "fvg_type": fvg.get("type"),
            "fvg_high": fvg.get("fvg_high"),
            "fvg_low": fvg.get("fvg_low"),
            "fvg_mid": fvg.get("fvg_mid"),
            "fvg_created_time": fvg.get("created_time"),
            "retest_time": cand.get("retest_time"),
            "entry_method": str(params.get("fvg_entry_method", "midpoint")),
            "session": session,
            "atr": cand.get("atr"),
            "market_context": {"timeframe": timeframe, "bars": len(df)},
            "events": events[-3:],
        }
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=cand["direction"], candle_time=str(df.index[-1]),
            entry=entry, entry_zone=[entry, entry], sl=sl, tps=tps,
            risk=risk, rr_primary=abs(tps[0] - entry) / risk if risk > 0 else 0.0,
            score=int(cand.get("score", 60)),
            score_components={"machine": float(cand.get("score", 60))},
            checks=[], analysis=(f"{extra['zone_type']} zone "
                                 f"{extra['zone_low']:.5g}-{extra['zone_high']:.5g} "
                                 f"with {extra['fvg_type']} "
                                 f"{extra['fvg_low']:.5g}-{extra['fvg_high']:.5g} "
                                 f"retested - entry {extra['entry_method']}"),
            mtf=None, params=dict(params), extra=extra)
