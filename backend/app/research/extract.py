"""STRATEGY EXTRACTION (owner brief 2026-10-08, section 4).

Turn a discovered strategy into EXPLICIT, testable rules - or mark it
INSUFFICIENT RULES. Missing rules are never invented to make a strategy
testable; ambiguity is a research result, not an obstacle to engineer around.
"""
from __future__ import annotations

from typing import Any, Dict, List

# fields the interpreter genuinely needs to reconstruct + test
REQUIRED_RULE_FIELDS = {
    "market": "market/instrument",
    "timeframe": "timeframe",
    "entry": "entry rules",
    "direction": "direction (BUY/SELL/both)",
}
OPTIONAL_RULE_FIELDS = {
    "exit": "exit rules",
    "sl": "stop-loss rules",
    "tp": "take-profit rules",
    "filters": "filters",
    "indicators": "indicators",
    "params": "parameters",
    "reported_performance": "reported performance (SOURCE CLAIM)",
    "limitations": "research limitations",
}

# condition schema the deterministic interpreter understands (section 5)
OPS = {"crosses_above", "crosses_below", "greater", "less", "rising", "falling"}
INDICATORS = {"close", "open", "high", "low", "ema", "sma", "rsi", "atr",
              "highest", "lowest", "body", "range"}
SL_TYPES = {"fixed_pips", "atr_mult", "structure_level"}
TP_TYPES = {"fixed_pips", "atr_mult", "rr_multiple", "structure_level"}


def _condition_valid(c: Dict[str, Any]) -> bool:
    if not isinstance(c, dict):
        return False
    if c.get("op") not in OPS:
        return False
    left = c.get("left") or {}
    if left.get("indicator") not in INDICATORS:
        return False
    if c.get("op") in ("rising", "falling"):
        return True
    right = c.get("right") or {}
    return ("indicator" in right and right["indicator"] in INDICATORS) or \
        ("value" in right and isinstance(right["value"], (int, float)))


def _rule_block_valid(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    conds = block.get("when")
    return isinstance(conds, list) and bool(conds) and all(_condition_valid(c) for c in conds)


def extract(source_ref: Dict[str, Any], raw_rules: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize extracted rules.

    source_ref: {source_id, url, author, evidence_tier, claimed: {...}}
    raw_rules:  the candidate rule dict as read from the source material.

    Returns {status: OK | INSUFFICIENT_RULES, rules | missing} - the stored
    form always separates SOURCE CLAIM fields from anything FOREXMIND tests.
    """
    from .sources import classify
    cls = classify(source_ref.get("url") or "", source_ref.get("source_id"))
    rules: Dict[str, Any] = {
        "name": str(raw_rules.get("name") or source_ref.get("name") or "unnamed strategy"),
        "source_id": source_ref.get("source_id") or cls["source_id"],
        "source_url": source_ref.get("url") or "",
        "author": source_ref.get("author") or "unknown",
        "evidence_tier": cls["tier"],
        "evidence_tier_name": cls["tier_name"],
        "market": raw_rules.get("market"),
        "timeframe": raw_rules.get("timeframe"),
        "direction": raw_rules.get("direction"),
        "entry": raw_rules.get("entry"),
        "exit": raw_rules.get("exit"),
        "sl": raw_rules.get("sl"),
        "tp": raw_rules.get("tp"),
        "filters": raw_rules.get("filters") or [],
        "indicators": raw_rules.get("indicators") or [],
        "params": raw_rules.get("params") or {},
        "reported_performance": raw_rules.get("reported_performance") or None,
        "limitations": raw_rules.get("limitations") or [],
        "source_claim": {
            "text": source_ref.get("claim") or (raw_rules.get("reported_performance") or {}).get("summary"),
            "disclaimer": "SOURCE CLAIM - not a FOREXMIND verified result",
        },
    }
    missing: List[str] = []
    for k, label in REQUIRED_RULE_FIELDS.items():
        v = rules.get(k)
        if k == "entry" and not _rule_block_valid(v):
            missing.append("entry")
        elif k != "entry" and (v is None or v == ""):
            missing.append(label)
    if rules.get("direction") not in ("BUY", "SELL", "BOTH"):
        missing.append("direction must be BUY | SELL | BOTH")
    if missing:
        return {"status": "INSUFFICIENT_RULES", "missing": missing,
                "note": "rules are NOT invented - the candidate cannot be "
                        "reconstructed until the source documents them",
                "rules": rules}
    return {"status": "OK", "rules": rules, "missing": []}
