"""Experiments - scientific ONE-VARIABLE testing (SPEC §21, §23, §28, §39).

HARD SYSTEM VALIDATION (SPEC §28): if an experiment changes more than one
strategy variable, the backend REJECTS it. This is enforced here, at the
engine level - never only in an AI prompt.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from ..backtesting.engine import BacktestEngine
from ..config import settings
from ..db.store import get_store
from ..learning.metrics import classify_experiment, compute_metrics
from ..strategies import get_strategy


class ExperimentError(Exception):
    """Raised when an experiment violates a hard rule - surfaces as HTTP 422."""


def validate_one_variable(strategy_id: str, base_params: Dict[str, Any],
                          new_params: Dict[str, Any]) -> str:
    """Returns the single changed variable name, or raises ExperimentError."""
    strategy = get_strategy(strategy_id)
    base = strategy.get_parameters(base_params)
    new = strategy.get_parameters(new_params)

    for k in set(new) - set(base):
        raise ExperimentError(f"Unknown parameter '{k}' for {strategy_id}.")

    changed = {}
    for k in set(base) | set(new):
        bv, nv = base.get(k), new.get(k)
        if k in strategy.experiment_variables:
            bv = strategy._validate_param(k, bv)
            nv = strategy._validate_param(k, nv)
        if bv != nv:
            changed[k] = (bv, nv)

    if len(changed) == 0:
        raise ExperimentError("Experiment rejected: no strategy variable was changed.")
    if len(changed) > 1:
        detail = ", ".join(f"{k}: {o} -> {n}" for k, (o, n) in sorted(changed.items()))
        raise ExperimentError(
            f"Experiment rejected: more than one strategy variable was changed ({detail}). "
            "Only one strategy variable may be changed per experiment.")
    key = next(iter(changed))
    if key not in strategy.experiment_variables:
        raise ExperimentError(
            f"Experiment rejected: '{key}' is not an experimentable variable of {strategy_id}.")

    # cross-field sanity rules
    if strategy_id == "strategy_2_ema_atr" and key in ("fast_len", "slow_len"):
        f = new.get("fast_len", base.get("fast_len"))
        s = new.get("slow_len", base.get("slow_len"))
        if int(f) >= int(s):
            raise ExperimentError("Experiment rejected: fast EMA must remain below slow EMA.")
    return key


class ExperimentEngine:
    """Runs original-vs-experimental on the SAME dataset, computes metrics and
    classifies the result (IMPROVED / NO SIGNIFICANT CHANGE / WORSE /
    INSUFFICIENT DATA - SPEC §23)."""

    def __init__(self, provider):
        self.backtester = BacktestEngine(provider)

    def compare(self, user_id: str, strategy_id: str, base_params: Dict[str, Any],
                new_params: Dict[str, Any], market: str, timeframe: str) -> Dict[str, Any]:
        strategy = get_strategy(strategy_id)
        changed_key = validate_one_variable(strategy_id, base_params, new_params)

        # SAME dataset for both versions (SPEC §39)
        base_result = self.backtester.run(user_id, strategy_id, market, timeframe,
                                          params=base_params, save=False)
        if not base_result.get("ok"):
            raise ExperimentError(base_result.get("error", "Market data unavailable."))
        exp_result = self.backtester.run(user_id, strategy_id, market, timeframe,
                                         params=new_params, save=False)

        orig_metrics = compute_metrics(base_result["trades"])
        exp_metrics = compute_metrics(exp_result["trades"])
        verdict = classify_experiment(orig_metrics, exp_metrics,
                                      settings.min_trades_for_experiment)

        # ---- anti-overfitting: time-ordered 70/30 train/validation split ----
        def _split(trades):
            ts = sorted(trades, key=lambda t: t.get("entry_time", ""))
            cut = max(1, int(len(ts) * 0.7))
            return ts[:cut], ts[cut:]

        b_train, b_val = _split(base_result["trades"])
        e_train, e_val = _split(exp_result["trades"])
        train_m = compute_metrics(b_train); train_e = compute_metrics(e_train)
        val_m = compute_metrics(b_val); val_e = compute_metrics(e_val)
        overfit_risk = False
        if (train_e.get("trades", 0) >= 10 and val_e.get("trades", 0) >= 5
                and train_m.get("trades", 0) >= 10):
            train_better = train_e.get("expectancy", 0) > train_m.get("expectancy", 0)
            val_worse = val_e.get("expectancy", 0) < val_m.get("expectancy", 0)
            overfit_risk = bool(train_better and val_worse)

        if verdict.get("result") != "INSUFFICIENT_DATA" and overfit_risk:
            verdict["overfitting_risk"] = True
            verdict["conclusion"] += (
                " WARNING: HIGH OVERFITTING RISK - the change improves the training "
                "period but degrades on the validation period. Do not approve without "
                "further out-of-sample evidence.")
        if verdict.get("result") != "INSUFFICIENT_DATA" and (
                train_e.get("trades", 0) < 10 or val_e.get("trades", 0) < 5):
            verdict["small_sample_warning"] = True
            verdict["conclusion"] += (
                " Note: small sample (train/val trades below 10/5) - any measured "
                "improvement may be noise.")

        # robustness across sessions (same dataset, same trades, split by session)
        robustness = {"sessions": {}, "note": None}
        sessions = sorted({t.get("session") for t in base_result["trades"]
                           if t.get("session")} | {t.get("session") for t in exp_result["trades"]
                                                  if t.get("session")})
        for ses in sessions:
            b_ses = [t for t in base_result["trades"] if t.get("session") == ses]
            e_ses = [t for t in exp_result["trades"] if t.get("session") == ses]
            if len(b_ses) >= 5 and len(e_ses) >= 5:
                robustness["sessions"][ses] = {
                    "base": compute_metrics(b_ses), "exp": compute_metrics(e_ses)}
        if not robustness["sessions"]:
            robustness["note"] = ("Insufficient data for robustness test - "
                                  "no session bucket reached 5 trades per version.")

        # signal frequency (trades per day over the dataset period)
        freq = None
        try:
            days = max(1.0, (pd.Timestamp(base_result["dataset"]["end"])
                             - pd.Timestamp(base_result["dataset"]["start"])).total_seconds() / 86400.0)
            freq = round(len(exp_result["trades"]) / days, 3)
        except Exception:
            pass

        return {
            "strategy_id": strategy_id,
            "variable": changed_key,
            "old_value": base_params.get(changed_key),
            "new_value": new_params.get(changed_key),
            "base_params": base_params,
            "new_params": new_params,
            "market": market,
            "timeframe": timeframe,
            "dataset": base_result["dataset"],
            "original_metrics": orig_metrics,
            "experimental_metrics": exp_metrics,
            "verdict": verdict,
            "overfitting_risk": verdict.get("overfitting_risk", False),
            "small_sample_warning": verdict.get("small_sample_warning", False),
            "split": {
                "train": {"base": {k: v for k, v in train_m.items() if not isinstance(v, dict)},
                          "exp": {k: v for k, v in train_e.items() if not isinstance(v, dict)}},
                "validation": {"base": {k: v for k, v in val_m.items() if not isinstance(v, dict)},
                               "exp": {k: v for k, v in val_e.items() if not isinstance(v, dict)}},
            },
            "robustness": robustness,
            "signal_frequency_per_day": freq,
        }

    def run_from_hypothesis(self, user_id: str, hypothesis: Dict[str, Any]) -> Dict[str, Any]:
        store = get_store()
        strategy_id = hypothesis["strategy_id"]
        strategy = get_strategy(strategy_id)

        active = store.list("strategy_versions",
                            filters={"strategy_id": strategy_id, "active": True}, limit=1)
        base_params = dict(active[0]["params"]) if active else dict(strategy.base_params)

        new_params = dict(base_params)
        new_params[hypothesis["variable"]] = hypothesis["new_value"]

        ds = hypothesis.get("dataset") or {}
        market = ds.get("market", "XAUUSD")
        timeframe = ds.get("timeframe", "15M")

        comparison = self.compare(user_id, strategy_id, base_params, new_params,
                                  market, timeframe)
        verdict = comparison["verdict"]

        now = comparison["dataset"]["end"]
        code = f"EXP-{store.count('experiments') + 1:06d}"
        exp_doc = store.create("experiments", {
            "userId": user_id,
            "experiment_code": code,
            "hypothesis_id": hypothesis["id"],
            "strategy_id": strategy_id,
            "variable": comparison["variable"],
            "old_value": comparison["old_value"],
            "new_value": comparison["new_value"],
            "base_params": comparison["base_params"],
            "new_params": comparison["new_params"],
            "market": market,
            "timeframe": timeframe,
            "dataset": comparison["dataset"],
            "original_metrics": {k: v for k, v in comparison["original_metrics"].items()
                                 if not isinstance(v, dict)},
            "experimental_metrics": {k: v for k, v in comparison["experimental_metrics"].items()
                                     if not isinstance(v, dict)},
            "result": verdict["result"],
            "conclusion": verdict["conclusion"],
            "recommend_approval": verdict.get("recommend_approval", False),
            "delta_win_rate": verdict.get("delta_win_rate"),
            "delta_expectancy": verdict.get("delta_expectancy"),
            "overfitting_risk": bool(comparison.get("overfitting_risk")),
            "small_sample_warning": bool(comparison.get("small_sample_warning")),
            "status": "READY_FOR_REVIEW",
            "split": comparison.get("split"),
            "robustness": (comparison.get("robustness") or {}).get("sessions"),
            "robustness_note": (comparison.get("robustness") or {}).get("note"),
            "signal_frequency_per_day": comparison.get("signal_frequency_per_day"),
        })

        store.update("hypotheses", hypothesis["id"], {
            "status": "AWAITING_APPROVAL" if verdict["recommend_approval"] else "CONCLUDED",
            "experiment_id": exp_doc["id"],
            "ai_conclusion": verdict["conclusion"],
            "result": verdict["result"],
            "old_metrics": {k: v for k, v in comparison["original_metrics"].items()
                            if not isinstance(v, dict)},
            "new_metrics": {k: v for k, v in comparison["experimental_metrics"].items()
                            if not isinstance(v, dict)},
        })
        return exp_doc
