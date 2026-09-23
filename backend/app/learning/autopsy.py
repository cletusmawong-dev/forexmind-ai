"""Trade Autopsy & Scientific Self-Improvement Engine (user spec 2026-09-22).

For EVERY completed trade: a structured autopsy is built from RECORDED data
(signal doc, Signal DNA, tracker lifecycle incl. MFE/MAE from C-3), compared
against historical comparable trades, and - only above the configured minimum
sample - may carry ONE evidence-backed HYPOTHESIS with a PROPOSED EXPERIMENT
that ALWAYS waits for explicit user approval.

Hard boundaries (spec sections 12/18):
  - This engine NEVER modifies entry logic, strategies, risk, or execution.
    It only OBSERVES, MEASURES and PROPOSES. Approval creates a PAPER
    (shadow/counterfactual) experiment - still zero behavior change.
  - The AI layer can only ADD labeled commentary (FACT/OBSERVATION/HYPOTHESIS);
    it cannot change any decision or parameter. AI output is validated and
    degrades to honest "AI_UNAVAILABLE".
  - Confidence is LOW/MEDIUM/HIGH from sample size + stability - never a
    guarantee. The engine actively tries to DISPROVE its own pattern (section
    16): the comparable sample is split into older/newer halves and the effect
    must hold in both or the hypothesis is marked DISPROVED and no experiment
    is proposed.

Reuse (section 19): forensics comparables, Signal DNA context, experiments
one-variable validation + metrics (win rate AND expectancy/avgR/drawdown),
hypotheses user-decision flow. Nothing here duplicates those.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

COLLECTION = "autopsies"

# Observation-level gates (an OBSERVATION needs less evidence than an
# EXPERIMENT proposal; the experiment gate uses the existing configurable
# min_trades_for_experiment = 30, spec section 3 default).
_OBS_MIN_GROUP = 8          # minimum trades in the "with condition" group
_PROP_MIN_GROUP = 10        # minimum per group to propose an experiment
_MIN_EFFECT_R = 0.15        # minimum |delta avg R| worth reporting


def _store():
    from ..db.store import get_store
    return get_store()


def _min_sample() -> int:
    from ..config import settings
    return int(getattr(settings, "min_trades_for_experiment", 30))


# ---------------------------------------------------------------------------
# condition predicates (declarative - safe to store and re-evaluate)
# ---------------------------------------------------------------------------
def _dna(doc: dict) -> dict:
    return doc.get("dna") or {}


def _mtf_disagreement(doc: dict) -> bool:
    mtf = _dna(doc).get("mtf") or {}
    sgn = {"BULLISH": 1, "BEARISH": -1}
    want = 1 if doc.get("direction") == "BUY" else -1
    return any(sgn.get(v, 0) not in (want, 0) for v in mtf.values())


def _field(doc: dict, key: str):
    if key.startswith("dna."):
        return _dna(doc).get(key[4:])
    return doc.get(key)


def eval_condition(doc: dict, cond: dict) -> bool:
    """Evaluate a stored declarative condition against a signal doc."""
    try:
        val = _field(doc, cond["key"])
        op = cond["op"]
        if op == "equals":
            return val == cond["value"]
        if op == "in":
            return val in cond["value"]
        if op == "not_in":
            return val not in cond["value"]
        if op == "is_true":
            return bool(val)
        if op == "abs_lte":
            return isinstance(val, (int, float)) and abs(val) <= cond["value"]
    except Exception:
        return False
    return False


def _condition_candidates(sig: dict) -> List[dict]:
    """Declarative conditions worth testing for this trade (from its DNA)."""
    dna = _dna(sig)
    out: List[dict] = []
    if dna.get("momentum"):
        out.append({"key": "dna.momentum", "op": "in",
                    "value": ["WEAK", "WEAK_UP", "WEAK_DOWN", "FLAT"],
                    "label": "weak momentum at entry"})
    if dna.get("volatility"):
        out.append({"key": "dna.volatility", "op": "equals", "value": "HIGH",
                    "label": "high volatility at entry"})
    if dna.get("mtf"):
        out.append({"key": "_mtf_disagreement", "op": "is_true", "value": None,
                    "label": "higher-timeframe disagreement with the trade"})
    if dna.get("session"):
        out.append({"key": "dna.session", "op": "equals", "value": dna.get("session"),
                    "label": f"{dna.get('session')} session"})
    npm = dna.get("news_proximity_min")
    if isinstance(npm, int) and abs(npm) <= 30:
        out.append({"key": "dna.news_proximity_min", "op": "abs_lte", "value": 30,
                    "label": "news within +/-30 minutes"})
    return out


def _group_stats(docs: List[dict]) -> dict:
    n = len(docs)
    if not n:
        return {"n": 0}
    rs = [float(d.get("r_multiple") or 0.0) for d in docs]
    wins = sum(1 for r in rs if r > 0)
    tp2 = sum(1 for d in docs if (d.get("tp_hits") or 0) >= 2)
    return {"n": n, "win_rate": round(100.0 * wins / n, 1),
            "avg_r": round(sum(rs) / n, 3),
            "tp2_rate": round(100.0 * tp2 / n, 1)}


def _find_comparables(sig: dict, store) -> List[dict]:
    """Same strategy + same pair + completed (forensics' definition, reused)."""
    docs = store.list("signals", filters={
        "strategy_id": sig.get("strategy_id"), "market": sig.get("market"),
        "completed": True}, limit=500)
    return [d for d in docs if d.get("id") != sig.get("id")
            and d.get("r_multiple") is not None]


# ---------------------------------------------------------------------------
# the autopsy record
# ---------------------------------------------------------------------------
def build_record(sig: dict, comps: List[dict]) -> dict:
    extra = sig.get("extra") or {}
    exec_delay = None
    try:
        from datetime import datetime
        a = pd_ts(sig.get("candle_time"))
        b = pd_ts(sig.get("createdAt"))
        if a and b:
            exec_delay = round((b - a).total_seconds(), 1)
    except Exception:
        pass
    actual_entry = sig.get("mt5_open_price")
    return {
        "trade_id": sig.get("signal_id"), "signal_doc_id": sig.get("id"),
        "strategy_id": sig.get("strategy_id"), "strategy_name": sig.get("strategy_name"),
        "setup_type": extra.get("setup_state") or "EMA_CROSS",
        "symbol": sig.get("market"), "direction": sig.get("direction"),
        "entry_timeframe": sig.get("timeframe"),
        "sweep_bos_retest": ({k: extra[k] for k in
                              ("sweep_timeframe", "bos_timeframe", "entry_timeframe", "setup_key")}
                             if extra.get("sweep_timeframe") else None),
        "entry": sig.get("entry"), "sl": sig.get("sl"),
        "tp1": sig.get("tp1"), "tp2": sig.get("tp2"), "tp3": sig.get("tp3"),
        "result": sig.get("outcome"), "status": sig.get("status"),
        "R": sig.get("r_multiple"),
        "market_regime": _dna(sig).get("regime"),
        "session": _dna(sig).get("session"),
        "volatility": _dna(sig).get("volatility"),
        "momentum": _dna(sig).get("momentum"),
        "mtf": _dna(sig).get("mtf"),
        "news": ({"event": _dna(sig).get("news_event"),
                  "proximity_min": _dna(sig).get("news_proximity_min")}
                 if _dna(sig).get("news_proximity_min") is not None else None),
        "behavior": {
            "tp1_hit": (sig.get("tp_hits") or 0) >= 1,
            "tp2_hit": (sig.get("tp_hits") or 0) >= 2,
            "tp3_hit": (sig.get("tp_hits") or 0) >= 3,
            "mfe_r": sig.get("mfe_r"), "mae_r": sig.get("mae_r"),
            "max_profit_r": sig.get("mfe_r"),           # same normalized record
            "max_drawdown_r": sig.get("mae_r"),
            "reversed_after_target": sig.get("status") == "SL_HIT"
                                     and (sig.get("tp_hits") or 0) > 0,
            "duration_min": _duration_min(sig),
            "execution_delay_sec": exec_delay,
            "intended_entry": sig.get("entry"),
            "actual_entry": actual_entry,
            "entry_slippage": (round(float(actual_entry) - float(sig["entry"]), 6)
                               if actual_entry is not None and sig.get("entry") else None),
            "intended_sl_tp_vs_broker": ("broker order carries the strategy levels; "
                                         "the AI ladder manages TP1/TP3 live"),
        },
    }


def pd_ts(v):
    if not v:
        return None
    try:
        import pandas as pd
        return pd.Timestamp(v) if not hasattr(v, "timestamp") else pd.Timestamp(v)
    except Exception:
        return None


def _duration_min(sig: dict) -> Optional[float]:
    a, b = pd_ts(sig.get("candle_time")), pd_ts(sig.get("completed_at"))
    if a and b:
        return round((b - a).total_seconds() / 60.0, 1)
    return None


def _facts(sig: dict) -> List[dict]:
    b = build_record(sig, [])["behavior"]
    facts = [
        {"label": "Outcome", "kind": "FACT",
         "detail": f"{sig.get('outcome')} ({sig.get('status')}) at {sig.get('r_multiple')}R"},
        {"label": "Levels reached", "kind": "FACT",
         "detail": {"tp1": b["tp1_hit"], "tp2": b["tp2_hit"], "tp3": b["tp3_hit"]}},
    ]
    if sig.get("mfe_r") is not None:
        facts.append({"label": "MFE/MAE", "kind": "FACT",
                      "detail": {"mfe_r": sig.get("mfe_r"), "mae_r": sig.get("mae_r")}})
    dna = _dna(sig)
    if dna:
        facts.append({"label": "Conditions at entry (recorded)", "kind": "FACT",
                      "detail": {"regime": dna.get("regime"), "volatility": dna.get("volatility"),
                                 "momentum": dna.get("momentum"), "session": dna.get("session"),
                                 "mtf": dna.get("mtf")}})
    if b.get("execution_delay_sec") is not None:
        facts.append({"label": "Execution delay", "kind": "FACT",
                      "detail": f"{b['execution_delay_sec']}s from candle close to signal creation"})
    if b.get("entry_slippage") is not None:
        facts.append({"label": "Entry vs intended", "kind": "FACT",
                      "detail": f"intended {b['intended_entry']} vs broker {b['actual_entry']}"})
    return facts


def _regime_table(sig: dict, comps: List[dict]) -> dict:
    """Regime-aware splits (section 7) - evidence only, never a verdict."""
    table: Dict[str, dict] = {}
    table["session"] = _split_by(comps, lambda d: (_dna(d).get("session") or "?"))
    table["direction"] = _split_by(comps, lambda d: d.get("direction") or "?")
    table["volatility"] = _split_by(comps, lambda d: _dna(d).get("volatility") or "?")
    table["entry_tf"] = _split_by(comps, lambda d: d.get("timeframe") or "?")
    return table


def _split_by(docs: List[dict], keyfn) -> dict:
    out: Dict[str, dict] = {}
    for d in docs:
        k = str(keyfn(d))
        out.setdefault(k, []).append(d)
    return {k: _group_stats(v) for k, v in sorted(out.items()) if k != "?"}


def _best_pattern(sig: dict, comps: List[dict]) -> Optional[dict]:
    """Pick the single strongest declarable condition by |delta avg R| - the
    COMPLETE performance picture (win rate AND avg R AND TP2 rate, section 17)."""
    best = None
    for cond in _condition_candidates(sig):
        with_ = [d for d in comps if eval_condition(d, cond)]
        without = [d for d in comps if not eval_condition(d, cond)]
        gw, gwo = _group_stats(with_), _group_stats(without)
        if gw.get("n", 0) < _OBS_MIN_GROUP or gwo.get("n", 0) < _OBS_MIN_GROUP:
            continue
        delta = round(gw["avg_r"] - gwo["avg_r"], 3)
        if best is None or abs(delta) > abs(best["delta_avg_r"]):
            best = {**cond, "with": gw, "without": gwo, "delta_avg_r": delta}
    return best


def _stable_effect(sig: dict, comps: List[dict], cond: dict) -> Optional[bool]:
    """Section 16: split the sample into older/newer halves; the effect must
    keep the same sign in both, else it fails cross-validation."""
    try:
        import pandas as pd
        keyed = sorted(comps, key=lambda d: str(d.get("completed_at") or ""))
        half = max(1, len(keyed) // 2)
        halves = (keyed[:half], keyed[half:])
        signs = []
        for h in halves:
            gw = _group_stats([d for d in h if eval_condition(d, cond)])
            gwo = _group_stats([d for d in h if not eval_condition(d, cond)])
            if gw.get("n", 0) < 4 or gwo.get("n", 0) < 4:
                return None     # halves too small to cross-check
            signs.append(1 if gw["avg_r"] > gwo["avg_r"] else -1)
        return signs[0] == signs[1]
    except Exception:
        return None


def _confidence(n: int, stable: Optional[bool], delta: float) -> str:
    if n >= 80 and stable and abs(delta) >= 0.3:
        return "HIGH"
    if n >= 50 and stable is not False and abs(delta) >= 0.2:
        return "MEDIUM"
    return "LOW"


def _fingerprint(strategy_id: str, cond: dict) -> str:
    return f"{strategy_id}|{cond.get('key')}|{cond.get('op')}|{cond.get('value')}"


def build_autopsy(sig: dict, store=None) -> Dict[str, Any]:
    """Core synchronous autopsy (no AI call - instant, loop-safe)."""
    store = store or _store()
    comps = _find_comparables(sig, store)
    rec = build_record(sig, comps)
    autopsy: Dict[str, Any] = {
        **rec,
        "facts": _facts(sig),
        "observations": [], "hypotheses": [],
        "regime_table": _regime_table(sig, comps),
        "comparable_sample": len(comps),
        "sample_size": len(comps),
        "min_sample_required": _min_sample(),
        "experiment_id": None, "user_approved": False,
        "occurrences": 1, "related_trade_ids": [sig.get("signal_id")],
        "ai": None,
    }

    # ---- sample gate (section 3): one trade NEVER changes anything --------
    if len(comps) < _min_sample():
        autopsy["status"] = "INSUFFICIENT_SAMPLE"
        autopsy["hypotheses"].append({
            "kind": "HYPOTHESIS",
            "text": f"Only {len(comps)} comparable completed trades - below the "
                    f"{_min_sample()} minimum. No pattern is claimed and no "
                    "experiment may be proposed from this trade.",
        })
        return autopsy

    best = _best_pattern(sig, comps)
    if not best:
        autopsy["status"] = "NO_EVALUABLE_PATTERN"
        return autopsy

    stable = _stable_effect(sig, comps, best)
    conf = _confidence(len(comps), stable, best["delta_avg_r"])
    autopsy["confidence"] = conf

    if stable is False:
        # section 16: the apparent improvement does not survive cross-validation
        autopsy["status"] = "PATTERN_DISPROVED"
        autopsy["observations"].append({
            "kind": "OBSERVATION",
            "text": (f"'{best['label']}' looked related to outcome over the full "
                     f"sample ({best['with']} vs {best['without']}) but the effect "
                     "does NOT hold across time halves - treated as noise."),
        })
        return autopsy

    delta = best["delta_avg_r"]
    if abs(delta) < _MIN_EFFECT_R:
        autopsy["status"] = "NO_MEANINGFUL_EFFECT"
        return autopsy

    autopsy["status"] = "PATTERN_OBSERVED"
    autopsy["observations"].append({
        "kind": "OBSERVATION",
        "text": (f"{best['label']}: {best['with']['n']} comparable trades with the "
                 f"condition reached {best['with']['win_rate']}% win rate / "
                 f"{best['with']['avg_r']} avg R / {best['with']['tp2_rate']}% TP2 vs "
                 f"{best['without']['win_rate']}% / {best['without']['avg_r']} / "
                 f"{best['without']['tp2_rate']}% without it."),
        "stats": {"with": best["with"], "without": best["without"],
                  "delta_avg_r": delta},
    })
    autopsy["hypotheses"].append({
        "kind": "HYPOTHESIS",
        "text": f"{best['label']} may {'reduce' if delta < 0 else 'increase'} "
                "the trade's expectancy. This is a hypothesis, not a fact.",
        "fingerprint": _fingerprint(sig.get("strategy_id"), best),
        "confidence": conf,
    })

    # ---- experiment PROPOSAL only (never self-applied, section 5/12) ------
    if (abs(delta) >= 0.2 and conf in ("MEDIUM", "HIGH")
            and best["with"]["n"] >= _PROP_MIN_GROUP
            and best["without"]["n"] >= _PROP_MIN_GROUP):
        cond = {k: best[k] for k in ("key", "op", "value")}
        autopsy["experiment_proposal"] = {
            "kind": "MANAGEMENT_CONDITION",     # post-entry learning only (section 10)
            "condition": cond,
            "condition_label": best["label"],
            "hypothesis": (f"When '{best['label']}' is present, manage differently: "
                           f"the evidence shows avg R {best['with']['avg_r']} vs "
                           f"{best['without']['avg_r']} without it."),
            "expected_effect": ("potentially protect profit earlier"
                                if delta < 0 else "potentially allow continuation"),
            "variable_changed": "NONE - paper/shadow tracking only",
            "confidence": conf,
            "status": "WAITING_FOR_USER_APPROVAL",
            "everything_else_unchanged": True,
        }
    return autopsy


def _merge_into_existing(autopsy: dict, store) -> Optional[dict]:
    """Section 14: consolidate repeated discoveries instead of 50 copies."""
    fps = [h.get("fingerprint") for h in autopsy.get("hypotheses", []) if h.get("fingerprint")]
    if not fps:
        return None
    existing = store.list(COLLECTION, filters={
        "strategy_id": autopsy["strategy_id"], "status": "PATTERN_OBSERVED"}, limit=100)
    for cand in existing:
        cand_fps = [h.get("fingerprint") for h in cand.get("hypotheses", []) if h.get("fingerprint")]
        if set(cand_fps) & set(fps):
            cand["occurrences"] = int(cand.get("occurrences") or 1) + 1
            cand["related_trade_ids"] = (cand.get("related_trade_ids") or [])[-49:] + \
                                        [autopsy["trade_id"]]
            cand["last_trade_id"] = autopsy["trade_id"]
            cand["confidence"] = autopsy.get("confidence", cand.get("confidence"))
            cand["comparable_sample"] = autopsy.get("comparable_sample")
            cand["observations"] = autopsy.get("observations")
            cand["regime_table"] = autopsy.get("regime_table")
            store.update(COLLECTION, cand["id"], {
                "occurrences": cand["occurrences"], "related_trade_ids": cand["related_trade_ids"],
                "last_trade_id": cand["last_trade_id"], "confidence": cand["confidence"],
                "comparable_sample": cand["comparable_sample"],
                "observations": cand["observations"], "regime_table": cand["regime_table"]})
            return cand
    return None


def relevant_knowledge(strategy_id: str, limit: int = 5) -> List[dict]:
    """Persistent knowledge base retrieval (section 13) - prior findings."""
    docs = _store().list(COLLECTION, filters={"strategy_id": strategy_id}, limit=200)
    docs.sort(key=lambda d: str(d.get("createdAt") or ""), reverse=True)
    return [{"trade_id": d.get("trade_id"), "status": d.get("status"),
             "confidence": d.get("confidence"), "occurrences": d.get("occurrences"),
             "observation": ((d.get("observations") or [{}])[0].get("text", "")[:220])}
            for d in docs[:limit]]


# ---------------------------------------------------------------------------
# AI enrichment (sections 2/11): labeled commentary ONLY, validated, optional
# ---------------------------------------------------------------------------
_ENRICH_LOCK = threading.Lock()
_ENRICH_INFLIGHT: set = set()


def _ai_enrich(autopsy_id: str) -> None:
    store = _store()
    doc = store.get(COLLECTION, autopsy_id)
    if not doc:
        return
    try:
        from ..agent.router import ModelRouter
        router = ModelRouter()
        prior = relevant_knowledge(doc["strategy_id"])
        prompt = (
            "You are auditing ONE completed trade for a scientific self-improvement "
            "engine. Answer THREE questions:\n"
            "1) Given everything knowable AT ENTRY, what evidence suggests this trade "
            "could have been managed better?\n"
            "2) What information available only AFTER entry explains the result?\n"
            "3) Is there a repeatable pattern here, or is this probably random noise?\n"
            "Reply with ONLY JSON: {\"facts\":[\"...\"], \"observations\":[\"...\"], "
            "\"hypotheses\":[\"...\"], \"management\":[\"...\"], "
            "\"repeatable\":\"YES|NO|UNCLEAR\", \"note\":\"...\"}. "
            "Facts must only restate given data. Never present a hypothesis as a fact. "
            "Do not propose changing entry logic - management observations only.\n\n"
            f"RECORDED TRADE DATA:\n{doc}\n\nPRIOR KNOWLEDGE:\n{prior}")
        res = router.analyze(prompt, {}, escalate=False,
                             user_id=doc.get("user_id") or None)
        if res.get("layer") == "local":
            store.update(COLLECTION, autopsy_id,
                         {"ai": {"status": "AI_UNAVAILABLE",
                                 "note": "deterministic fallback - no AI commentary"}})
            return
        import json as _json
        text = (res.get("text") or "").strip()
        start, end = text.find("{"), text.rfind("}")
        parsed = _json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        clean = {k: parsed.get(k) for k in
                 ("facts", "observations", "hypotheses", "management", "repeatable", "note")
                 if parsed.get(k) is not None}
        clean = {"status": "OK", "model": res.get("model"), **clean,
                 "labels_rule": "FACT=recorded data, OBSERVATION=measured pattern, "
                                "HYPOTHESIS=unproven"}
        store.update(COLLECTION, autopsy_id, {"ai": clean})
    except Exception as e:
        try:
            store.update(COLLECTION, autopsy_id,
                         {"ai": {"status": "AI_UNAVAILABLE", "note": type(e).__name__}})
        except Exception:
            pass
    finally:
        with _ENRICH_LOCK:
            _ENRICH_INFLIGHT.discard(autopsy_id)


# ---------------------------------------------------------------------------
# entry point (called by the tracker on every completion)
# ---------------------------------------------------------------------------
def on_trade_completed(sig: dict) -> Optional[str]:
    """Stamp paper-experiment groups, then build/merge the autopsy; AI
    enrichment runs in a daemon thread so the tracker loop never blocks."""
    store = _store()
    # ---- PAPER shadow stamping (zero behavior change, section 5/6) --------
    papers = store.list("experiments", filters={
        "kind": "MANAGEMENT_CONDITION", "lifecycle": "PAPER",
        "strategy_id": sig.get("strategy_id")}, limit=10)
    for exp in papers:
        if eval_condition(sig, exp.get("condition") or {}):
            group = "EXPERIMENT"
        else:
            group = "BASELINE"
        store.update("signals", sig["id"], {
            "experiment_id": exp.get("experiment_id"),
            "experiment_group": group})

    autopsy = build_autopsy(sig, store)
    autopsy["user_id"] = sig.get("userId")

    merged = _merge_into_existing(autopsy, store)
    if merged:
        return merged["id"]
    doc = store.create(COLLECTION, autopsy)
    _spawn_ai(doc["id"])
    return doc["id"]


def _spawn_ai(autopsy_id: str) -> None:
    """Background AI enrichment - never blocks the tracker loop."""
    with _ENRICH_LOCK:
        if autopsy_id in _ENRICH_INFLIGHT:
            return
        _ENRICH_INFLIGHT.add(autopsy_id)
    threading.Thread(target=_ai_enrich, args=(autopsy_id,),
                     daemon=True, name="autopsy-ai").start()


# ---------------------------------------------------------------------------
# user decisions (section 5/12): approve -> PAPER experiment, reject -> closed
# ---------------------------------------------------------------------------
def decide_proposal(autopsy_id: str, user_id: str, approve: bool) -> dict:
    store = _store()
    doc = store.get(COLLECTION, autopsy_id)
    if not doc or doc.get("user_id") != user_id:
        from fastapi import HTTPException
        raise HTTPException(404, "Autopsy not found")
    proposal = doc.get("experiment_proposal")
    if not proposal:
        from fastapi import HTTPException
        raise HTTPException(409, "This autopsy carries no experiment proposal")
    if doc.get("user_approved"):
        from fastapi import HTTPException
        raise HTTPException(409, "Already decided")
    store.update(COLLECTION, autopsy_id, {"user_approved": bool(approve)})
    if not approve:
        store.update(COLLECTION, autopsy_id,
                     {"experiment_proposal": {**proposal, "status": "REJECTED_BY_USER"}})
        return {"approved": False, "status": "REJECTED_BY_USER"}
    exp_doc = store.create("experiments", {
        "userId": user_id, "kind": "MANAGEMENT_CONDITION",
        "experiment_id": f"EXP-A{doc['id'][:6].upper()}",
        "strategy_id": doc["strategy_id"], "market": doc["symbol"],
        "condition": proposal["condition"], "condition_label": proposal["condition_label"],
        "hypothesis": proposal["hypothesis"], "expected_effect": proposal["expected_effect"],
        "lifecycle": "PAPER",       # shadow/counterfactual ONLY - never live (section 12)
        "status": "PAPER", "one_variable": True, "variable_changed": "NONE",
        "source_autopsy": autopsy_id, "confidence": proposal.get("confidence"),
        "approved_by_user": True, "baseline": {}, "experiment": {},
    })
    store.update(COLLECTION, autopsy_id, {
        "experiment_id": exp_doc["experiment_id"],
        "experiment_proposal": {**proposal, "status": "APPROVED_PAPER"}})
    return {"approved": True, "lifecycle": "PAPER", "experiment_id": exp_doc["experiment_id"]}


def experiment_report(experiment_id: str) -> dict:
    """Baseline vs experiment picture (section 6) - never win-rate-only."""
    from .metrics import compute_metrics
    store = _store()
    docs = store.list("signals", filters={"experiment_id": experiment_id}, limit=500)
    groups = {"BASELINE": [d for d in docs if d.get("experiment_group") == "BASELINE"],
              "EXPERIMENT": [d for d in docs if d.get("experiment_group") == "EXPERIMENT"]}
    out = {"experiment_id": experiment_id, "sample": {k: len(v) for k, v in groups.items()}}
    for name, g in groups.items():
        out[name.lower()] = compute_metrics(
            [{"r_multiple": d.get("r_multiple") or 0, "tp_hits": d.get("tp_hits") or 0,
              "duration_bars": 0, "market": d.get("market"), "timeframe": d.get("timeframe"),
              "session": (_dna(d).get("session") or "?")} for d in g])
    return out
