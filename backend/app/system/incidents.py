"""INCIDENT CENTER (3.0 spec sections 48-49).

Centralized incidents with lifecycle DETECTED -> ACKNOWLEDGED -> INVESTIGATING
-> RESOLVED (+ postmortem). Detection sweeps derive from real signals only:
bridge reachability, degradation INVESTIGATION states, CONTRADICTED evidence,
TP-audit leaks. AI INCIDENT COMMANDER investigates via ONE bounded router call
and produces ADVICE-ONLY reports - it never changes infrastructure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def raise_incident(user_id: str, kind: str, subject: str, severity: str,
                   detail: str, dedupe: bool = True) -> Optional[dict]:
    from ..db.store import get_store
    store = get_store()
    try:
        if dedupe:
            open_docs = [i for i in store.list("incidents", filters={"userId": user_id},
                                               limit=100)
                         if i.get("kind") == kind and i.get("subject") == subject
                         and i.get("status") not in ("RESOLVED",)]
            if open_docs:
                store.update("incidents", open_docs[0]["id"],
                             {"last_seen_at": _now(),
                              "detail": detail,
                              "occurrences": int(open_docs[0].get("occurrences") or 1) + 1})
                return store.get("incidents", open_docs[0]["id"])
        return store.create("incidents", {
            "userId": user_id, "kind": str(kind)[:40], "subject": str(subject)[:80],
            "severity": severity if severity in ("CRITICAL", "WARNING", "INFO") else "WARNING",
            "detail": str(detail)[:400], "status": "DETECTED",
            "occurrences": 1, "createdAt": _now(), "last_seen_at": _now(),
            "postmortem": None})
    except Exception:
        return None


def sweep(user_id: str, bridge_probe: Optional[bool] = None) -> dict:
    """Run all detectors. bridge_probe: None=probe now, True/False=precomputed."""
    out = {"raised": 0}
    from ..db.store import get_store
    store = get_store()

    # 1. bridge reachability
    if bridge_probe is None:
        try:
            from ..execution.mt5 import bridge_get
            bridge_probe = bool(bridge_get("/account", timeout=4))
        except Exception:
            bridge_probe = False
    if bridge_probe is False:
        if raise_incident(user_id, "MT5_BRIDGE", "bridge", "CRITICAL",
                          "MT5 bridge unreachable during sweep"):
            out["raised"] += 1

    # 2. strategy degradation INVESTIGATION
    try:
        from ..learning.health import strategy_health
        for sid in ("strategy_1_zero_lag", "strategy_2_ema_atr"):
            h = strategy_health(user_id, sid)
            if h["state"] == "INVESTIGATION":
                if raise_incident(user_id, "STRATEGY_DEGRADATION", sid, "WARNING",
                                  h["note"]):
                    out["raised"] += 1
    except Exception:
        pass

    # 3. CONTRADICTED evidence
    try:
        from ..evidence import engine
        for ev in engine.list_evidence(user_id):
            if ev.get("state") == "CONTRADICTED":
                if raise_incident(user_id, "EVIDENCE_CONTRADICTED", ev["subject_id"],
                                  "WARNING", ev["conclusion"]):
                    out["raised"] += 1
    except Exception:
        pass

    # 4. TP-audit leaks (existing audit findings)
    try:
        docs = store.list("incidents", filters={"userId": user_id, "kind": "TP_LEAK"},
                          limit=5)
        _ = docs  # TP audit already raises real incidents in its own path
    except Exception:
        pass
    return out


def investigate_with_ai(user_id: str, incident_id: str, router=None) -> Optional[dict]:
    """AI INCIDENT COMMANDER - ADVICE ONLY (spec section 49)."""
    from ..db.store import get_store
    store = get_store()
    inc = store.get("incidents", incident_id)
    if not inc or inc.get("userId") != user_id:
        return None
    snapshot: Dict[str, Any] = {"incident": {k: inc.get(k) for k in
                                             ("kind", "subject", "severity", "detail")}}
    try:
        from ..execution.mt5 import bridge_get
        acct = bridge_get("/account", timeout=4)
        snapshot["bridge"] = {"reachable": bool(acct),
                              "login": (acct or {}).get("login")}
    except Exception as exc:
        snapshot["bridge"] = {"reachable": False, "error": type(exc).__name__}
    try:
        h = store.get("settings", "killswitch") or {}
        snapshot["killswitch_level"] = h.get("level", 0)
    except Exception:
        pass
    recent_exec = store.list("exec_events", filters={"userId": user_id}, limit=30)
    snapshot["recent_exec_failures"] = [
        {"stage": e.get("stage"), "detail": (e.get("detail") or "")[:80]}
        for e in recent_exec if e.get("stage") in ("FAILED", "EXPIRED")][-5:]

    try:
        if router is None:
            from ..agent.router import get_router
            router = get_router()
        res = router.analyze(
            "You are the incident commander. Given the infrastructure snapshot, "
            "reply ONLY JSON: {\"probable_cause\": \"...\", \"evidence\": [\"...\"], "
            "\"affected\": [\"...\"], \"recommended_action\": \"...\", "
            "\"safe\": true}. You investigate and advise ONLY - you cannot and "
            "must not change infrastructure.",
            snapshot, escalate=False, user_id=user_id)
        import json
        blob = {}
        try:
            txt = res.get("text", "")
            blob = json.loads(txt[txt.find("{"):txt.rfind("}") + 1] or "{}")
        except Exception:
            blob = {"probable_cause": "commander_output_unparseable",
                    "safe": True}
        report = {"probable_cause": str(blob.get("probable_cause", ""))[:200],
                  "evidence": [str(e)[:160] for e in (blob.get("evidence") or [])][:5],
                  "affected": [str(e)[:80] for e in (blob.get("affected") or [])][:5],
                  "recommended_action": str(blob.get("recommended_action", ""))[:300],
                  "advice_only": True, "at": _now()}
        store.update("incidents", incident_id,
                     {"commander_report": report, "status": inc.get("status", "DETECTED")})
        return report
    except Exception as exc:
        return {"error": type(exc).__name__, "advice_only": True}
