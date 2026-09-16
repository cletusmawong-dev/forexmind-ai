"""Adaptive Signal Intelligence (user spec 2026-09-16, UPGRADE 1).

Evaluates the QUALITY of an already-valid signal using historical evidence.
The two strategy engines remain the only signal generators - this layer never
creates signals and NEVER modifies entry, SL or TP. It only explains how much
historical support exists for the signal that was already generated.

Principles:
- Deterministic, explainable similarity (no vague AI matching).
- Weights are documented constants and are copied into every evaluation.
- MIN_SAMPLE protection: small samples -> "INSUFFICIENT SAMPLE", never a
  fake-precise win rate.
- Every number comes from stored signals. Missing data is listed, never
  invented.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ADAPTIVE_VERSION = "1.0"

# Quality-score component weights (documented, configurable). Sum = 1.0.
WEIGHTS: Dict[str, float] = {
    "historical_evidence": 0.35,   # outcomes of similar completed signals
    "htf_alignment": 0.20,         # higher-timeframe agreement at signal time
    "regime_evidence": 0.10,       # historical outcome in this regime
    "volatility_suitability": 0.15,  # ATR percentile suitability
    "session_behavior": 0.10,      # historical outcome in this session
    "news_environment": 0.10,      # proximity of high-impact news
}

# Deterministic similarity weights for comparable-signal matching. Sum = 1.0.
SIM_WEIGHTS: Dict[str, float] = {
    "regime": 0.30,                # same market regime
    "volatility": 0.25,            # ATR percentile within +-0.15
    "session": 0.20,               # same session
    "htf_alignment": 0.15,         # same MTF trend structure
    "news_proximity": 0.10,        # same news-proximity bucket
}

MIN_SAMPLE = 10          # below this -> INSUFFICIENT SAMPLE (no fake stats)
SEGMENT_MIN_SAMPLE = 10  # per-segment historical stats (regime/session)
SIM_THRESHOLD = 0.60     # minimum weighted similarity to count as comparable
MAX_COMPARABLES = 25
RELIABILITY_HIGH = 20    # comparables >= this -> HIGH reliability


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _bucket_vol(rank: Optional[float]) -> Optional[str]:
    if rank is None:
        return None
    if rank < 0.33:
        return "low"
    if rank > 0.66:
        return "high"
    return "mid"


def _news_bucket(prox_min: Optional[float]) -> Optional[str]:
    if prox_min is None:
        return None
    if prox_min <= 30:
        return "near"
    if prox_min <= 120:
        return "mid"
    return "far"


def features(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Normalized feature snapshot. Reuses Signal DNA instead of duplicating."""
    dna = signal.get("dna") or {}
    mc = signal.get("market_conditions") or {}
    candle_time = str(signal.get("candle_time") or "")
    tod = dow = None
    try:
        import pandas as pd
        ts = pd.Timestamp(candle_time)
        tod = int(ts.hour)
        dow = int(ts.dayofweek)
    except Exception:
        pass
    return {
        "strategy_id": signal.get("strategy_id"),
        "strategy_version": signal.get("strategy_version"),
        "market": signal.get("market"),
        "direction": signal.get("direction"),
        "timeframe": signal.get("timeframe"),
        "session": dna.get("session") or mc.get("session"),
        "regime": dna.get("regime"),
        "regime_confidence": dna.get("regime_confidence"),
        "volatility_rank": dna.get("volatility_rank"),
        "volatility_bucket": _bucket_vol(dna.get("volatility_rank")),
        "momentum": dna.get("momentum"),
        "mtf": dna.get("mtf"),
        "mtf_alignment": dna.get("mtf_alignment"),
        "atr14": dna.get("atr14"),
        "atr_pct_of_price": dna.get("atr_pct_of_price"),
        "ema9": dna.get("ema9"),
        "ema21": dna.get("ema21"),
        "news_proximity_min": dna.get("news_proximity_min"),
        "news_bucket": _news_bucket(dna.get("news_proximity_min")),
        "news_event": dna.get("news_event"),
        "time_of_day": tod,
        "day_of_week": dow,
        "entry": signal.get("entry"),
        "score_components": signal.get("score_components") or {},
    }


