"""INDEPENDENT RECONSTRUCTION (owner brief 2026-10-08, section 5).

Every candidate stores a full traceability chain:

    SOURCE  ->  EXTRACTED RULE  ->  IMPLEMENTED RULE

The IMPLEMENTED RULE is the deterministic interpreter program FOREXMIND will
actually run (normalized conditions, explicit SL/TP evaluation, explicit
costs). It is built FROM the extracted rules - never from the source's
claimed performance - so reviewers can see exactly what was tested.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .extract import OPS, INDICATORS, SL_TYPES, TP_TYPES


def _impl_condition(c: Dict[str, Any]) -> Dict[str, Any]:
    left = {"indicator": c["left"]["indicator"],
            **({"params": c["left"].get("params", {})} if c["left"].get("params") else {})}
    out: Dict[str, Any] = {"op": c["op"], "left": left}
    if c.get("op") not in ("rising", "falling"):
        right = c.get("right") or {}
        if "indicator" in right:
            out["right"] = {"indicator": right["indicator"],
                            **({"params": right.get("params", {})} if right.get("params") else {})}
        else:
            out["right"] = {"value": right.get("value")}
    if c.get("op") in ("rising", "falling"):
        out["lookback"] = int(c.get("lookback", 1))
    return out


def reconstruct(rules: Dict[str, Any]) -> Dict[str, Any]:
    """Build the implemented-rule program + a human-readable trace. Raises
    nothing: any problem is returned as status UNRECONSTRUCTABLE with reason."""
    if not _rule_ok(rules.get("entry")):
        return {"status": "UNRECONSTRUCTABLE",
                "reason": "entry block missing or uses unsupported operators"}
    entry = {"side": "BUY" if rules.get("direction") in ("BUY", "BOTH") else "SELL",
             "when": [_impl_condition(c) for c in rules["entry"]["when"]],
             "all": bool(rules["entry"].get("all", True))}
    if rules.get("direction") == "BOTH":
        entry["mirror_side"] = "SELL"

    sl = _impl_stop(rules.get("sl"))
    tp = _impl_target(rules.get("tp"))
    if sl is None:
        return {"status": "UNRECONSTRUCTABLE",
                "reason": "no interpretable stop-loss rule (fixed_pips | atr_mult)"}
    exit_when = [_impl_condition(c) for c in (rules.get("exit") or {}).get("when", [])
                 ] if rules.get("exit") else []
    for c in exit_when:
        if c["op"] not in OPS:
            return {"status": "UNRECONSTRUCTABLE",
                    "reason": f"exit rule uses unsupported op {c['op']!r}"}

    implemented = {
        "engine": "research.rule_interp.v1",       # deterministic interpreter
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "exit_when": exit_when,
        "costs": {"spread_pips": None, "commission_per_lot": 7.0,
                  "slippage_pips": 0.3},           # stamped at backtest time
        "evaluation": "closed bars only; entry next-bar open; first-touch "
                      "SL-priority intrabar; no lookahead",
    }
    trace = {
        "source": {"source_id": rules.get("source_id"), "url": rules.get("source_url"),
                   "author": rules.get("author"),
                   "evidence_tier": rules.get("evidence_tier")},
        "extracted": {k: rules.get(k) for k in
                      ("market", "timeframe", "direction", "entry", "exit", "sl", "tp",
                       "filters", "indicators", "params")},
        "implemented": implemented,
    }
    return {"status": "OK", "implemented": implemented, "trace": trace}


def _rule_ok(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    conds = block.get("when")
    if not isinstance(conds, list) or not conds:
        return False
    for c in conds:
        if not isinstance(c, dict) or c.get("op") not in OPS:
            return False
        left = c.get("left") or {}
        if left.get("indicator") not in INDICATORS:
            return False
        if c.get("op") in ("greater", "less", "crosses_above", "crosses_below"):
            right = c.get("right") or {}
            if ("indicator" not in right or right["indicator"] not in INDICATORS) \
                    and not isinstance(right.get("value"), (int, float)):
                return False
    return True


def _impl_stop(sl: Any) -> Optional[Dict[str, Any]]:
    """Normalize a stop-loss rule. Returns None when nothing interpretable."""
    if not isinstance(sl, dict):
        return None
    t = sl.get("type")
    if t == "fixed_pips" and isinstance(sl.get("value"), (int, float)) and sl["value"] > 0:
        return {"type": "fixed_pips", "pips": float(sl["value"])}
    if t == "atr_mult" and isinstance(sl.get("mult"), (int, float)) and sl["mult"] > 0:
        return {"type": "atr_mult", "mult": float(sl["mult"]),
                "period": int(sl.get("period", 14))}
    if t == "structure_level" and isinstance(sl.get("lookback"), int):
        return {"type": "structure_level", "lookback": int(sl["lookback"])}
    return None


def _impl_target(tp: Any) -> Optional[Dict[str, Any]]:
    """Normalize a take-profit rule (single target or ladder). None = exit by rule/time."""
    if not isinstance(tp, dict):
        return None
    t = tp.get("type")
    if t == "fixed_pips" and isinstance(tp.get("value"), (int, float)) and tp["value"] > 0:
        return {"type": "fixed_pips", "pips": float(tp["value"])}
    if t == "rr_multiple" and isinstance(tp.get("value"), (int, float)) and tp["value"] > 0:
        return {"type": "rr_multiple", "value": float(tp["value"])}
    if t == "atr_mult" and isinstance(tp.get("mult"), (int, float)) and tp["mult"] > 0:
        return {"type": "atr_mult", "mult": float(tp["mult"]), "period": int(tp.get("period", 14))}
    return None
