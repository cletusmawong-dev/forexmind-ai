"""Audit trail (SS46): every AI decision attempt is recorded - model used,
snapshot time, decision, confidence, reason codes, gate verdict, actual MT5
actions and outcome. Stored in the `ai_decisions` collection. Never raises:
auditing must not break trading (and vice versa)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..db.store import get_store

INPUT_CONTEXT_VERSION = 1   # bump when the evidence pack layout changes


def record(user_id: Optional[str], position: dict, trigger: str,
           model: str, layer: str, decision: Optional[dict],
           decision_valid: bool, gate_verdict: str,
           execution: Optional[dict] = None, error: Optional[str] = None,
           snapshot_ts: Optional[float] = None) -> Optional[dict]:
    try:
        doc = {
            "userId": user_id,
            "position_ticket": position.get("ticket"),
            "symbol": position.get("app_market") or position.get("symbol"),
            "signal_id": position.get("signal_id"),
            "trigger": trigger,
            "model": model,
            "layer": layer,
            "decision": ({k: decision.get(k) for k in
                          ("action", "continuation_assessment", "next_target",
                           "confidence", "reason_codes", "risk_state",
                           "recommended_sl", "partial_fraction",
                           "escalation_required")} if decision else None),
            "decision_valid": decision_valid,
            "gate_verdict": gate_verdict,
            "execution": execution or {},
            "error": error,
            "input_context_version": INPUT_CONTEXT_VERSION,
            "snapshot_ts": snapshot_ts,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        return get_store().create("ai_decisions", doc)
    except Exception:
        return None