def _similarity(feat: Dict[str, Any], other_feat: Dict[str, Any]) -> Optional[float]:
    """Deterministic weighted similarity of two feature snapshots (0..1).

    Hard requirements: same strategy, same market, same direction. The
    weighted components then grade the remaining conditions. Returns None
    when hard requirements fail or neither side has comparable data.
    """
    if (feat.get("strategy_id") != other_feat.get("strategy_id")
            or feat.get("market") != other_feat.get("market")
            or feat.get("direction") != other_feat.get("direction")):
        return None

    parts: List[tuple] = []  # (weight, score or None)

    # regime
    if feat.get("regime") is not None and other_feat.get("regime") is not None:
        parts.append((SIM_WEIGHTS["regime"],
                      1.0 if feat["regime"] == other_feat["regime"] else 0.0))
    # volatility
    if feat.get("volatility_rank") is not None and other_feat.get("volatility_rank") is not None:
        diff = abs(float(feat["volatility_rank"]) - float(other_feat["volatility_rank"]))
        parts.append((SIM_WEIGHTS["volatility"], _clamp01(1.0 - diff / 0.3)))
    # session
    if feat.get("session") and other_feat.get("session"):
        parts.append((SIM_WEIGHTS["session"],
                      1.0 if feat["session"] == other_feat["session"] else 0.0))
    # HTF alignment structure (per-frame trends must match exactly)
    if feat.get("mtf") and other_feat.get("mtf"):
        same = sum(1 for tf, v in feat["mtf"].items() if other_feat["mtf"].get(tf) == v)
        parts.append((SIM_WEIGHTS["htf_alignment"], same / max(1, len(feat["mtf"]))))
    # news proximity bucket
    if feat.get("news_bucket") and other_feat.get("news_bucket"):
        parts.append((SIM_WEIGHTS["news_proximity"],
                      1.0 if feat["news_bucket"] == other_feat["news_bucket"] else 0.0))

    if not parts:
        return None
    used = sum(w for w, _ in parts)
    if used <= 0:
        return None
    return round(sum(w * s for w, s in parts) / used, 4)


def _historical_stats(store, signal: Dict[str, Any], feat: Dict[str, Any]) -> Dict[str, Any]:
    """Find similar completed signals and compute their outcome stats."""
    completed = store.list("signals", filters={
        "userId": signal.get("userId"), "completed": True}, limit=2000)
    scored = []
    for other in completed:
        if other.get("id") == signal.get("id"):
            continue  # never compare a signal with itself
        of = features(other)
        sim = _similarity(feat, of)
        if sim is not None and sim >= SIM_THRESHOLD:
            scored.append((sim, other))
    scored.sort(key=lambda t: (-t[0], str(t[1].get("createdAt", ""))), reverse=True)
    comparables = []
    for sim, other in scored[:MAX_COMPARABLES]:
        comparables.append({
            "signal_id": other.get("signal_id"),
            "similarity": sim,
            "outcome": other.get("outcome"),
            "r": other.get("r_multiple"),
            "regime": (other.get("dna") or {}).get("regime"),
            "session": (other.get("dna") or {}).get("session") or
                       (other.get("market_conditions") or {}).get("session"),
            "time": other.get("createdAt"),
        })
    n = len(comparables)
    if n < MIN_SAMPLE:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "similar_signals": n,
            "note": (f"Insufficient sample: {n} comparable completed signal(s) "
                     f"found; at least {MIN_SAMPLE} required before any "
                     "reliable statistic can be quoted."),
            "comparables": comparables,
        }
    rs = [float(c["r"]) for c in comparables if isinstance(c["r"], (int, float))]
    wins = sum(1 for c in comparables if c["outcome"] == "WIN")
    return {
        "status": "OK",
        "similar_signals": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(100.0 * wins / n, 1),
        "total_r": round(sum(rs), 2) if rs else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "comparables": comparables,
    }


