"""SYNTHETIC STRESS LAB (3.0 spec section 31).

Runs a strategy's REAL deterministic signal engine over CONTROLLED SYNTHETIC
OHLC scenarios (trend/range/vol regimes/spread-shock proxy/gap/rapid reversal).
Clearly labeled SYNTHETIC - never mixed with real historical evidence.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


def _frame(kind: str, n: int = 400, base: float = 1.1000) -> List[dict]:
    rows = []
    px = base
    for i in range(n):
        if kind == "trend_up":
            px += 0.00035
            o = px - 0.00010
        elif kind == "trend_down":
            px -= 0.00035
            o = px + 0.00010
        elif kind == "range":
            o = base + 0.0008 * math.sin(i / 12.0)
            px = o + 0.00008 * math.cos(i / 5.0)
        elif kind == "high_vol":
            o = base + 0.004 * math.sin(i / 7.0) + (0.0015 if i % 11 == 0 else 0)
            px = o + (0.0022 if i % 2 else -0.0018)
        elif kind == "low_vol":
            o = base + 0.00008 * math.sin(i / 20.0)
            px = o + 0.00003
        elif kind == "gap":
            o = px + (0.006 if i == n // 2 else 0.0001)
            px = o + 0.00005
        elif kind == "rapid_reversal":
            px += 0.0005 if i < n // 2 else -0.0005
            o = px - 0.00005
        else:
            o, px = base, base
        hi = max(o, px) + 0.0002
        lo = min(o, px) - 0.0002
        rows.append({"open": round(o, 5), "high": round(hi, 5),
                     "low": round(lo, 5), "close": round(px, 5), "volume": 100})
    return rows


SCENARIOS = ("trend_up", "trend_down", "range", "high_vol", "low_vol",
             "gap", "rapid_reversal")


def _to_df(rows: List[dict], start="2026-01-01", freq="15min"):
    import pandas as pd
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    df = pd.DataFrame(rows)
    df.index = idx
    df.index.name = "datetime"
    return df


def run_stress(strategy_id: str, seed: int = 7) -> dict:
    from ..strategies import get_strategy
    strategy = get_strategy(strategy_id)
    out = []
    for sc in SCENARIOS:
        try:
            df = _to_df(_frame(sc), start=f"2026-01-0{(SCENARIOS.index(sc) % 9) + 1}")
            sigs = 0
            for i in range(60, len(df)):
                try:
                    sig = strategy.detect_signal(df.iloc[: i + 1], "EURUSD", "15M")
                except TypeError:
                    sig = strategy.detect_signal(df.iloc[: i + 1], "EURUSD", "15M", None)
                if sig:
                    sigs += 1
            out.append({"scenario": sc, "signals": sigs,
                        "bars": len(df), "data": "SYNTHETIC"})
        except Exception as exc:
            out.append({"scenario": sc, "error": type(exc).__name__,
                        "data": "SYNTHETIC"})
    return {
        "strategy_id": strategy_id, "status": "SYNTHETIC_TEST",
        "scenarios": out,
        "disclaimer": ("controlled synthetic frames through the real deterministic "
                       "signal engine - NOT real historical evidence and NOT a "
                       "performance claim"),
    }
