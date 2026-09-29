"""FOREXMIND 3.0 Evidence Engine (3.0 spec §3–§6, Stage 1).

Answers one question deterministically: "How much should FOREXMIND actually
trust this conclusion?" — independent from any LLM confidence. The same store
state always produces the same score (pure functions, fixed weights).

Subjects are the preferred analytical granularity of spec §15:
strategy x instrument x session. States are CLASSIFICATIONS, not guarantees:
    INSUFFICIENT -> PRELIMINARY -> SUPPORTED -> STRONGER (+ internal CONTRADICTED)

Sample-size gates exist, but state is NEVER classified from sample size alone:
quality gates (score, recent collapse, contradictions) can downgrade anywhere.

Weights are fixed by design and MUST NOT be tuned against data being scored.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..db.store import get_store

EVIDENCE_STATES = ("INSUFFICIENT", "PRELIMINARY", "SUPPORTED", "STRONGER")

# Fixed component weights (sum = 1.00). Do not tune against evaluated data.
WEIGHTS: Dict[str, float] = {
    "sample": 0.25,
    "similarity": 0.10,
    "regime": 0.10,
    "oos": 0.15,          # out-of-sample + walk-forward (experiment results)
    "recency": 0.10,
    "recent_performance": 0.10,
    "execution": 0.10,
    "data_quality": 0.05,
    "consistency": 0.05,
}
CONTRADICTION_PENALTY = 0.6     # total *= (1 - penalty * contradiction_strength)

MIN_N = 20                      # below this: INSUFFICIENT, always
RECENT_WINDOW = 20              # "recent performance" = last N completed


# ---------------------------------------------------------------------------
# observation collection (completed signals only - never open/never future)
# ---------------------------------------------------------------------------
def _completed(store, user_id: str, strategy_id: str, market: str,
               session: Optional[str]) -> List[dict]:
    rows = store.list("signals", filters={"userId": user_id}, limit=2000)
    out = []
    for s in rows:
        if not s.get("completed") or s.get("strategy_id") != strategy_id \
                or s.get("market") != market:
            continue
        if session and (s.get("market_conditions") or {}).get("session") != session:
            continue
        out.append(s)
    out.sort(key=lambda s: str(s.get("completed_at") or s.get("candle_time") or ""))
    return out


def _wr_rs(rows: List[dict]) -> tuple:
    rs = [float(s.get("r_multiple") or 0.0) for s in rows]
    wins = sum(1 for s in rows if s.get("outcome") == "WIN")
    wr = round(100.0 * wins / len(rows), 1) if rows else None
    return wr, rs


def _days_since(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# contradiction engine (spec §5): actively look for evidence AGAINST
# ---------------------------------------------------------------------------
def contradiction_analysis(rows: List[dict], baseline_wr: Optional[float]) -> tuple:
    """Returns (contradictions, contradiction_strength, recent_view)."""
    contradictions: List[Dict[str, Any]] = []
    if not rows:
        return contradictions, 0.0, {}

    recent = rows[-RECENT_WINDOW:]
    recent_wr, recent_rs = _wr_rs(recent)
    overall_wr, _ = _wr_rs(rows)
    strength = 0.0

    if baseline_wr is not None and recent_wr is not None and len(recent) >= 5 \
            and recent_wr < baseline_wr - 15:
        s = min(1.0, (baseline_wr - recent_wr) / 50.0)
        strength = max(strength, s)
        contradictions.append({
            "factor": "recent_collapse",
            "detail": f"last-{len(recent)} WR {recent_wr}% vs baseline "
                      f"{round(baseline_wr, 1)}% (historical {overall_wr}%)",
            "strength": round(s, 2)})

    # current losing streak
    streak = 0
    for s in reversed(rows):
        if s.get("outcome") == "LOSS":
            streak += 1
        else:
            break
    if streak >= 4:
        s = min(1.0, 0.3 + 0.1 * (streak - 4))
        strength = max(strength, s)
        contradictions.append({
            "factor": "losing_streak",
            "detail": f"{streak} consecutive losses",
            "strength": round(s, 2)})

    # regime split: one regime badly underperforms another (n>=5 each)
    by_regime: Dict[str, List[dict]] = {}
    for s in rows:
        rg = (s.get("market_conditions") or {}).get("regime")
        if rg:
            by_regime.setdefault(rg, []).append(s)
    if len(by_regime) >= 2:
        stats = {rg: _wr_rs(rr) for rg, rr in by_regime.items()
                 if len(rr) >= 5}
        if len(stats) >= 2:
            good = max(stats.items(), key=lambda kv: kv[0] and (kv[1][0] or 0))
            bad = min(stats.items(), key=lambda kv: kv[1][0] if kv[1][0] is not None else 999)
            g_wr, b_wr = good[1][0], bad[1][0]
            if g_wr is not None and b_wr is not None and g_wr - b_wr >= 25:
                strength = max(strength, 0.4)
                contradictions.append({
                    "factor": "regime_split",
                    "detail": f"{good[0]} WR {g_wr}% vs {bad[0]} WR {b_wr}% "
                              f"(n={len(good[1])}/{len(bad[1])})",
                    "strength": 0.4})

    # realized drawdown of the cumulative R curve
    cum = peak = 0.0
    dd = 0.0
    for r in (float(s.get("r_multiple") or 0.0) for s in rows):
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    if dd <= -3.0:
        strength = max(strength, 0.35)
        contradictions.append({
            "factor": "drawdown",
            "detail": f"open R drawdown {round(dd, 2)}R from equity peak",
            "strength": 0.35})

    return contradictions, round(strength, 2), {
        "recent_n": len(recent), "recent_wr": recent_wr,
        "recent_avg_r": round(sum(recent_rs) / len(recent_rs), 3) if recent_rs else None,
        "current_losing_streak": streak, "open_drawdown_r": round(dd, 2)}


def supporting_analysis(rows: List[dict], baseline_wr: Optional[float]) -> List[dict]:
    support: List[dict] = []
    if not rows:
        return support
    wr, rs = _wr_rs(rows)
    if baseline_wr is not None and wr is not None and wr >= baseline_wr:
        support.append({"factor": "win_rate_at_or_above_baseline",
                        "detail": f"overall WR {wr}% vs baseline {round(baseline_wr, 1)}%"})
    avg = sum(rs) / len(rs) if rs else 0
    if avg > 0:
        support.append({"factor": "positive_expectancy",
                        "detail": f"avg {round(avg, 3)}R over {len(rows)} completed"})
    confirmed = sum(1 for s in rows if s.get("mt5_confirmed"))
    if rows and confirmed / len(rows) >= 0.8:
        support.append({"factor": "broker_confirmed",
                        "detail": f"{confirmed}/{len(rows)} results broker-confirmed"})
    return support


# ---------------------------------------------------------------------------
# deterministic scoring (spec §4)
# ---------------------------------------------------------------------------
def _experiment_strength(store, strategy_id: str) -> tuple:
    """(oos_strength, walk_forward_strength, found) from real experiment docs."""
    exps = store.list("experiments", filters={"strategy_id": strategy_id}, limit=20)
    if not exps:
        return 0.0, 0.0, False
    exps.sort(key=lambda e: str(e.get("created_at") or ""))
    e = exps[-1]
    # legacy docs may carry result/walk_forward as strings - tolerate
    wf = e.get("walk_forward") if isinstance(e.get("walk_forward"), dict) else {}
    wf_strength = 1.0 if wf.get("consistent") is True else \
        0.0 if wf.get("consistent") is False else 0.0
    res = e.get("result") if isinstance(e.get("result"), dict) else {}
    oos = None
    for key in ("oos_win_rate", "holdout_win_rate"):
        if isinstance(res.get(key), (int, float)):
            oos = max(0.0, min(1.0, float(res[key]) / 100.0))
    if oos is None and wf:
        oos = wf_strength
    return (oos if oos is not None else 0.0), wf_strength, True


def score_observations(rows: List[dict], baseline_wr: Optional[float],
                       store=None, strategy_id: Optional[str] = None) -> dict:
    """Pure deterministic scoring. Same inputs -> same outputs, always."""
    n = len(rows)
    contradictions, c_strength, recent_view = contradiction_analysis(rows, baseline_wr)
    overall_wr, rs = _wr_rs(rows)
    recent_wr = recent_view.get("recent_wr")

    components: Dict[str, float] = {}
    missing: List[str] = []

    components["sample"] = min(1.0, n / 100.0)

    # historical_similarity: how homogeneous is the sample (dominant-regime share)
    regimes = [(s.get("market_conditions") or {}).get("regime") for s in rows]
    known = [r for r in regimes if r]
    if known:
        top = max(set(known), key=known.count)
        components["similarity"] = round(len(known and [r for r in known if r == top]) / len(rows), 3)
        components["regime"] = round(len([r for r in known if r == top]) / len(known), 3)
    else:
        components["similarity"] = 0.5
        components["regime"] = 0.5
        missing.append("regime_labels")

    oos, wf, found = (0.0, 0.0, False)
    if store is not None and strategy_id:
        oos, wf, found = _experiment_strength(store, strategy_id)
    components["oos"] = round(0.5 * oos + 0.5 * wf, 3) if found else 0.0
    if not found:
        missing.append("out_of_sample")
        missing.append("walk_forward")

    last_ts = rows[-1].get("completed_at") or rows[-1].get("candle_time") if rows else None
    days = _days_since(last_ts)
    components["recency"] = round(math.exp(-(days or 999.0) / 45.0), 3)

    components["recent_performance"] = round((recent_wr or 0.0) / 100.0, 3)
    if recent_wr is None:
        missing.append("recent_performance")

    confirmed = sum(1 for s in rows if s.get("mt5_confirmed"))
    components["execution"] = round(confirmed / n, 3) if n else 0.0
    if not any(s.get("mt5_confirmed") for s in rows):
        missing.append("execution_quality")

    bad = sum(1 for s in rows if s.get("outcome") not in ("WIN", "LOSS")
              or s.get("r_multiple") is None)
    components["data_quality"] = round(1.0 - bad / n, 3) if n else 0.0
    if bad:
        missing.append(f"data_quality:{bad}_incomplete")

    if overall_wr is not None and recent_wr is not None:
        components["consistency"] = round(max(0.0, 1.0 - abs(overall_wr - recent_wr) / 100.0), 3)
    else:
        components["consistency"] = 0.5

    total = sum(WEIGHTS[k] * components.get(k, 0.0) for k in WEIGHTS)
    total *= (1.0 - CONTRADICTION_PENALTY * c_strength)
    total = round(max(0.0, min(1.0, total)), 4)

    return {"n": n, "overall_wr": overall_wr, "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "components": {k: components.get(k, 0.0) for k in WEIGHTS},
            "total": total, "contradictions": contradictions,
            "contradiction_strength": c_strength,
            "supporting": supporting_analysis(rows, baseline_wr),
            "missing": sorted(set(missing)), "recent_view": recent_view,
            "first_observed_at": (rows[0].get("completed_at") or rows[0].get("candle_time")) if rows else None,
            "last_observed_at": last_ts, "age_days": days}


# ---------------------------------------------------------------------------
# state classification (spec §3: never from sample size alone)
# ---------------------------------------------------------------------------
def state_for(n: int, total: float, c_strength: float,
              recent_wr: Optional[float], baseline_wr: Optional[float]) -> str:
    if n < MIN_N:
        return "INSUFFICIENT"
    collapsed = (recent_wr is not None and baseline_wr is not None
                 and recent_wr < baseline_wr - 15)
    if c_strength >= 0.6 and collapsed:
        return "CONTRADICTED"          # internal: historic support revoked now
    if n < 50:
        return "PRELIMINARY"
    if n < 100:
        return "SUPPORTED" if total >= 0.60 else "PRELIMINARY"
    strong = total >= 0.70 and not collapsed
    return "STRONGER" if strong else "SUPPORTED"


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def evidence_id_for(user_id: str, subject_type: str, subject_id: str) -> str:
    h = hashlib.sha1(f"{user_id}|{subject_type}|{subject_id}".encode()).hexdigest()
    return f"ev-{h[:12]}"


def subject_ids(strategy_id: str, market: str, session: Optional[str]) -> tuple:
    st = "strategy_market_session" if session else "strategy_market"
    sid = f"{strategy_id}:{market}" + (f":{session}" if session else "")
    return st, sid


def build_evidence(user_id: str, strategy_id: str, market: str,
                   session: Optional[str] = None, persist: bool = True,
                   rows: Optional[List[dict]] = None,
                   mx: Optional[dict] = None) -> dict:
    store = get_store()
    subject_type, subject_id = subject_ids(strategy_id, market, session)

    # strategy baseline from the learning matrix (never invented)
    from ..learning.matrix import build_matrix
    if mx is None:
        mx = build_matrix(user_id)
    st = (mx.get("strategies") or {}).get(strategy_id) or {}
    baseline_wr = st.get("win_rate")

    if rows is None:
        rows = _completed(store, user_id, strategy_id, market, session)
    sc = score_observations(rows, baseline_wr, store=store, strategy_id=strategy_id)
    state = state_for(sc["n"], sc["total"], sc["contradiction_strength"],
                      sc["recent_view"].get("recent_wr"), baseline_wr)

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "evidence_id": evidence_id_for(user_id, subject_type, subject_id),
        "userId": user_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "strategy_id": strategy_id,
        "instrument": market,
        "timeframe": None,          # per-signal TFs vary; not asserted
        "session": session,
        "regime": None,
        "conclusion": (f"{st.get('name') or strategy_id} on {market}"
                       + (f" during {session}" if session else "")
                       + f": {sc['n']} completed, WR {sc['overall_wr']}%"
                       + (f", avg {sc['avg_r']}R" if sc["avg_r"] is not None else "")),
        "state": state,
        "score": sc["total"],
        "score_components": sc["components"],
        "sample_size": sc["n"],
        "historical_similarity": sc["components"]["similarity"],
        "regime_consistency": sc["components"]["regime"],
        "oos_strength": sc["components"]["oos"],
        "walk_forward_strength": None,
        "recent_performance": sc["recent_view"].get("recent_wr"),
        "execution_quality": sc["components"]["execution"],
        "data_quality": sc["components"]["data_quality"],
        "contradiction_strength": sc["contradiction_strength"],
        "evidence_age_days": round(sc["age_days"], 2) if sc["age_days"] is not None else None,
        "first_observed_at": sc["first_observed_at"],
        "last_observed_at": sc["last_observed_at"],
        "last_refresh_at": now,
        "model_confidence": None,   # deliberately independent of any LLM
        "calibrated_probability": None,
        "calibration": "CALIBRATION_INSUFFICIENT",   # spec §16: never fabricate
        "historically_supported": bool(sc["n"] >= MIN_N and baseline_wr is not None
                                       and sc["overall_wr"] is not None
                                       and sc["overall_wr"] >= baseline_wr),
        "currently_supported": bool(sc["recent_view"].get("recent_wr") is not None
                                    and baseline_wr is not None
                                    and sc["recent_view"]["recent_wr"] >= baseline_wr - 5),
        "supporting_factors": sc["supporting"],
        "contradictions": sc["contradictions"],
        "missing_evidence": sc["missing"] + (["calibration"] if True else []),
        "limitations": [
            "classification, not a guarantee",
            "statistical association, not causation",
            "no claim transfers across regimes without new evidence",
        ],
        "recent_view": sc["recent_view"],
        "updated_at": now,
    }

    if persist:
        prev = store.get("evidence", doc["evidence_id"])
        if prev:
            doc["created_at"] = prev.get("created_at") or now
            doc["refresh_count"] = int(prev.get("refresh_count") or 0) + 1
            store.update("evidence", doc["evidence_id"], doc)
        else:
            doc["created_at"] = now
            doc["refresh_count"] = 0
            store.create("evidence", doc, doc_id=doc["evidence_id"])
    return doc


def refresh_all(user_id: str, limit: int = 40) -> List[dict]:
    """Recompute evidence for every strategy x market the user actually
    trades. ONE matrix build + ONE signal pass total (hot path - the old
    per-pair rebuilds blew past gateway timeouts on real data)."""
    from collections import defaultdict
    from ..learning.matrix import build_matrix
    mx = build_matrix(user_id)
    store = get_store()
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for s in store.list("signals", filters={"userId": user_id}, limit=2000):
        if s.get("completed") and s.get("strategy_id") and s.get("market"):
            groups[(s["strategy_id"], s["market"])].append(s)
    docs = []
    for key in sorted(groups)[:limit]:
        rows = groups[key]
        rows.sort(key=lambda x: str(x.get("completed_at") or x.get("candle_time") or ""))
        try:
            docs.append(build_evidence(user_id, key[0], key[1],
                                       persist=True, rows=rows, mx=mx))
        except Exception:
            # one malformed subject never blocks the rest (spec: fail loud,
            # not fatal) - record a visible incident-level marker instead
            store.create("evidence", {
                "evidence_id": f"ev-error-{key[0]}-{key[1]}",
                "userId": user_id, "subject_type": "error",
                "subject_id": f"{key[0]}:{key[1]}",
                "strategy_id": key[0], "instrument": key[1],
                "state": "ERROR", "score": None,
                "conclusion": "evidence build failed on this subject",
                "updated_at": datetime.now(timezone.utc).isoformat()})
    return docs


def list_evidence(user_id: str, subject_type: Optional[str] = None,
                  strategy_id: Optional[str] = None) -> List[dict]:
    store = get_store()
    filters: Dict[str, Any] = {"userId": user_id}
    if subject_type:
        filters["subject_type"] = subject_type
    if strategy_id:
        filters["strategy_id"] = strategy_id
    docs = store.list("evidence", filters=filters, limit=200)
    docs.sort(key=lambda d: str(d.get("last_refresh_at") or ""), reverse=True)
    return docs
