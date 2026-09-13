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
    out = []
    for sid, strat in all_strategies().items():
        doc = store.list("strategies", filters={"id": sid}, limit=1)
        doc = doc[0] if doc else {"id": sid, "status": "ACTIVE",
                                  "active_version": strat.version}
        out.append({**doc, "metadata": strat.get_metadata(),
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
    return {"strategy": doc[0] if doc else {}, "metadata": strat.get_metadata(),
            "active_params": vc.active_params(strategy_id),
            "active_version": vc.active_version(strategy_id)}


@router.patch("/{strategy_id}")
def set_status(strategy_id: str, body: dict, user_id: str = Depends(get_user_id)):
    status = body.get("status")
    if status not in ("ACTIVE", "PAUSED", "DISABLED"):
        raise HTTPException(422, "status must be ACTIVE, PAUSED or DISABLED")
    store = State.store
    doc = store.list("strategies", filters={"id": strategy_id}, limit=1)
    if not doc:
        raise HTTPException(404, "Unknown strategy")
    return {"strategy": store.update("strategies", strategy_id, {"status": status})}


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
    try:
        target = vc.rollback(strategy_id, (body or {}).get("target_version"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"rolled_back_to": target}


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
