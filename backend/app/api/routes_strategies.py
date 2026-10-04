"""Strategy Manager & version control routes (SPEC §8, §12, §27, §47, §52, §53)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..learning import versions as vc
from ..state import State
from ..strategies import all_strategies, get_strategy
from .deps import get_user_id

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("")
def list_strategies(user_id: str = Depends(get_user_id)):
    store = State.store
    from ..learning.versions import RETIRED_STRATEGIES
    out = []
    for sid, strat in all_strategies().items():
        if sid in RETIRED_STRATEGIES:
            continue          # retired: signal history stays, UI never shows it
        doc = store.list("strategies", filters={"id": sid}, limit=1)
        doc = doc[0] if doc else {"id": sid, "status": "ACTIVE",
                                  "active_version": strat.version}
        md = strat.get_metadata()
        # Friendly names live on the strategy class; older stored docs predate
        # them and rendered blank rows on the Strategies screen (2026-10-01).
        # doc wins over md (stored truth), md fills any gaps.
        out.append({**md, **doc, "id": sid, "metadata": md,
                    "lifecycle": vc.lifecycle_of(sid),
                    "active_params": vc.active_params(sid),
                    "experiment_variables": strat.experiment_variables})
    return {"strategies": out}


@router.get("/{strategy_id}")
def strategy_detail(strategy_id: str, user_id: str = Depends(get_user_id)):
    try:
        strat = get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, "Unknown strategy")
    store = State.store
    doc = store.list("strategies", filters={"id": strategy_id}, limit=1)
    return {"strategy": {**get_strategy(strategy_id).get_metadata(), **(doc[0] if doc else {})},
            "metadata": strat.get_metadata(),
            "active_params": vc.active_params(strategy_id),
            "active_version": vc.active_version(strategy_id)}


@router.patch("/{strategy_id}/params")
def set_strategy_param(strategy_id: str, body: dict, user_id: str = Depends(get_user_id)):
    """User-initiated strategy parameter change (e.g. entry_mode).
    Validated against the strategy's experiment_variables, versioned and
    audited - the user IS the approver (SPEC: AI never applies changes)."""
    variable = body.get("variable")
    if not variable:
        raise HTTPException(422, "variable is required")
    try:
        doc = vc.set_param_direct(user_id, strategy_id, variable, body.get("value"))
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"version": doc["version"], "params": doc["params"],
            "changes": doc["changes"]}


@router.patch("/{strategy_id}")
def set_status(strategy_id: str, body: dict, user_id: str = Depends(get_user_id)):
    """Update strategy status and/or per-strategy sessions (P7).
    Every change is audited (P17). Activating a recommendation's draft =
    explicitly passing its `proposed_sessions` here - never automatic."""
    from ..config import SESSIONS
    from ..core.permissions import audit
    status = body.get("status")
    sessions = body.get("sessions", "__absent__")
    if status is not None and status not in ("ACTIVE", "PAUSED", "DISABLED"):
        raise HTTPException(422, "status must be ACTIVE, PAUSED or DISABLED")
    if sessions != "__absent__" and sessions is not None:
        if (not isinstance(sessions, list) or not sessions or
                any(x not in SESSIONS for x in sessions)):
            raise HTTPException(422, f"sessions must be a non-empty list from {list(SESSIONS)}")
    if status is None and sessions == "__absent__":
        raise HTTPException(422, "nothing to update")
    store = State.store
    doc = store.list("strategies", filters={"id": strategy_id}, limit=1)
    if not doc:
        raise HTTPException(404, "Unknown strategy")
    prev = {"status": doc[0].get("status"), "sessions": doc[0].get("sessions")}
    patch = {}
    if status is not None:
        patch["status"] = status
    if sessions != "__absent__":
        patch["sessions"] = sessions
        store.update("strategies", strategy_id,
                     {"proposed_sessions": None, "proposed_change_note": None})
    out = store.update("strategies", strategy_id, patch)
    audit(user_id, "strategy.update", None, prev, patch, f"strategy {strategy_id}")
    return {"strategy": out}


@router.get("/{strategy_id}/versions")
def list_versions(strategy_id: str, user_id: str = Depends(get_user_id)):
    try:
        get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, "Unknown strategy")
    versions = State.store.list("strategy_versions",
                                filters={"strategy_id": strategy_id})
    versions.sort(key=lambda v: tuple(int(x) for x in v["version"].split(".")))
    return {"versions": versions}


@router.post("/{strategy_id}/rollback")
def rollback(strategy_id: str, body: dict = None, user_id: str = Depends(get_user_id)):
    body = body or {}
    if not body.get("confirm"):
        raise HTTPException(
            428, "Rollback requires explicit confirmation "
                 "(body: {\"confirm\": true, \"target_version\": \"x.y\"}). "
                 "The active version stays unchanged.")
    versions = State.store.list("strategy_versions",
                                filters={"strategy_id": strategy_id})
    from_v = next((v["version"] for v in versions if v.get("active")
                   and v.get("version")), None)
    try:
        target = vc.rollback(strategy_id, body.get("target_version"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    State.store.create("version_events", {
        "userId": user_id, "kind": "ROLLBACK", "strategy_id": strategy_id,
        "from_version": from_v, "to_version": target["version"],
        "reason": body.get("reason") or None,
        "at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    })
    return {"rolled_back": True, "rolled_back_to": target,
            "from_version": from_v, "to_version": target["version"],
            "note": "History was never destroyed - previous versions are retained."}


@router.get("/{strategy_id}/compare")
def compare(strategy_id: str, a: str = Query(...), b: str = Query(...),
            user_id: str = Depends(get_user_id)):
    try:
        result = vc.compare_versions(strategy_id, a, b, State.backtester)
    except ValueError as e:
        raise HTTPException(404, str(e))
    result["demo"] = State.provider.is_demo
    return result


@router.post("/{strategy_id}/backtest")
def backtest(strategy_id: str, body: dict = None,
             user_id: str = Depends(get_user_id)):
    body = body or {}
    try:
        result = State.backtester.run(
            user_id, strategy_id, body.get("market", "XAUUSD"),
            body.get("timeframe", "15M"), params=body.get("params"),
            label=body.get("label", "manual"))
    except KeyError:
        raise HTTPException(404, "Unknown strategy")
    if not result.get("ok"):
        raise HTTPException(503, result.get("error", "Backtest unavailable"))
    result["trades"] = result["trades"][-100:]
    result["demo"] = State.provider.is_demo
    return result
