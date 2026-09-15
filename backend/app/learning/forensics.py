"""Forensic analysis - runs automatically when a signal completes.

Investigates WHY the setup won or lost by comparing its DNA against every
historical comparable signal (same strategy + market). Every finding is
explicitly labeled:

  FACT                 - recorded system data (levels hit, duration, ...)
  POSSIBLE_EXPLANATION - supported by comparable-signal evidence
  HYPOTHESIS           - plausible but untested

No speculation is ever presented as fact; tiny samples are flagged.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..db.store import get_store

MIN_SAMPLE = 10   # below this, findings are marked INSUFFICIENT SAMPLE


def _comparables(signal: Dict[str, Any], store) -> List[Dict[str, Any]]:
    docs = store.list("signals", filters={
        "strategy_id": signal.get("strategy_id"),
        "market": signal.get("market"),
        "completed": True}, limit=500)
    return [d for d in docs if d["id"] != signal["id"] and d.get("r_multiple") is not None]


def _stats(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(subset)
    if not n:
        return {"n": 0}
    losses = sum(1 for s in subset if s.get("outcome") == "LOSS")
    return {"n": n,
            "loss_rate": round(100.0 * losses / n, 1),
            "avg_r": round(sum(float(s.get("r_multiple") or 0) for s in subset) / n, 2)}


def _with_without(comps: List[Dict[str, Any]], pred) -> Dict[str, Any]:
    with_ = [s for s in comps if pred(s)]
    without_ = [s for s in comps if not pred(s)]
    return {"with": _stats(with_), "without": _stats(without_)}


def _dna(signal: Dict[str, Any]) -> Dict[str, Any]:
    return signal.get("dna") or {}


def analyze(signal: Dict[str, Any], store=None) -> Dict[str, Any]:
    """Build the forensic report. Never raises."""
    try:
        store = store or get_store()
        comps = _comparables(signal, store)
        dna = _dna(signal)
        findings: List[Dict[str, Any]] = []

        # ---- FACTS (recorded data, always true) --------------------------
        facts = {
            "result": f"{signal.get('outcome')} ({signal.get('status')})",
            "r_multiple": signal.get("r_multiple"),
            "levels_hit": signal.get("tp_hits") or 0,
            "entry": signal.get("entry"), "exit": signal.get("exit_price"),
            "timeframe": signal.get("timeframe"),
        }
        findings.append({"label": "Recorded outcome", "kind": "FACT", "detail": facts})

        if dna:
            findings.append({"label": "Conditions at entry", "kind": "FACT", "detail": {
                "regime": dna.get("regime"), "volatility": dna.get("volatility"),
                "momentum": dna.get("momentum"), "mtf": dna.get("mtf"),
                "news_proximity_min": dna.get("news_proximity_min"),
                "session": dna.get("session")}})

        if len(comps) < MIN_SAMPLE:
            findings.append({
                "label": "INSUFFICIENT SAMPLE",
                "kind": "HYPOTHESIS",
                "detail": f"Only {len(comps)} comparable completed signals - "
                          "no reliable pattern can be claimed yet."})
            return {"findings": findings, "comparables": _evidence(comps),
                    "sample_size": len(comps)}

        # ---- evidence-based explanations ---------------------------------
        if dna:
            # 1) MTF disagreement with the trade direction
            mtf = dna.get("mtf") or {}
            sgn_map = {"BULLISH": 1, "BEARISH": -1}
            want = sgn_map.get(signal.get("direction") == "BUY" and "BULLISH" or "BEARISH", 1)
            disagree = [k for k, v in mtf.items() if sgn_map.get(v, 0) not in (want, 0)]
            if disagree:
                def _pred(s):
                    d = (s.get("dna") or {}).get("mtf") or {}
                    dmap = {"BULLISH": 1, "BEARISH": -1}
                    return any(dmap.get(v, 0) not in (want, 0) for v in d.values())
                ww = _with_without(comps, _pred)
                findings.append({
                    "label": f"MTF disagreement ({', '.join(disagree)})",
                    "kind": "POSSIBLE_EXPLANATION",
                    "detail": "Signals with any opposing higher-timeframe trend "
                              f"show {ww['with'].get('loss_rate', 0)}% loss rate "
                              f"({ww['with'].get('n', 0)} signals) vs "
                              f"{ww['without'].get('loss_rate', 0)}% without it "
                              f"({ww['without'].get('n', 0)} signals)."})

            # 2) high volatility at entry
            if dna.get("volatility") == "HIGH":
                ww = _with_without(comps, lambda s: (s.get("dna") or {}).get("volatility") == "HIGH")
                findings.append({
                    "label": "High volatility at entry",
                    "kind": "POSSIBLE_EXPLANATION",
                    "detail": f"High-vol entries: {ww['with'].get('loss_rate', 0)}% losses "
                              f"on {ww['with'].get('n', 0)} signals; others: "
                              f"{ww['without'].get('loss_rate', 0)}% on {ww['without'].get('n', 0)}."})

            # 3) news within +/-30 min
            npm = dna.get("news_proximity_min")
            if isinstance(npm, int) and abs(npm) <= 30:
                def _near(s):
                    v = (s.get("dna") or {}).get("news_proximity_min")
                    return isinstance(v, int) and abs(v) <= 30
                ww = _with_without(comps, _near)
                findings.append({
                    "label": f"News proximity ({npm} min to {dna.get('news_event')})",
                    "kind": "POSSIBLE_EXPLANATION",
                    "detail": f"Near-news entries: {ww['with'].get('loss_rate', 0)}% losses "
                              f"({ww['with'].get('n', 0)} signals) vs "
                              f"{ww['without'].get('loss_rate', 0)}% otherwise."})

        # ---- untested hypothesis ------------------------------------------
        if dna and dna.get("regime"):
            findings.append({
                "label": f"Regime was {dna.get('regime')}",
                "kind": "HYPOTHESIS",
                "detail": "Whether this regime systematically hurts this strategy is "
                          "UNTESTED. Regime performance analysis can test it once "
                          "enough regime-tagged signals exist."})

        return {"findings": findings, "comparables": _evidence(comps),
                "sample_size": len(comps)}
    except Exception as exc:
        return {"findings": [{"label": "Forensic analysis unavailable",
                              "kind": "FACT", "detail": type(exc).__name__}],
                "comparables": [], "sample_size": 0}


def _evidence(comps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The actual historical signals behind the analysis (most recent 25)."""
    out = []
    for s in sorted(comps, key=lambda x: str(x.get("completed_at") or x.get("candle_time")), reverse=True)[:25]:
        out.append({"signal_id": s.get("signal_id"), "id": s.get("id"),
                    "outcome": s.get("outcome"), "r": s.get("r_multiple"),
                    "status": s.get("status"),
                    "time": str(s.get("candle_time") or "")[:16]})
    return out
