"""EVIDENCE REPORT (owner brief 2026-10-08, sections 13-14).

The user reviews EVIDENCE, not process. Each candidate gets one report:
strategy, source evidence, exact reconstructed rules, historical test,
out-of-sample, walk-forward, stress, regime analysis, shadow results,
risks, and a recommendation:

    REJECT | CONTINUE_RESEARCH | SHADOW | READY_FOR_REVIEW

READY_FOR_REVIEW is the ceiling - the engine can never make a strategy
live. The existing human-approval lifecycle (versions.py) stays the only
path to LIVE, unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

SHADOW_MIN_TRADES = 20
SHADOW_PWIN_BAND_PP = 15.0     # shadow P(win) may drift at most this many points


def _risks(candidate: Dict[str, Any], validation: Dict[str, Any],
           backtest: Dict[str, Any], shadow: Dict[str, Any]) -> List[str]:
    risks: List[str] = []
    tier = candidate.get("evidence_tier")
    if tier == 3:
        risks.append("Source is Tier 3 (idea discovery) - the claim itself carries no weight; "
                     "only FOREXMIND's own evidence counts.")
    m = backtest.get("metrics") or {}
    if (m.get("trade_count") or 0) < 60:
        risks.append(f"Small sample: {m.get('trade_count')} verified trades - "
                     "statistics remain fragile.")
    pf = m.get("profit_factor")
    if pf is not None and pf < 1.3:
        risks.append(f"Thin profit factor ({pf}) - costs can erase the edge.")
    for g in validation:
        if g.get("verdict") == "MARGINAL":
            risks.append(f"{g.get('gate')}: only marginal stability ({g.get('detail')}).")
    if (m.get("max_losing_streak") or 0) >= 6:
        risks.append(f"Longest verified losing streak: {m.get('max_losing_streak')} trades.")
    regimes = ((candidate.get("regime_analysis") or {}).get("detail") or "")
    if "single" in regimes:
        risks.append("Edge concentrated in one market regime - regime risk is real.")
    if shadow and (shadow.get("n") or 0) > 0 and (shadow.get("n") or 0) < SHADOW_MIN_TRADES:
        risks.append(f"Shadow sample still small ({shadow.get('n')}/{SHADOW_MIN_TRADES}).")
    return risks or ["No specific additional risks identified - all gates behaved."]


def recommend(validation: List[Dict[str, Any]], backtest: Dict[str, Any],
              shadow: Optional[Dict[str, Any]],
              shadow_probs: Optional[Dict[str, Any]],
              stage: Optional[str] = None) -> Dict[str, Any]:
    if stage == "REJECTED":
        return {"recommendation": "REJECT",
                "reason": "engine rejected the candidate during validation "
                          "(see stage events)"}
    verdicts = [g.get("verdict") for g in validation]
    m = backtest.get("metrics") or {}
    if any(v == "FAIL" for v in verdicts):
        why = next(g["detail"] for g in validation if g.get("verdict") == "FAIL")
        return {"recommendation": "REJECT", "reason": why}
    if m.get("trade_count", 0) < 30:
        return {"recommendation": "REJECT",
                "reason": f"only {m.get('trade_count')} verified trades - "
                          "below the statistical floor"}
    if any(v == "INSUFFICIENT_DATA" for v in verdicts):
        return {"recommendation": "CONTINUE_RESEARCH",
                "reason": "one or more gates lack data - collect more history"}
    if any(v == "MARGINAL" for v in verdicts):
        return {"recommendation": "CONTINUE_RESEARCH",
                "reason": "walk-forward stability is marginal"}
    # all gates PASS -> shadow decides the rest
    if not shadow or (shadow.get("n") or 0) < SHADOW_MIN_TRADES:
        return {"recommendation": "SHADOW",
                "reason": f"historical gates PASS - collecting forward shadow "
                          f"evidence ({(shadow or {}).get('n') or 0}/"
                          f"{SHADOW_MIN_TRADES} hypothetical trades)"}
    sp = (shadow_probs or {}).get("P_win") or {}
    bp = _backtest_pwin(backtest)
    if sp.get("status") == "INSUFFICIENT_DATA":
        return {"recommendation": "SHADOW", "reason": "shadow probabilities not yet estimable"}
    drift = abs(100 * (sp.get("probability", 0) - bp))
    if (shadow.get("avg_r") or 0) <= 0:
        return {"recommendation": "CONTINUE_RESEARCH",
                "reason": f"shadow expectancy is {(shadow.get('avg_r') or 0)}R - "
                          "forward behavior diverged from the backtest"}
    if drift > SHADOW_PWIN_BAND_PP:
        return {"recommendation": "CONTINUE_RESEARCH",
                "reason": f"shadow P(win) drifted {round(drift, 1)}pp from the "
                          "backtest - not stable forward"}
    return {"recommendation": "READY_FOR_REVIEW",
            "reason": f"all historical gates PASS and shadow confirms "
                      f"({shadow.get('n')} trades, avg {shadow.get('avg_r')}R, "
                      f"P(win) drift {round(drift, 1)}pp) - awaiting HUMAN APPROVAL"}


def _backtest_pwin(backtest: Dict[str, Any]) -> float:
    m = backtest.get("metrics") or {}
    return (m.get("win_rate") or 0) / 100.0


def build(candidate: Dict[str, Any], backtest: Dict[str, Any],
          validation: List[Dict[str, Any]], probabilities: Dict[str, Any],
          shadow: Optional[Dict[str, Any]] = None,
          shadow_probs: Optional[Dict[str, Any]] = None,
          stage: Optional[str] = None) -> Dict[str, Any]:
    from .validate import overall
    rec = recommend(validation, backtest, shadow, shadow_probs, stage=stage)
    if stage == "REJECTED":
        for ev in reversed(candidate.get("events") or []):
            if ev.get("stage") == "REJECTED":
                rec = {"recommendation": "REJECT", "reason": ev.get("detail", "")}
                break
    from .sources import tier_name
    return {
        "strategy": {"name": candidate.get("name"), "candidate_id": candidate.get("id"),
                     "source_id": candidate.get("source_id"),
                     "source_url": candidate.get("source_url"),
                     "author": candidate.get("author"),
                     "market": candidate.get("market"),
                     "timeframe": candidate.get("timeframe")},
        "source_evidence": {
            "tier": candidate.get("evidence_tier"),
            "tier_name": tier_name(candidate.get("evidence_tier") or 0),
            "source_claim": candidate.get("source_claim"),
            "disclaimer": "SOURCE CLAIM != FOREXMIND VERIFIED RESULT - "
                          "everything below was independently reconstructed and tested",
        },
        "rules": candidate.get("trace") or candidate.get("implemented") or {},
        "historical_test": backtest.get("metrics"),
        "out_of_sample": next((g for g in validation if g["gate"] == "OOS"), None),
        "walk_forward": next((g for g in validation if g["gate"] == "WALK_FORWARD"), None),
        "stress_testing": [g for g in validation if g["gate"] in
                           ("MONTE_CARLO", "COST_SENSITIVITY")],
        "generalization": next((g for g in validation
                                if g["gate"] == "GENERALIZATION"), None),
        "parameter_stability": next((g for g in validation
                                     if g["gate"] == "PARAM_STABILITY"), None),
        "regime_analysis": candidate.get("regime_analysis"),
        "probabilities": probabilities,
        "shadow_results": shadow,
        "shadow_probabilities": shadow_probs,
        "risks": _risks(candidate, validation, backtest, shadow or {}),
        "recommendation": rec["recommendation"],
        "recommendation_reason": rec["reason"],
        "evidence_level": overall(validation),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "human_approval_required": True,
        "note": "The engine can NEVER make this live. READY_FOR_REVIEW only "
                "queues it for the owner's explicit approval.",
    }
