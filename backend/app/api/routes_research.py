"""FOREXMIND 3.0 research/intelligence APIs (stages 5, 6, 8 + stage 2/3 reads).

Every route is user-scoped (existing get_user_id dependency), read-only over
the user's own records unless explicitly stated, and every research
computation is labeled (SIMULATION / SYNTHETIC / association-not-causation).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..api.deps import get_user_id
from ..db.store import get_store

router = APIRouter(tags=["research3"])


class StrategyBody(BaseModel):
    strategy_id: str


class AskBody(BaseModel):
    question: str


class DecisionBody(BaseModel):
    note: str = ""


class DebateBody(BaseModel):
    subject_type: str
    subject_id: str
    question: str


# ---------------------------------------------------------------- Stage 2/3
@router.get("/research/fingerprint/{market}")
def fingerprint(market: str, user_id: str = Depends(get_user_id)):
    from ..ai.world_model import build_world_model
    from ..ai.fingerprint import build_fingerprint
    try:
        world = build_world_model(user_id, market)
    except Exception as exc:
        raise HTTPException(503, f"world model unavailable: {type(exc).__name__}")
    fp = build_fingerprint(world)
    fp["note"] = ("snapshot of what the system knew at build time - not a "
                  "prediction; missing inputs are null, never guessed")
    return fp


@router.get("/learning/strategy-dna/{strategy_id}")
def strategy_dna(strategy_id: str, user_id: str = Depends(get_user_id)):
    from ..learning.perf_dna import build_dna
    return build_dna(user_id, strategy_id)


@router.post("/ai/debate")
def debate(body: DebateBody, user_id: str = Depends(get_user_id)):
    """Structured multi-role AI debate (event-driven; also on explicit
    request). Advice/interpretation only - epistemically labeled."""
    from ..ai.debate import run_debate
    from ..evidence import engine
    evidence = None
    if body.subject_type.startswith("strategy"):
        try:
            sid, mkt = (body.subject_id.split(":") + [None])[:2]
            if sid and mkt:
                evidence = engine.build_evidence(user_id, sid, mkt, persist=False)
        except Exception:
            evidence = None
    doc = run_debate(user_id, body.subject_type, body.subject_id, body.question,
                     {"evidence": evidence})
    if not doc:
        raise HTTPException(503, "debate could not run (router unavailable)")
    return doc


# ---------------------------------------------------------------- Stage 4
@router.get("/risk/tca")
def tca(user_id: str = Depends(get_user_id)):
    from ..execution.tca import tca_report
    return tca_report(user_id)


@router.get("/risk/exposure")
def exposure(user_id: str = Depends(get_user_id)):
    from ..risk.exposure import exposure_snapshot
    return exposure_snapshot(user_id)


@router.get("/risk/adaptive")
def adaptive(market: str = Query("EURUSD"), user_id: str = Depends(get_user_id)):
    from ..risk.exposure import adaptive_risk_suggestion
    return adaptive_risk_suggestion(user_id, market)


# ---------------------------------------------------------------- Stage 5
@router.get("/research/summary")
def research_summary(user_id: str = Depends(get_user_id)):
    """Multiple-testing control (spec 29): the full research context."""
    store = get_store()
    exps = store.list("experiments", filters={"userId": user_id}, limit=500)
    # old experiment docs may carry result as a string / combinations as
    # anything - aggregation must never 500 on legacy shapes
    def _is_passed(e):
        res = e.get("result")
        return isinstance(res, dict) and bool(res.get("passed"))
    def _combos(e):
        c = e.get("combinations")
        return len(c) if isinstance(c, list) else 1
    passed = [e for e in exps if _is_passed(e)]
    combos = sum(_combos(e) for e in exps)
    return {"experiments_total": len(exps),
            "experiments_passed": len(passed),
            "experiments_failed": len(exps) - len(passed),
            "parameter_combinations_tested": combos,
            "hypotheses_total": store.count("hypotheses"),
            "context": ("a single successful result is meaningless without this "
                        "multiple-testing context"),
            "multiple_testing_note": (
                f"best-of-{len(exps)} experiments over ~{combos} combinations"
                if exps else "no experiments yet")}


@router.post("/research/montecarlo")
def montecarlo(body: StrategyBody, user_id: str = Depends(get_user_id)):
    from ..research.montecarlo import simulate
    store = get_store()
    rs = [s.get("r_multiple") for s in store.list("signals", filters={"userId": user_id},
                                                  limit=2000)
          if s.get("completed") and s.get("strategy_id") == body.strategy_id
          and s.get("r_multiple") is not None]
    return simulate([float(r) for r in rs])


@router.post("/research/stress")
def stress(body: StrategyBody, user_id: str = Depends(get_user_id)):
    from ..research.stresslab import run_stress
    return run_stress(body.strategy_id)


@router.get("/research/calibration")
def calibration(user_id: str = Depends(get_user_id)):
    from ..research.calibration import calibration_report
    return calibration_report(user_id)


@router.get("/research/shadow")
def shadow_list(user_id: str = Depends(get_user_id)):
    store = get_store()
    docs = store.list("shadow_trades", filters={"userId": user_id}, limit=100)
    docs.sort(key=lambda d: str(d.get("createdAt")), reverse=True)
    strats = [d["id"] for d in store.list("strategies", limit=10) if d.get("shadow")]
    return {"shadow_trades": docs, "strategies_in_shadow": strats,
            "note": "hypothetical research trades - never sent to the broker"}


@router.post("/research/shadow/run")
def shadow_run(user_id: str = Depends(get_user_id)):
    from ..research.shadow import run_shadow_cycle
    return run_shadow_cycle(user_id)


# ---------------------------------------------------------------- Stage 6
@router.get("/research/replay/{signal_ref}")
def replay(signal_ref: str, user_id: str = Depends(get_user_id)):
    from ..research.replay import decision_replay
    r = decision_replay(user_id, signal_ref)
    if not r:
        raise HTTPException(404, "signal not found")
    return r


@router.get("/research/timemachine/{signal_ref}")
def timemachine(signal_ref: str, window: int = Query(24),
                user_id: str = Depends(get_user_id)):
    from ..research.replay import time_machine
    r = time_machine(user_id, signal_ref, window=max(4, min(int(window), 96)))
    if not r:
        raise HTTPException(404, "signal not found")
    return r


@router.get("/research/counterfactuals/{signal_ref}")
def counterfactuals(signal_ref: str, user_id: str = Depends(get_user_id)):
    from ..research.counterfactual import counterfactuals
    return counterfactuals(user_id, signal_ref)


@router.get("/research/blocked")
def blocked(user_id: str = Depends(get_user_id)):
    from ..research.blocked import blocked_analysis
    return blocked_analysis(user_id)


@router.get("/research/why-blocked/{signal_ref}")
def why_blocked(signal_ref: str, user_id: str = Depends(get_user_id)):
    from ..research.blocked import why_blocked
    r = why_blocked(user_id, signal_ref)
    if not r:
        raise HTTPException(404, "no blocked signal with this id")
    return r


# ---------------------------------------------------------------- Stage 8
@router.get("/knowledge/graph")
def knowledge_graph(user_id: str = Depends(get_user_id)):
    from ..research.knowledge import graph
    return graph(user_id)


@router.get("/knowledge/query")
def knowledge_query(strategy_id: Optional[str] = Query(None),
                    market: Optional[str] = Query(None),
                    session: Optional[str] = Query(None),
                    regime: Optional[str] = Query(None),
                    user_id: str = Depends(get_user_id)):
    from ..research.knowledge import query
    return query(user_id, strategy_id, market, session, regime)


@router.post("/research/ask")
def research_ask(body: AskBody, user_id: str = Depends(get_user_id)):
    from ..research.nl import ask
    return ask(user_id, body.question)


# ---------------------------------------------------------------- Stage 7
@router.get("/incidents")
def incidents_list(user_id: str = Depends(get_user_id)):
    from ..system.incidents import sweep
    store = get_store()
    sweep(user_id)                      # cheap detectors, deduped
    docs = store.list("incidents", filters={"userId": user_id}, limit=100)
    docs.sort(key=lambda d: (d.get("severity") != "CRITICAL",
                             str(d.get("last_seen_at")),), reverse=False)
    docs.sort(key=lambda d: str(d.get("last_seen_at") or ""), reverse=True)
    return {"incidents": docs,
            "open": sum(1 for d in docs if d.get("status") != "RESOLVED")}


class IncidentAction(BaseModel):
    note: Optional[str] = None
    confirm: bool = False


def _load_own_incident(user_id: str, incident_id: str) -> dict:
    doc = get_store().get("incidents", incident_id)
    if not doc or doc.get("userId") != user_id:
        raise HTTPException(404, "incident not found")
    return doc


@router.post("/incidents/{incident_id}/ack")
def incident_ack(incident_id: str, user_id: str = Depends(get_user_id)):
    from ..api.routes_admin import audit
    doc = _load_own_incident(user_id, incident_id)
    get_store().update("incidents", incident_id, {"status": "ACKNOWLEDGED"})
    audit(user_id, "incident.ack", incident_id,
          {"status": doc.get("status")}, {"status": "ACKNOWLEDGED"}, "")
    return {"id": incident_id, "status": "ACKNOWLEDGED"}


@router.post("/incidents/{incident_id}/investigate")
def incident_investigate(incident_id: str, user_id: str = Depends(get_user_id)):
    from ..system.incidents import investigate_with_ai
    _load_own_incident(user_id, incident_id)
    get_store().update("incidents", incident_id, {"status": "INVESTIGATING"})
    report = investigate_with_ai(user_id, incident_id)
    if not report:
        raise HTTPException(503, "commander unavailable")
    return report


@router.post("/incidents/{incident_id}/resolve")
def incident_resolve(incident_id: str, body: IncidentAction,
                     user_id: str = Depends(get_user_id)):
    from ..api.routes_admin import audit
    doc = _load_own_incident(user_id, incident_id)
    get_store().update("incidents", incident_id,
                       {"status": "RESOLVED", "postmortem": body.note,
                        "resolved_at": __import__("datetime").datetime
                       .now(__import__("datetime").timezone.utc).isoformat()})
    audit(user_id, "incident.resolve", incident_id,
          {"status": doc.get("status")}, {"status": "RESOLVED"}, body.note or "")
    return {"id": incident_id, "status": "RESOLVED"}


# ------------------------------------------------- Research Intelligence (2026-10-08)
@router.get("/research/engine/sources")
def research_sources(user_id: str = Depends(get_user_id)):
    """The controlled Research Source Registry (tiers + allowed purposes)."""
    from ..research.sources import list_sources, tier_name
    return {"sources": list_sources(get_store()),
            "tiers": {1: tier_name(1), 2: tier_name(2), 3: tier_name(3)},
            "policy": "external claims are recorded as claims - never "
                      "presented as FOREXMIND VERIFIED RESULTS"}


_REC_RANK = {"READY_FOR_REVIEW": 0, "SHADOW": 1, "CONTINUE_RESEARCH": 2,
             "REJECT": 3}
_EVID_RANK = {"STRONG": 0, "MIXED": 1, "WEAK": 2, "INSUFFICIENT": 3}


@router.get("/research/engine/candidates")
def research_candidates(stage: Optional[str] = Query(None),
                        sort: str = Query("recent"),
                        limit: int = Query(100, le=200),
                        user_id: str = Depends(get_user_id)):
    """Candidate strategies through the pipeline (dashboard list view).

    sort=evidence compares candidates: serious recommendations first, then
    evidence level, then expectancy - the stable ones rank above the lucky.
    """
    store = get_store()
    from ..research.engine import COLLECTION
    rows = store.list(COLLECTION, limit=limit)
    if stage:
        rows = [r for r in rows if r.get("stage") == stage]
    if sort == "evidence":
        rows.sort(key=lambda c: (
            _REC_RANK.get((c.get("report") or {}).get("recommendation"), 4),
            _EVID_RANK.get(c.get("evidence_level")
                           or (c.get("report") or {}).get("evidence_level"), 4),
            -(((((c.get("backtest") or {}).get("metrics") or {}).get("avg_r"))
               or 0.0)),
            c.get("updatedAt") or "", ), reverse=False)
    else:
        rows.sort(key=lambda c: c.get("updatedAt") or c.get("createdAt") or "",
                  reverse=True)
    return {"count": len(rows), "candidates": [{
        "id": c.get("id"), "name": c.get("name"), "stage": c.get("stage"),
        "market": c.get("market"), "timeframe": c.get("timeframe"),
        "source_id": c.get("source_id"), "source_url": c.get("source_url"),
        "evidence_tier": c.get("evidence_tier"),
        "evidence_level": c.get("evidence_level"),
        "recommendation": (c.get("report") or {}).get("recommendation"),
        "recommendation_reason": (c.get("report") or {}).get("recommendation_reason"),
        "verified_trades": ((c.get("backtest") or {}).get("metrics") or {}).get("trade_count"),
        "shadow_trades": (c.get("shadow") or {}).get("n", 0),
        "updatedAt": c.get("updatedAt"),
    } for c in rows]}


@router.get("/research/engine/candidates/{candidate_id}")
def research_candidate_detail(candidate_id: str,
                              user_id: str = Depends(get_user_id)):
    """Full candidate: trace, rules, backtest, validation, report."""
    store = get_store()
    from ..research.engine import COLLECTION
    rows = store.list(COLLECTION, filters={"id": candidate_id}, limit=1)
    if not rows:
        raise HTTPException(404, "candidate not found")
    c = rows[0]
    return {"candidate": c}


@router.get("/research/engine/candidates/{candidate_id}/report")
def research_candidate_report(candidate_id: str,
                              user_id: str = Depends(get_user_id)):
    """The evidence report (the thing a human reviews)."""
    store = get_store()
    from ..research.engine import COLLECTION
    rows = store.list(COLLECTION, filters={"id": candidate_id}, limit=1)
    if not rows:
        raise HTTPException(404, "candidate not found")
    rep = rows[0].get("report")
    if not rep:
        raise HTTPException(409, "no evidence report yet - engine still working")
    return {"report": rep}


@router.get("/research/engine/memory")
def research_memory(limit: int = Query(50, le=200),
                    user_id: str = Depends(get_user_id)):
    """Research Memory: what was already tested, verdicts + reasons."""
    from ..research import memory
    return memory.summary(get_store(), limit=limit)


@router.post("/research/engine/tick")
def research_engine_tick(user_id: str = Depends(get_user_id)):
    """Manual bounded tick (the background loop runs this automatically)."""
    from ..research.engine import tick
    return tick()

@router.post("/research/engine/candidates/{candidate_id}/submit-approval")
def research_submit_approval(candidate_id: str,
                             user_id: str = Depends(get_user_id)):
    """Queue a READY_FOR_REVIEW candidate for the owner's decision."""
    from ..research import approval
    try:
        return approval.submit_for_approval(candidate_id)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 409, str(e))


@router.post("/research/engine/candidates/{candidate_id}/approve")
def research_approve(candidate_id: str, body: DecisionBody,
                     user_id: str = Depends(get_user_id)):
    """HUMAN APPROVAL ONLY -> immutable v1.0 snapshot. The strategy is NOT
    made live by this call (controlled deployment is a separate step)."""
    from ..research import approval
    from ..api.routes_admin import audit
    try:
        res = approval.approve_candidate(user_id, candidate_id, body.note or "")
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 409, str(e))
    audit(user_id, "research.approve", candidate_id,
          {"stage": "READY_FOR_REVIEW"}, res, body.note or "")
    return res


@router.post("/research/engine/candidates/{candidate_id}/reject")
def research_reject(candidate_id: str, body: DecisionBody,
                    user_id: str = Depends(get_user_id)):
    """HUMAN REJECTION - recorded with the reason in research memory."""
    from ..research import approval
    from ..api.routes_admin import audit
    if not (body.note or "").strip():
        raise HTTPException(422, "a rejection reason is required")
    try:
        res = approval.reject_candidate(user_id, candidate_id, body.note)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 409, str(e))
    audit(user_id, "research.reject", candidate_id,
          {"stage": "READY_FOR_REVIEW"}, res, body.note)
    return res
