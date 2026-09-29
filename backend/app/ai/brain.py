"""AI Brain 2.0 reasoning pipeline (Stage 2).

    OBSERVE   freshness audit of the world model (deterministic)
    GENERATE  one structured model call (ModelRouter: primary -> escalation)
    CHALLENGE devil's-advocate pass on any CONCRETE action (self-challenge)
    DECIDE    evidence-confidence % + one of the honest answers:
                 ACTION | NO_ACTION | INSUFFICIENT_EVIDENCE | DATA_STALE |
                 CONFLICTING
    (the CALLER then runs the UNCHANGED deterministic risk gate - the brain
     never executes anything)

Hard rules:
  * DATA_STALE / INSUFFICIENT_EVIDENCE are answered WITHOUT inventing actions;
  * a failed self-challenge downgrades the decision to CONFLICTING (no action);
  * confidence_pct is a DETERMINISTIC blend (60% model evidence score +
    40% measurable evidence quality) - it informs visibility only and NEVER
    moves entry/SL/TP;
  * every pipeline failure is recorded, never silent - the caller falls back
    to the legacy single-call path.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..config import settings
from ..aimanager import decisions
from . import memory

NON_ACTIONS = ("NO_ACTION", "INSUFFICIENT_EVIDENCE", "DATA_STALE", "CONFLICTING")

INSTRUCTIONS = (
    "You are the AI trade manager's reasoning core. You manage ONE existing "
    "position (never open/reverse/resize entries). Reply with ONLY a JSON "
    "object. Prefer honesty over action: you MAY answer without a management "
    "action using action=NO_ACTION, or answer=INSUFFICIENT_EVIDENCE / "
    "DATA_STALE / CONFLICTING. Otherwise: {\"action\": "
    "\"HOLD|PROTECT|PARTIAL_PROFIT|EXIT\", \"continuation_assessment\": "
    "\"STRONG|WEAKENING|REVERSING|UNCERTAIN\", \"next_target\": "
    "\"TP1|TP2|TP3|null\", \"confidence\": 0..1, \"reason_codes\": [\"UP\"], "
    "\"risk_state\": \"CONTROLLED|ELEVATED|CRITICAL\", \"recommended_sl\": "
    "number_or_null (PROTECT only; must TIGHTEN risk), \"partial_fraction\": "
    "0.x_or_null}. After TP1 reassess continuation (a partial close is never "
    "a full close); after TP2 the position is protected deterministically. "
    "confidence is an evidence score, NOT a success probability. Base every "
    "reason on the provided world model only."
)

CHALLENGE_INSTRUCTIONS = (
    "You are the devil's advocate for a trading management decision. Given "
    "the world model and the proposed decision, find the STRONGEST evidence-"
    "based objections (data contradictions, missing evidence, risk-state "
    "conflicts). Answer explicitly: 1) What supports this? 2) What "
    "contradicts it? 3) What evidence is missing? 4) How large is the "
    "sample? 5) Is the historical environment comparable? 6) Could regime "
    "change explain the result? 7) Could execution quality explain the "
    "result? 8) What would falsify this conclusion? Reply with ONLY: "
    "{\"survives\": bool, \"objections\": [\"...\"]}. survives=false "
    "means the decision must NOT be executed."
)


# ---------------------------------------------------------------------------
# evidence-quality components (deterministic, testable)
# ---------------------------------------------------------------------------
def freshness_score(world: dict) -> float:
    return float((world.get("freshness") or {}).get("score") or 0.0)


def sample_score(world: dict) -> float:
    stats = (world.get("historical") or {}).get("strategy_stats") or {}
    n = int(stats.get("sample_size") or 0)
    return min(1.0, n / max(1, settings.brain_min_strategy_samples))


def tf_agreement_score(world: dict) -> float:
    """Share of timeframes whose EMA alignment supports the trade direction."""
    trade = world.get("trade") or {}
    direction = trade.get("direction")
    if direction not in ("BUY", "SELL"):
        return 0.5                     # no position: neutral, not penalized
    want = "above" if direction == "BUY" else "below"
    dirs = [tf.get("ema9_vs_21") for tf in (world.get("timeframes") or {}).values()
            if tf and tf.get("ema9_vs_21")]
    if not dirs:
        return 0.5
    return round(sum(1 for d in dirs if d == want) / len(dirs), 2)


def evidence_quality(world: dict) -> float:
    return round((freshness_score(world) + sample_score(world) +
                  tf_agreement_score(world)) / 3.0, 3)


def confidence_pct(model_conf: float, world: dict) -> int:
    """Deterministic evidence-confidence percentage (visibility ONLY - it
    never moves entry/SL/TP)."""
    model_conf = max(0.0, min(1.0, float(model_conf or 0.0)))
    return int(round(100 * (0.6 * model_conf + 0.4 * evidence_quality(world))))


# ---------------------------------------------------------------------------
def _no_op(answer: str, world: dict, model: str, layer: str,
           reasons: list, challenge: Optional[dict] = None,
           model_conf: float = 0.0) -> dict:
    return {
        "answer": answer,
        "decision": None,                  # engine applies deterministic hold
        "confidence_pct": confidence_pct(model_conf, world),
        "evidence_quality": evidence_quality(world),
        "freshness": (world.get("freshness") or {}).get("verdict"),
        "reasons": reasons[:8],
        "model": model, "layer": layer,
        "challenge": challenge or {"ran": False, "survives": None,
                                   "objections": [], "error": None},
        "snapshot_ts": time.time(),
    }


def _observe(world: dict) -> Optional[str]:
    """Freshness audit -> blocking answer, or None to continue."""
    verdict = (world.get("freshness") or {}).get("verdict")
    if verdict == "NO_DATA":
        return "DATA_STALE"
    if verdict == "STALE":
        m15 = ((world.get("timeframes") or {}).get("15M") or {}).get("freshness") or {}
        if m15.get("stale") or freshness_score(world) < 0.5:
            return "DATA_STALE"   # execution-TF stale OR majority stale
    tfs = [tf for tf in (world.get("timeframes") or {}).values() if tf]
    if len(tfs) < 2:
        return "INSUFFICIENT_EVIDENCE"
    stats = (world.get("historical") or {}).get("strategy_stats") or {}
    if int(stats.get("sample_size") or 0) < settings.brain_min_strategy_samples:
        return "INSUFFICIENT_EVIDENCE"
    return None


# ---------------------------------------------------------------------------
def _run_brain(user_id: str, world: dict, escalate: bool = False,
                  router=None, signal: Optional[dict] = None) -> dict:
    """Full pipeline. NEVER raises (callers fall back honestly on error)."""
    from ..agent.router import get_router
    router = router or get_router()
    market = world.get("market")

    # OBSERVE -----------------------------------------------------------
    blocking = _observe(world)
    if blocking:
        return _no_op(blocking, world, "deterministic", "observe",
                      [f"BRAIN_OBSERVE_{blocking}"])

    # GENERATE ----------------------------------------------------------
    context = {"world_model": world,
               "controlled_memory": memory.recall(user_id, market=market,
                                                  strategy=(signal or {}).get(
                                                      "strategy_name"), k=5),
               "instructions": INSTRUCTIONS}
    try:
        res = router.analyze(
            "Reason over this world model and reply with ONLY the JSON decision.",
            context, escalate=escalate, user_id=user_id)
    except Exception as exc:
        return _no_op("NO_ACTION", world, "deterministic", "router_error",
                      [f"ROUTER_ERROR_{type(exc).__name__}"])
    model_name, layer = res.get("model", "primary"), res.get("layer", "primary")

    def _hold(reason: str, challenge=None):
        return _no_op("NO_ACTION", world, model_name, layer, [reason],
                      challenge=challenge)

    if layer == "local":
        return _hold("AI_UNAVAILABLE_DETERMINISTIC_HOLD")

    # DECIDE: classify the answer honestly -------------------------------
    try:
        blob = decisions.parse_json_blob(res.get("text", ""))
    except decisions.DecisionError as exc:
        return _hold(f"INVALID_JSON_{type(exc).__name__}")

    raw_action = str(blob.get("action", "")).strip().upper()
    raw_answer = str(blob.get("answer", "")).strip().upper()

    if raw_answer in NON_ACTIONS or raw_action in NON_ACTIONS:
        answer = raw_answer if raw_answer in NON_ACTIONS else (
            raw_action if raw_action != "NO_ACTION" else "NO_ACTION")
        out = _no_op(answer, world, model_name, layer,
                     [str(c)[:48] for c in (blob.get("reason_codes") or
                                            [f"BRAIN_{answer}"])])
        out["decision"] = decisions.deterministic_hold(answer)
        out["decision"]["raw"] = blob
        _remember(user_id, market, world, answer, None, out["confidence_pct"])
        return out

    decision: Optional[dict] = None
    try:
        decision = decisions.validate(res.get("text", ""))
    except decisions.DecisionError as exc:
        return _hold(f"INVALID_DECISION_{str(exc)[:60]}")

    # CHALLENGE (self-challenge on concrete actions only) ----------------
    challenge = {"ran": False, "survives": None, "objections": [], "error": None}
    if decision["action"] != "HOLD":
        challenge["ran"] = True
        try:
            ch = router.analyze(
                "Challenge this proposed decision.",
                {"world_model": world,
                 "proposed_decision": {k: v for k, v in decision.items()
                                       if k != "raw"},
                 "instructions": CHALLENGE_INSTRUCTIONS},
                escalate=False, user_id=user_id)
            ch_blob = decisions.parse_json_blob(ch.get("text", ""))
            challenge["survives"] = bool(ch_blob.get("survives", False))
            challenge["objections"] = [str(o)[:120]
                                       for o in (ch_blob.get("objections") or [])][:5]
        except Exception as exc:
            # challenge layer unavailable: decision stands, but this is
            # RECORDED - never silent (caller stores challenge metadata).
            challenge["error"] = f"{type(exc).__name__}"
        if challenge["survives"] is False:
            out = _no_op("CONFLICTING", world, model_name, layer,
                         challenge["objections"] or ["SELF_CHALLENGE_FAILED"],
                         challenge=challenge, model_conf=decision["confidence"])
            out["decision"] = decisions.deterministic_hold("CONFLICTING")
            out["decision"]["raw"] = blob
            _remember(user_id, market, world, "CONFLICTING", None,
                      out["confidence_pct"])
            return out

    # DECIDE (action survived) -------------------------------------------
    pct = confidence_pct(decision["confidence"], world)
    out = {
        "answer": "ACTION",
        "decision": decision,
        "confidence_pct": pct,
        "evidence_quality": evidence_quality(world),
        "freshness": (world.get("freshness") or {}).get("verdict"),
        "reasons": decision.get("reason_codes", []),
        "model": model_name, "layer": layer,
        "challenge": challenge,
        "snapshot_ts": time.time(),
    }
    _remember(user_id, market, world, "ACTION", decision["action"], pct)
    return out


def _remember(user_id: str, market: Optional[str], world: dict,
              answer: str, action: Optional[str], pct: int) -> None:
    """Short factual outcome note (controlled memory). Never raises."""
    try:
        trade = world.get("trade") or {}
        memory.remember(
            user_id,
            text=(f"{answer}{'/' + action if action else ''} "
                  f"{market} r={trade.get('r_multiple_now')} "
                  f"mfe={trade.get('mfe_r')} conf={pct}% "
                  f"fresh={(world.get('freshness') or {}).get('verdict')}"),
            kind="brain",
            meta={"market": market, "strategy": trade.get("strategy"),
                  "answer": answer, "action": action})
    except Exception:
        pass

# ---------------------------------------------------------------------------
# EVIDENCE stage (3.0 spec section 7): OBSERVE -> GENERATE -> CHALLENGE ->
# EVIDENCE -> DECIDE. Runs on EVERY exit path; purely additive annotation -
# the deterministic evidence view NEVER modifies the decision itself.
# ---------------------------------------------------------------------------
def run_brain(user_id: str, world: dict, escalate: bool = False,
              router=None, signal: Optional[dict] = None) -> dict:
    out = _run_brain(user_id, world, escalate, router, signal)
    try:
        from ..evidence.brain_bridge import attach_evidence
        attach_evidence(user_id, world, signal, out)
    except Exception as exc:                      # never break the brain
        out["evidence"] = None
        out["evidence_error"] = type(exc).__name__
    return out
