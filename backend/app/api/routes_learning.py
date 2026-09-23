"""Learning Lab routes - lessons, hypotheses, experiments (SPEC §21-§26, §51)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..agent import core as agent_core
from ..learning.analysis import analyze_closed_signals
from ..learning.experiments import ExperimentError
from ..models.schemas import ExperimentIn, HypothesisIn
from ..notifications.service import notify
from ..state import State
from .deps import get_user_id

router = APIRouter(tags=["learning"])


@router.get("/lessons")
def lessons(user_id: str = Depends(get_user_id), limit: int = Query(50, le=200)):
    items = State.store.list("lessons", filters={"userId": user_id}, limit=limit)
    return {"lessons": items, "demo": State.provider.is_demo}


@router.get("/hypotheses")
def hypotheses(user_id: str = Depends(get_user_id), status: str = Query("all")):
    filters = {"userId": user_id}
    if status != "all":
        filters["status"] = status
    items = State.store.list("hypotheses", filters=filters, limit=100)
    return {"hypotheses": items, "demo": State.provider.is_demo}


@router.post("/hypotheses")
def create_hypothesis(body: HypothesisIn, user_id: str = Depends(get_user_id)):
    from ..learning.hypotheses import create_hypothesis as create
    try:
        h = create(user_id, body.strategy_id, body.variable, body.new_value,
                   reason=body.reason, expected_effect=body.expected_effect,
                   dataset=body.dataset)
    except ValueError as e:
        raise HTTPException(422, str(e))
    agent_core.log(f"New hypothesis {h['hypothesis_id']}: {body.variable} "
                   f"-> {body.new_value} on {body.strategy_id}", kind="HYPOTHESIS")
    return {"hypothesis": h}


@router.post("/hypotheses/{hypothesis_id}/approve")
def approve(hypothesis_id: str, user_id: str = Depends(get_user_id)):
    """USER APPROVAL ONLY -> creates a new strategy version (SPEC §26, §41)."""
    from ..learning.versions import approve_hypothesis
    try:
        version = approve_hypothesis(user_id, hypothesis_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    notify(user_id, "STRATEGY_VERSION_UPDATED", "Strategy version updated",
           f"{version['strategy_id']} is now v{version['version']}. "
           f"Change: {version['note']}")
    agent_core.log(f"User approved {hypothesis_id}. New version "
                   f"v{version['version']} created for {version['strategy_id']}.",
                   kind="VERSION")
    return {"version": version}


@router.post("/hypotheses/{hypothesis_id}/reject")
def reject(hypothesis_id: str, user_id: str = Depends(get_user_id)):
    from ..learning.versions import reject_hypothesis
    try:
        h = reject_hypothesis(user_id, hypothesis_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    agent_core.log(f"User rejected {hypothesis_id}. Change NOT applied.",
                   kind="VERSION")
    return {"hypothesis": h}


@router.get("/experiments")
def experiments(user_id: str = Depends(get_user_id), limit: int = Query(50, le=200)):
    items = State.store.list("experiments", filters={"userId": user_id}, limit=limit)
    return {"experiments": items, "demo": State.provider.is_demo}


@router.get("/experiments/{experiment_id}")
def experiment_detail(experiment_id: str, user_id: str = Depends(get_user_id)):
    e = State.store.get("experiments", experiment_id)
    if not e or e.get("userId") != user_id:
        raise HTTPException(404, "Experiment not found")
    return {"experiment": e}


@router.post("/experiments")
def run_experiment(body: ExperimentIn, user_id: str = Depends(get_user_id)):
    """Runs the ONE-VARIABLE experiment for a hypothesis. Hard validation at
    the backend (SPEC §28)."""
    hyp = State.store.get("hypotheses", body.hypothesis_id)
    if not hyp or hyp.get("userId") != user_id:
        raise HTTPException(404, "Hypothesis not found")
    if hyp.get("status") not in ("PROPOSED", "CONCLUDED"):
        raise HTTPException(409, f"Hypothesis is {hyp['status']}")
    try:
        exp = State.experiments.run_from_hypothesis(user_id, hyp)
    except ExperimentError as e:
        raise HTTPException(422, str(e))
    if exp.get("status") == "READY_FOR_REVIEW" and (
            exp.get("recommend_approval") or exp.get("overfitting_risk")):
        warn = " HIGH OVERFITTING RISK detected - do not approve without further evidence." \
               if exp.get("overfitting_risk") else ""
        notify(user_id, "APPROVAL_REQUIRED",
               f"Experiment {exp.get('experiment_code', '')} READY FOR REVIEW",
               f"{hyp['hypothesis_id']}: {exp['variable']} {exp['old_value']} -> "
               f"{exp['new_value']} on the same dataset. Verdict: "
               f"{exp['result']}.{warn} Review it in Learning Lab.",
               meta={"hypothesis_id": hyp["id"]})
        agent_core.log(f"Experiment {exp.get('experiment_code')} for "
                       f"{hyp['hypothesis_id']} is READY FOR REVIEW: "
                       f"{exp.get('result')}. Awaiting user decision.",
                       kind="EXPERIMENT")
    else:
        agent_core.log(f"Experiment for {hyp['hypothesis_id']} completed: "
                       f"{exp.get('result')}.", kind="EXPERIMENT")
    return {"experiment": exp}


@router.post("/learning/analyze")
def run_analysis(user_id: str = Depends(get_user_id)):
    new_lessons = analyze_closed_signals(user_id)
    return {"new_lessons": len(new_lessons), "lessons": new_lessons}

@router.get("/autopsies")
def autopsies(user_id: str = Depends(get_user_id), limit: int = Query(50, le=200)):
    items = State.store.list("autopsies", filters={"user_id": user_id}, limit=limit)
    items.sort(key=lambda d: str(d.get("createdAt") or ""), reverse=True)
    return {"autopsies": items, "count": len(items)}


@router.get("/autopsies/{autopsy_id}")
def autopsy_detail(autopsy_id: str, user_id: str = Depends(get_user_id)):
    a = State.store.get("autopsies", autopsy_id)
    if not a or a.get("user_id") != user_id:
        raise HTTPException(404, "Autopsy not found")
    return {"autopsy": a}


@router.post("/autopsies/{autopsy_id}/decide")
def autopsy_decide(autopsy_id: str, body: dict, user_id: str = Depends(get_user_id)):
    """User decision on a PROPOSED experiment: approve -> PAPER shadow
    experiment (zero live behavior change), reject -> closed. The engine can
    NEVER apply anything without this explicit call."""
    from ..learning.autopsy import decide_proposal
    approve = bool(body.get("approve"))
    out = decide_proposal(autopsy_id, user_id, approve)
    agent_core.log(f"Trade autopsy {autopsy_id[:8]}: user "
                   f"{'APPROVED paper experiment' if approve else 'REJECTED the proposal'}.",
                   kind="VERSION")
    return out


@router.get("/autopsies/experiment/{experiment_id}/report")
def autopsy_experiment_report(experiment_id: str, user_id: str = Depends(get_user_id)):
    """Baseline vs experiment groups (complete picture, never win-rate-only)."""
    from ..learning.autopsy import experiment_report
    return experiment_report(experiment_id)