def _segment_stat(store, signal: Dict[str, Any], feat: Dict[str, Any],
                  key_fn) -> Dict[str, Any]:
    """Historical outcome for one segment (regime/session), or honest gap."""
    completed = store.list("signals", filters={
        "userId": signal.get("userId"), "completed": True}, limit=2000)
    same = [s for s in completed
            if s.get("id") != signal.get("id")
            and s.get("strategy_id") == feat["strategy_id"]
            and s.get("market") == feat["market"]]
    target = key_fn(feat)
    if target is None:
        return {"status": "MISSING_DATA", "note": "segment value unavailable on this signal"}
    subset = [s for s in same if key_fn(features(s)) == target]
    if len(subset) < SEGMENT_MIN_SAMPLE:
        return {"status": "INSUFFICIENT_SAMPLE", "sample_size": len(subset),
                "note": (f"Insufficient sample ({len(subset)}) for {target} - "
                         "no statistic quoted.")}
    rs = [float(s.get("r_multiple", 0.0)) for s in subset]
    wins = sum(1 for s in subset if s.get("outcome") == "WIN")
    return {"status": "OK", "segment": target, "sample_size": len(subset),
            "win_rate": round(100.0 * wins / len(subset), 1),
            "avg_r": round(sum(rs) / len(subset), 3)}


