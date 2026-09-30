"""Strategy version control (SPEC §26, §27, §41, §52, §53).

* The AI can NEVER apply a strategy change automatically (absolute rule).
* Every approved change creates a new immutable version (v1.0 -> v1.1 ...).
* Every previous version is retained: view / compare / rollback.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..db.store import get_store
from ..strategies import get_strategy


# Retired strategies (user directive 2026-09-30): Zero Lag replaced by the
# ChartPrime Volume Profile + Pivot strategy. The module stays registered so
# signal history, replay and the executor still resolve old signals - but any
# FRESH registry doc is created DISABLED so the engine never scans it. The
# live doc is flipped once (audited PATCH) at deploy time.
RETIRED_STRATEGIES = {"strategy_1_zero_lag": "DISABLED"}


def _bump(version: str) -> str:

    major, minor = version.split(".")
    return f"{major}.{int(minor) + 1}"


def ensure_strategy_docs() -> None:
    """Registers every strategy module in the database with its v1.0."""
    store = get_store()
    from ..strategies import all_strategies
    for sid, strategy in all_strategies().items():
        doc = store.list("strategies", filters={"id": sid}, limit=1)
        if not doc:
            store.create("strategies", {
                "id": sid,
                "name": strategy.name,
                "short_name": strategy.short_name,
                "description": strategy.description,
                "status": RETIRED_STRATEGIES.get(sid, "ACTIVE"),
                                              # ACTIVE | PAUSED | DISABLED
                "active_version": strategy.version,
            }, doc_id=sid)
            store.create("strategy_versions", {
                "strategy_id": sid,
                "version": strategy.version,
                "params": dict(strategy.base_params),
                "changes": [],
                "hypothesis_id": None,
                "experiment_id": None,
                "active": True,
                "note": "Original version",
            }, doc_id=f"{sid}-v{strategy.version}")
        else:
            active = store.list("strategy_versions",
                                filters={"strategy_id": sid, "active": True}, limit=1)
            if not active:
                store.create("strategy_versions", {
                    "strategy_id": sid,
                    "version": strategy.version,
                    "params": dict(strategy.base_params),
                    "changes": [],
                    "hypothesis_id": None,
                    "experiment_id": None,
                    "active": True,
                    "note": "Original version (restored)",
                }, doc_id=f"{sid}-v{strategy.version}")


def active_params(strategy_id: str) -> Dict[str, Any]:
    store = get_store()
    active = store.list("strategy_versions",
                        filters={"strategy_id": strategy_id, "active": True}, limit=1)
    if active:
        return dict(active[0]["params"])
    return dict(get_strategy(strategy_id).base_params)


def active_version(strategy_id: str) -> str:
    store = get_store()
    active = store.list("strategy_versions",
                        filters={"strategy_id": strategy_id, "active": True}, limit=1)
    return active[0]["version"] if active else get_strategy(strategy_id).version


def approve_hypothesis(user_id: str, hypothesis_id: str) -> Dict[str, Any]:
    """USER APPROVAL -> new strategy version (SPEC §26)."""
    store = get_store()
    hyp = store.get("hypotheses", hypothesis_id)
    if not hyp:
        raise ValueError("Hypothesis not found")
    if hyp.get("user_decision"):
        raise ValueError(f"Hypothesis already decided ({hyp['user_decision']})")
    strategy_id = hyp["strategy_id"]

    current_version = active_version(strategy_id)
    new_version = _bump(current_version)
    params = active_params(strategy_id)
    old_value = params.get(hyp["variable"])
    params[hyp["variable"]] = hyp["new_value"]

    version_doc = store.create("strategy_versions", {
        "strategy_id": strategy_id,
        "version": new_version,
        "params": params,
        "changes": [{"variable": hyp["variable"],
                     "old_value": old_value,
                     "new_value": hyp["new_value"]}],
        "hypothesis_id": hypothesis_id,
        "experiment_id": hyp.get("experiment_id"),
        "active": True,
        "note": f"{hyp['variable']}: {old_value} -> {hyp['new_value']} "
                f"(approved {hyp['hypothesis_id']})",
    })
    # deactivate previous versions of this strategy
    for v in store.list("strategy_versions", filters={"strategy_id": strategy_id}):
        if v["id"] != version_doc["id"]:
            store.update("strategy_versions", v["id"], {"active": False})
    store.update("strategies", strategy_id, {
        "active_version": new_version,
        "status": store.get("strategies", strategy_id).get("status", "ACTIVE"),
    })
    store.update("hypotheses", hypothesis_id, {
        "status": "APPROVED", "user_decision": "APPROVED",
        "applied_version": new_version,
    })
    return store.get("strategy_versions", version_doc["id"])


def set_param_direct(user_id: str, strategy_id: str, variable: str,
                     value: Any) -> Dict[str, Any]:
    """USER-initiated parameter change from the Strategy Manager UI.

    Human-set = human-approved (SPEC: the AI can never apply a strategy
    change automatically). Same immutability contract as hypothesis
    approval: a NEW active version is created, the previous ones retained
    and rollback-able, and the change is audited (P17)."""
    from ..core.permissions import audit
    strat = get_strategy(strategy_id)
    spec = (getattr(strat, "experiment_variables", {}) or {}).get(variable)
    if spec is None:
        raise KeyError(f"unknown variable '{variable}'")
    kind = spec.get("type")
    if kind == "select":
        opts = list(spec.get("options") or [])
        if value not in opts:
            raise ValueError(f"{variable} must be one of {opts}")
    else:
        try:
            value = int(value) if kind == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{variable} must be a number")
        if "min" in spec and value < spec["min"]:
            raise ValueError(f"{variable} below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ValueError(f"{variable} above maximum {spec['max']}")
    store = get_store()
    params = active_params(strategy_id)
    old_value = params.get(variable)
    if old_value == value:
        raise ValueError(f"{variable} is already {value}")
    params[variable] = value
    new_version = _bump(active_version(strategy_id))
    doc = store.create("strategy_versions", {
        "strategy_id": strategy_id,
        "version": new_version,
        "params": params,
        "changes": [{"variable": variable,
                     "old_value": old_value,
                     "new_value": value}],
        "hypothesis_id": None,
        "experiment_id": None,
        "active": True,
        "note": f"{variable}: {old_value} -> {value} (user setting)",
    })
    for v in store.list("strategy_versions", filters={"strategy_id": strategy_id}):
        if v["id"] != doc["id"]:
            store.update("strategy_versions", v["id"], {"active": False})
    sdoc = store.get("strategies", strategy_id)
    store.update("strategies", strategy_id, {
        "active_version": new_version,
        "status": (sdoc or {}).get("status", "ACTIVE"),
    })
    audit(user_id, "strategy.param.set", None,
          {"strategy_id": strategy_id, variable: old_value},
          {"strategy_id": strategy_id, variable: value},
          f"user set {variable} on {strategy_id}")
    return doc


def reject_hypothesis(user_id: str, hypothesis_id: str) -> Dict[str, Any]:
    """REJECT: change NOT applied; hypothesis retained (SPEC §26)."""
    store = get_store()
    hyp = store.get("hypotheses", hypothesis_id)
    if not hyp:
        raise ValueError("Hypothesis not found")
    if hyp.get("user_decision"):
        raise ValueError(f"Hypothesis already decided ({hyp['user_decision']})")
    store.update("hypotheses", hypothesis_id, {
        "status": "REJECTED", "user_decision": "REJECTED",
    })
    return store.get("hypotheses", hypothesis_id)


def rollback(strategy_id: str, target_version: Optional[str] = None) -> Dict[str, Any]:
    """Roll back to a previous version; history is never destroyed (SPEC §53)."""
    store = get_store()
    versions = store.list("strategy_versions", filters={"strategy_id": strategy_id})
    if target_version is None:
        sorted_vs = sorted(versions, key=lambda v: (v["version"]))
        actives = [v for v in sorted_vs if v.get("active")]
        idx = sorted_vs.index(actives[-1]) if actives else len(sorted_vs) - 1
        if idx <= 0:
            raise ValueError("Already at the original version - nothing to roll back to.")
        target = sorted_vs[idx - 1]
    else:
        matches = [v for v in versions if v["version"] == target_version]
        if not matches:
            raise ValueError(f"Version {target_version} not found")
        target = matches[0]
    for v in versions:
        store.update("strategy_versions", v["id"], {"active": v["id"] == target["id"]})
    store.update("strategies", strategy_id, {"active_version": target["version"]})
    store.create("agent_activity", {
        "userId": None, "kind": "STRATEGY_VERSION_UPDATED",
        "message": f"{strategy_id} rolled back to v{target['version']} "
                   "(previous versions retained)",
    })
    return target


def compare_versions(strategy_id: str, version_a: str, version_b: str,
                     backtester) -> Dict[str, Any]:
    """SPEC §52: compare two versions on the SAME current dataset."""
    store = get_store()
    va = [v for v in store.list("strategy_versions", filters={"strategy_id": strategy_id})
          if v["version"] == version_a]
    vb = [v for v in store.list("strategy_versions", filters={"strategy_id": strategy_id})
          if v["version"] == version_b]
    if not va or not vb:
        raise ValueError("Version not found")
    market, timeframe = "XAUUSD", "15M"
    ra = backtester.run(None, strategy_id, market, timeframe, params=va[0]["params"], save=False)
    rb = backtester.run(None, strategy_id, market, timeframe, params=vb[0]["params"], save=False)
    from ..learning.metrics import compute_metrics
    diffs = []
    pa, pb = va[0]["params"], vb[0]["params"]
    for k in pa:
        if pa[k] != pb.get(k):
            diffs.append({"variable": k, "old": pa[k], "new": pb.get(k)})
    return {
        "strategy_id": strategy_id,
        "a": {"version": version_a, "params": va[0]["params"],
              "metrics": compute_metrics(ra.get("trades", []))},
        "b": {"version": version_b, "params": vb[0]["params"],
              "metrics": compute_metrics(rb.get("trades", []))},
        "changed": diffs,
        "dataset": ra.get("dataset"),
    }
