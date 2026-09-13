"""Hypothesis system (SPEC §24) + hypothesis generation from lessons (§21).

Every hypothesis gets an ID (HYP-###), stores variable / old value / new
value / reason / expected effect / dataset, and can only ever propose ONE
variable change. The AI never applies changes itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import settings
from ..db.store import get_store
from ..strategies import get_strategy


def next_hypothesis_id() -> str:
    store = get_store()
    return f"HYP-{store.count('hypotheses') + 1:03d}"


def create_hypothesis(user_id: str, strategy_id: str, variable: str, new_value: Any,
                      reason: str, expected_effect: str,
                      dataset: Optional[Dict[str, Any]] = None,
                      source_lesson: Optional[str] = None) -> Dict[str, Any]:
    strategy = get_strategy(strategy_id)
    spec = strategy.experiment_variables.get(variable)
    if spec is None:
        raise ValueError(f"'{variable}' is not an experimentable variable of {strategy_id}")
    new_value = strategy._validate_param(variable, new_value)

    store = get_store()
    active = store.list("strategy_versions",
                        filters={"strategy_id": strategy_id, "active": True}, limit=1)
    current = active[0]["params"][variable] if active else strategy.base_params[variable]

    hyp = store.create("hypotheses", {
        "userId": user_id,
        "hypothesis_id": next_hypothesis_id(),
        "strategy_id": strategy_id,
        "strategy_name": strategy.short_name,
        "strategy_version": active[0]["version"] if active else strategy.version,
        "variable": variable,
        "old_value": current,
        "new_value": new_value,
        "reason": reason,
        "expected_effect": expected_effect,
        "dataset": dataset or {"market": "XAUUSD", "timeframe": "15M"},
        "source_lesson": source_lesson,
        "status": "PROPOSED",
        "user_decision": None,
    }, doc_id=None)
    store.update("hypotheses", hyp["id"], {"hypothesis_id": hyp["hypothesis_id"]})
    return store.get("hypotheses", hyp["id"])


def propose_from_lessons(user_id: str, max_new: int = 2) -> List[Dict[str, Any]]:
    """Reviews evidence-backed lessons and proposes ONE-variable hypotheses.

    Only lessons with sufficient evidence become hypotheses. Mapping from
    observation to variable is deliberately conservative and transparent.
    """
    store = get_store()
    lessons = store.list("lessons", filters={"userId": user_id}, limit=60)
    lessons = [l for l in lessons if l.get("evidence", 0) >= settings.min_trades_for_lesson]
    lessons.sort(key=lambda l: abs(l.get("delta_pp", 0)), reverse=True)

    proposed: List[Dict[str, Any]] = []
    seen_vars = set()
    for lesson in lessons:
        if len(proposed) >= max_new:
            break
        strategy_id = lesson["strategy_id"]
        segment = lesson.get("segment", "")
        strategy = get_strategy(strategy_id)

        # skip segments we already proposed a hypothesis for
        already = store.list("hypotheses", filters={"source_lesson": lesson["id"]}, limit=1)
        if already:
            continue

        candidates = []
        if strategy_id == "strategy_2_ema_atr":
            if "volatility:low" in segment:
                candidates.append(dict(
                    variable="sl_mult",
                    new_value=float(strategy.base_params["sl_mult"]) + 0.3,
                    reason=("Underperforming signals cluster in low-volatility regimes where the "
                            "1.5x ATR stop may sit too close to entry; test whether a wider stop "
                            "reduces premature stop-outs."),
                    effect="Fewer SL-first exits in low-volatility conditions."))
            candidates.append(dict(
                variable="fast_len",
                new_value=int(strategy.base_params["fast_len"]) + 1,
                reason=("Test whether a slightly slower fast EMA reduces low-quality "
                        "crossovers for the observed segment."),
                effect="Fewer, cleaner crossover signals."))
        elif strategy_id == "strategy_1_zero_lag":
            if "volatility:high" in segment:
                candidates.append(dict(
                    variable="band_mult",
                    new_value=round(float(strategy.base_params["band_mult"]) - 0.1, 2),
                    reason=("Signals for this segment cluster in high-volatility regimes; test "
                            "whether slightly tighter volatility bands improve selection."),
                    effect="Stricter trend confirmation in volatile conditions."))
            candidates.append(dict(
                variable="length",
                new_value=int(strategy.base_params["length"]) + 5,
                reason=("Test whether a longer Zero-Lag EMA length improves signal quality "
                        "for the observed segment."),
                effect="Smoother trend state, fewer marginal entries."))

        for cand in candidates[:2]:   # each hypothesis = exactly ONE variable
            if len(proposed) >= max_new:
                return proposed
            key = (strategy_id, cand["variable"])
            if key in seen_vars:
                continue
            seen_vars.add(key)
            proposed.append(create_hypothesis(
                user_id, strategy_id, cand["variable"], cand["new_value"],
                reason=cand["reason"], expected_effect=cand["effect"],
                dataset={"market": "XAUUSD", "timeframe": "15M"},
                source_lesson=lesson["id"],
            ))
    return proposed