def evaluate(signal: Dict[str, Any], store) -> Dict[str, Any]:
    """Full Adaptive Quality evaluation. Never raises; never touches levels."""
    feat = features(signal)
    missing: List[str] = []
    components: Dict[str, float] = {}
    notes: Dict[str, Any] = {}

    # 1) historical evidence (similar completed signals)
    hist = _historical_stats(store, signal, feat)
    if hist["status"] == "OK":
        wr = _clamp01(float(hist["win_rate"]) / 100.0)
        exp = _clamp01((float(hist["avg_r"]) + 0.5) / 1.5)  # -0.5R..+1.0R band
        components["historical_evidence"] = round(100.0 * (0.5 * wr + 0.5 * exp), 1)
    else:
        missing.append("historical_evidence")
    notes["historical"] = {k: v for k, v in hist.items() if k != "comparables"}
    notes["historical"]["comparables"] = hist.get("comparables", [])[:10]

    # 2) HTF alignment from the signal's own score components (0..60 pts)
    sc = feat["score_components"]
    htf_pts = sc.get("HTF alignment")
    if isinstance(htf_pts, (int, float)):
        components["htf_alignment"] = round(100.0 * _clamp01(htf_pts / 60.0), 1)
    elif sc.get("1H EMA alignment"):  # pre-2026-09-16 signals
        components["htf_alignment"] = 100.0 * _clamp01(float(sc["1H EMA alignment"]) / 15.0)
    else:
        missing.append("htf_alignment")

    # 3) regime evidence (historical, same strategy+market+regime)
    reg = _segment_stat(store, signal, feat, lambda f: f.get("regime"))
    if reg["status"] == "OK":
        components["regime_evidence"] = round(
            100.0 * (0.5 * _clamp01(reg["win_rate"] / 100.0)
                     + 0.5 * _clamp01((reg["avg_r"] + 0.5) / 1.5)), 1)
    else:
        missing.append("regime_evidence")
    notes["regime"] = reg

    # 4) volatility suitability (ATR percentile closeness to the 0.6 sweet spot)
    vr = feat.get("volatility_rank")
    if isinstance(vr, (int, float)):
        components["volatility_suitability"] = round(
            100.0 * _clamp01(1.0 - abs(float(vr) - 0.6) * 1.6), 1)
    else:
        missing.append("volatility_suitability")

    # 5) session behavior (historical, same strategy+market+session)
    ses = _segment_stat(store, signal, feat, lambda f: f.get("session"))
    if ses["status"] == "OK":
        components["session_behavior"] = round(
            100.0 * (0.5 * _clamp01(ses["win_rate"] / 100.0)
                     + 0.5 * _clamp01((ses["avg_r"] + 0.5) / 1.5)), 1)
    else:
        missing.append("session_behavior")
    notes["session"] = ses

    # 6) news environment
    nb = feat.get("news_bucket")
    if nb is None:
        components["news_environment"] = 100.0  # no news within window = clear
        notes["news"] = {"status": "OK", "note": "no high-impact news near signal time"}
    elif nb == "near":
        components["news_environment"] = 20.0
        notes["news"] = {"status": "OK", "note": "high-impact news within 30 min"}
    elif nb == "mid":
        components["news_environment"] = 60.0
        notes["news"] = {"status": "OK", "note": "news within 2 hours"}
    else:
        components["news_environment"] = 100.0
        notes["news"] = {"status": "OK", "note": "news more than 2 hours away"}

    # weighted total over AVAILABLE components only (weights renormalized)
    used = [(k, WEIGHTS[k]) for k in components if k in WEIGHTS]
    if used:
        wsum = sum(w for _, w in used)
        score = int(round(sum(components[k] * w for k, w in used) / wsum))
    else:
        score = 0
        missing.append("all_components")

    if score >= 75:
        verdict = "STRONG historical support"
    elif score >= 55:
        verdict = "MODERATE historical support"
    else:
        verdict = "WEAK historical support"
    n_comp = hist.get("similar_signals", 0)
    reliability = ("HIGH" if n_comp >= RELIABILITY_HIGH
                   else "MEDIUM" if n_comp >= MIN_SAMPLE else "LOW")

    # WHY-checklist (plain ASCII, evidence-tied)
    checks: List[Dict[str, str]] = []
    if isinstance(htf_pts, (int, float)) and htf_pts >= 40:
        checks.append({"label": "Higher timeframes aligned with the direction"})
    if components.get("historical_evidence") is not None and hist["status"] == "OK":
        checks.append({"label": (f"{hist['similar_signals']} comparable historical "
                                 f"signals: {hist['wins']}W/{hist['losses']}L, "
                                 f"{hist['win_rate']}% win rate, {hist['total_r']}R total")})
    if reg.get("status") == "OK":
        checks.append({"label": (f"{feat.get('regime')} regime historically "
                                 f"{reg['win_rate']}% win rate ({reg['sample_size']} signals)")})
    if ses.get("status") == "OK":
        checks.append({"label": (f"{feat.get('session')} session historically "
                                 f"{ses['win_rate']}% win rate ({ses['sample_size']} signals)")})
    if components.get("volatility_suitability", 0) >= 60:
        checks.append({"label": "Volatility percentile in a suitable band"})
    if nb is None or nb in ("far", "mid"):
        checks.append({"label": "No major news conflict at signal time"})
    if not checks:
        checks.append({"label": "Insufficient evidence for a positive checklist - "
                                "treat this signal with caution"})

    return {
        "adaptive_version": ADAPTIVE_VERSION,
        "weights": dict(WEIGHTS),
        "similarity_weights": dict(SIM_WEIGHTS),
        "similarity_threshold": SIM_THRESHOLD,
        "min_sample": MIN_SAMPLE,
        "score": score,
        "verdict": verdict,
        "reliability": reliability,
        "components": components,
        "missing_data": missing,
        "historical": notes["historical"],
        "regime_evidence": notes["regime"],
        "session_evidence": notes["session"],
        "news": notes["news"],
        "checks": checks,
        "features": feat,
        "informational_only": True,
        "note": ("Adaptive Quality evaluates existing signals. It never changes "
                 "entry, stop-loss or take-profit levels."),
    }


def evaluate_and_store(store, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Evaluate a signal and persist the result on its doc (best effort)."""
    try:
        result = evaluate(signal, store)
        store.update("signals", signal["id"], {"adaptive": result})
        return result
    except Exception:
        return None
