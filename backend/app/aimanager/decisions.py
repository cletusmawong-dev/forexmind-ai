"""AI decision schema + validator (SS18, SS20).

The AI must answer with STRUCTURED JSON. Free-form text is never executed.
Validation is total: any violation -> the decision is rejected and the
deterministic fallback (HOLD) applies.

Actions are management-only. There is deliberately no BUY/SELL/ENTRY action:
the strategy is the only entry authority (SS9).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

ACTIONS = ("HOLD", "PROTECT", "PARTIAL_PROFIT", "EXIT")
ASSESSMENTS = ("STRONG", "WEAKENING", "REVERSING", "UNCERTAIN")
RISK_STATES = ("CONTROLLED", "ELEVATED", "CRITICAL")
TARGETS = ("TP1", "TP2", "TP3")

_DECISION_TTL_S = 90   # SS20: decisions expire; the gate enforces freshness


class DecisionError(ValueError):
    """Raised with a human-readable reason when an AI decision is invalid."""


def parse_json_blob(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from model text (tolerates code fences
    and prose around the blob). Raises DecisionError when none parses."""
    if not text or not str(text).strip():
        raise DecisionError("empty response")
    t = str(text).strip()
    # strip ```json ... ``` fences if present
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end <= start:
            raise DecisionError("no JSON object found in response")
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except Exception as exc:
        raise DecisionError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DecisionError("decision is not a JSON object")
    return data


def validate(raw: str) -> Dict[str, Any]:
    """Parse + fully validate an AI decision. Returns the normalized dict.
    Raises DecisionError on any violation - the caller then applies HOLD."""
    data = parse_json_blob(raw)

    action = str(data.get("action", "")).strip().upper()
    if action not in ACTIONS:
        raise DecisionError(f"action must be one of {ACTIONS}, got {action!r}")

    assessment = str(data.get("continuation_assessment", "UNCERTAIN")).strip().upper()
    if assessment not in ASSESSMENTS:
        raise DecisionError(f"continuation_assessment must be one of {ASSESSMENTS}")

    try:
        confidence = float(data.get("confidence", 0.5))
    except Exception:
        raise DecisionError("confidence must be a number")
    if not (0.0 <= confidence <= 1.0):
        raise DecisionError("confidence must be within 0..1 (an evidence score, "
                            "NOT a probability - SS37)")

    codes = data.get("reason_codes")
    if not isinstance(codes, list) or not codes:
        raise DecisionError("reason_codes must be a non-empty list (SS36: evidence required)")
    clean_codes = []
    for c in codes:
        cs = re.sub(r"[^A-Za-z0-9_\- ]", "", str(c)).strip().upper()[:48]
        if cs:
            clean_codes.append(cs)
    if not clean_codes:
        raise DecisionError("reason_codes empty after sanitization")

    risk_state = str(data.get("risk_state", "CONTROLLED")).strip().upper()
    if risk_state not in RISK_STATES:
        raise DecisionError(f"risk_state must be one of {RISK_STATES}")

    sl = data.get("recommended_sl", None)
    if sl is not None:
        try:
            sl = float(sl)
        except Exception:
            raise DecisionError("recommended_sl must be a number or null")
        if sl <= 0:
            raise DecisionError("recommended_sl must be positive")
        if action not in ("PROTECT", "HOLD"):
            raise DecisionError("recommended_sl is only legal with PROTECT/HOLD")

    frac = data.get("partial_fraction", None)
    if action == "PARTIAL_PROFIT":
        try:
            frac = float(frac)
        except Exception:
            raise DecisionError("PARTIAL_PROFIT requires partial_fraction")
        if not (0.0 < frac < 1.0):
            raise DecisionError("partial_fraction must be strictly between 0 and 1")
    elif frac is not None:
        frac = None     # ignored outside PARTIAL_PROFIT

    target = data.get("next_target", None)
    if target is not None:
        target = str(target).strip().upper()
        if target not in TARGETS:
            target = None

    return {
        "action": action,
        "continuation_assessment": assessment,
        "next_target": target,
        "confidence": round(confidence, 3),
        "reason_codes": clean_codes[:8],
        "risk_state": risk_state,
        "recommended_sl": sl,
        "partial_fraction": frac,
        "escalation_required": bool(data.get("escalation_required", False)),
        "raw": data,
    }


HOLD = Dict[str, Any]


def deterministic_hold(reason: str) -> HOLD:
    """The only decision the system may invent without the AI (SS44)."""
    return {
        "action": "HOLD", "continuation_assessment": "UNCERTAIN",
        "next_target": None, "confidence": 0.0,
        "reason_codes": ["AI_UNAVAILABLE_DETERMINISTIC_HOLD"],
        "risk_state": "CONTROLLED", "recommended_sl": None,
        "partial_fraction": None, "escalation_required": False,
        "raw": {"deterministic": True, "reason": reason},
    }
