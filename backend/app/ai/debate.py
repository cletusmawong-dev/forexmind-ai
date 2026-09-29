"""AI DEBATE MODE (3.0 spec section 8) - structured multi-role analysis.

ANALYSTS -> EVIDENCE ENGINE -> CRITIC -> FINAL SYNTHESIS.
Event-driven only (spec section 64): normal monitoring stays cheap; debates run
on meaningful research events (INVESTIGATION health, CONTRADICTED evidence) or
explicit admin request. One bounded router call per role, never per candle.
Arguments are stored on a debate doc (debates collection) - traceable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROLES = ("MARKET_ANALYST", "STRATEGY_ANALYST", "RISK_ANALYST",
         "EXECUTION_ANALYST", "RESEARCH_ANALYST")

ROLE_BRIEF = {
    "MARKET_ANALYST": "Assess the MARKET dimension: regime, structure, volatility, session, news. What does the market data support or contradict?",
    "STRATEGY_ANALYST": "Assess the STRATEGY dimension: historical behavior, parameter sensitivity, sample size, regime fit. What does performance data support or contradict?",
    "RISK_ANALYST": "Assess the RISK dimension: drawdown, exposure, losing streaks, worst case. What risks are being underweighted?",
    "EXECUTION_ANALYST": "Assess the EXECUTION dimension: slippage, latency, spread, rejects, broker behavior. Could execution quality explain the result?",
    "RESEARCH_ANALYST": "Assess the RESEARCH dimension: methodology, lookahead bias, multiple-testing, out-of-sample gaps. How reliable is the evidence?",
}

CRITIC_BRIEF = (
    "You are the CRITIC. Given the analyst arguments and the deterministic "
    "evidence view, identify: the strongest points of agreement, the genuine "
    "contradictions, what is still unknown, and what would change the "
    "conclusion. Be adversarial toward unfounded certainty."
)


def _parse_json_block(text: str) -> dict:
    import json
    try:
        return json.loads(text[text.find("{"):text.rfind("}") + 1] or "{}")
    except Exception:
        return {}


def run_debate(user_id: str, subject_type: str, subject_id: str,
               question: str, context: dict, router=None,
               store=None) -> Optional[dict]:
    """Run one structured debate. NEVER raises; returns the debate doc."""
    try:
        if router is None:
            from .router import get_router
            router = get_router()
        if store is None:
            from ..db.store import get_store
            store = get_store()

        args: List[dict] = []
        for role in ROLES:
            try:
                res = router.analyze(
                    f"{ROLE_BRIEF[role]} Reply with ONLY JSON: "
                    '{"position": "SUPPORT|CONTRADICT|UNCERTAIN", "points": ["..."]}',
                    {"question": question, "subject": f"{subject_type}:{subject_id}",
                     "data": context, "role": role},
                    escalate=False, user_id=user_id)
                blob = _parse_json_block(res.get("text", ""))
                args.append({
                    "role": role,
                    "position": str(blob.get("position", "UNCERTAIN")).upper()[:16],
                    "points": [str(p)[:200] for p in (blob.get("points") or [])][:5],
                    "model": res.get("model", "primary"),
                })
            except Exception as exc:
                args.append({"role": role, "position": "UNCERTAIN",
                             "points": [f"analyst_unavailable:{type(exc).__name__}"],
                             "model": "none"})

        # deterministic evidence view joins the debate (EVIDENCE before CRITIC)
        evidence_view = context.get("evidence") if isinstance(context, dict) else None

        critic_blob = {}
        try:
            res = router.analyze(
                CRITIC_BRIEF + ' Reply with ONLY JSON: {"agreements": ["..."], '
                '"contradictions": ["..."], "unknowns": ["..."], '
                '"would_change_conclusion_if": ["..."], "synthesis": "..."}',
                {"question": question, "arguments": args,
                 "deterministic_evidence": evidence_view},
                escalate=False, user_id=user_id)
            critic_blob = _parse_json_block(res.get("text", ""))
        except Exception as exc:
            critic_blob = {"synthesis": f"critic_unavailable:{type(exc).__name__}"}

        now = datetime.now(timezone.utc).isoformat()
        return store.create("debates", {
            "userId": user_id, "subject_type": subject_type,
            "subject_id": subject_id, "question": str(question)[:400],
            "arguments": args, "evidence_view": evidence_view,
            "critic": {k: critic_blob.get(k) for k in
                       ("agreements", "contradictions", "unknowns",
                        "would_change_conclusion_if", "synthesis")},
            "epistemic": "INTERPRETATION - analyst reasoning, not established fact",
            "createdAt": now,
        })
    except Exception as exc:
        try:
            (store or get_store()).create("debates", {
                "userId": user_id, "subject_type": subject_type,
                "subject_id": subject_id, "question": str(question)[:400],
                "error": type(exc).__name__, "createdAt": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass
        return None
