"""Deterministic regression harness for the strategy architecture upgrade.

Owner spec 2026-11-02 (section 5): "Before and after migration, run
regression tests using the same historical market data. The refactored
strategy must produce the same signals as the original implementation."

Real candle history cannot be committed to the repo, so the harness builds
DETERMINISTIC synthetic frames (fixed seed, fixed index) and hashes every
rule-output field of what each strategy produces on them:

  * detect_signal at three window cuts (exercises lookback/bar selection)
  * backtest() walk-forward trade list

Only RULE outputs are hashed (direction, entry, sl, tps, risk, R:R, score,
checks, analysis, extra) - contract metadata added by the upgrade
(strategy_version/timestamp/signal_id/metadata on Candidate) is excluded,
so the before/after comparison proves the TRADING BEHAVIOUR is unchanged.

The snapshot JSON (tests/fixtures/strategy_regression_snapshot.json) is
committed with the code HEAD it was captured on. Any future change that
alters a strategy's signal on this data changes a hash and fails the test.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

import numpy as np
import pandas as pd

SEED = 20261102
N_BARS = 900
END = "2026-11-02 00:00"

# Rule-output fields only (see module docstring).
CAND_FIELDS = ["direction", "entry", "entry_zone", "sl", "tps", "risk",
               "rr_primary", "score", "score_components", "checks",
               "analysis", "mtf", "candle_time", "params", "params_version",
               "extra"]

# Registered live strategies are pulled from the registry; the dormant
# EMA-family modules (spec section 5 names the "9/21 EMA" strategy - the
# repo's Zero Lag EMA implementation) are instantiated directly.
DORMANT = [
    ("strategy_1_zero_lag", "app.strategies.strategy_1_zero_lag.strategy",
     "ZeroLagStrategy"),
    ("strategy_2_ema_atr", "app.strategies.strategy_2_ema_atr.strategy",
     "EmaAtrStrategy"),
    ("strategy_2_supply_demand_fvg",
     "app.strategies.strategy_2_supply_demand_fvg", "SupplyDemandFvgStrategy"),
]


def synth_frame(seed: int, n: int = N_BARS) -> pd.DataFrame:
    """Deterministic OHLC walk with trending regimes (indicators see a
    realistic mix of trend and pullback)."""
    rng = np.random.default_rng(seed)
    steps = []
    for k in range(n // 150 + 1):
        steps += [rng.normal(0.0004 if k % 2 == 0 else -0.0003, 0.004, 150)]
    rets = np.concatenate(steps)[:n]
    close = 100.0 * np.exp(np.cumsum(rets))
    spread = np.abs(rng.normal(0, 0.0015, n)) * close
    open_ = np.roll(close, 1) + rng.normal(0, 0.0004, n) * close
    open_[0] = close[0]
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range(end=END, periods=n, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": rng.integers(100, 1000, n)},
                        index=idx)


def higher_frames(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum"}
    return {"1H": df.resample("1h").agg(agg).dropna(),
            "4H": df.resample("4h").agg(agg).dropna()}


def _clean(obj: Any):
    if is_dataclass(obj):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 10)
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


def candidate_record(cand) -> Any:
    if cand is None:
        return None
    d = _clean(cand)
    return {k: d.get(k) for k in CAND_FIELDS}


def strategy_record(strategy, markets: List[str]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"markets": {}}
    for mi, market in enumerate(markets):
        df = synth_frame(SEED + 17 * (mi + 1))
        hf = higher_frames(df)
        mrec: Dict[str, Any] = {}
        for cut in (0, 25, 60):
            try:
                cand = strategy.detect_signal(
                    df.iloc[: len(df) - cut].copy(), market, "15M",
                    higher_frames=hf)
                mrec[f"detect_{cut}"] = candidate_record(cand)
            except Exception as e:   # a deterministic error is a fingerprint
                mrec[f"detect_{cut}"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            mrec["backtest"] = _clean(strategy.backtest(df, max_hold_bars=200))
        except Exception as e:
            mrec["backtest"] = {"error": f"{type(e).__name__}: {e}"}
        rec["markets"][market] = mrec
    rec["hash"] = hashlib.sha256(
        json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
    return rec


def build_snapshot() -> Dict[str, Any]:
    from app.strategies import all_strategies
    import importlib

    out: Dict[str, Any] = {}
    for sid, strategy in all_strategies().items():
        markets = (strategy.markets or ["EURUSD"])[:2]
        out[sid] = strategy_record(strategy, markets)
    for sid, modpath, clsname in DORMANT:
        cls = getattr(importlib.import_module(modpath), clsname)
        out[sid] = strategy_record(cls(), (cls.markets or ["EURUSD"])[:2])
    return out


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


if __name__ == "__main__":
    snap = build_snapshot()
    for sid, rec in snap.items():
        print(f"{sid:34s} {rec['hash'][:16]}")
    print("TOTAL", snapshot_hash(snap)[:16])
