"""Autonomous Strategy Research Lab (user spec 2026-09-16, UPGRADE 2).

OBSERVE -> DETECT PATTERN -> CREATE HYPOTHESIS -> (user designs the
one-variable experiment) -> existing Experiment Engine -> approval.

Discovery scans completed signals for outcome divergence across segments
(regime / volatility band / session). Every finding is stored labeled
UNTESTED HYPOTHESIS with its evidence. The AI never modifies live
strategies and never creates multi-variable experiments: turning a
discovery into a testable hypothesis goes through the EXISTING
create_hypothesis validation (one experimentable variable only) and the
existing approval flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MIN_SEGMENT = 10          # segment sample size to even consider
MIN_OVERALL = 20          # overall sample size for the baseline
DIVERGENCE_PP = 15.0      # segment win rate must differ by this many points


def _vol_band(rank: Optional[float]) -> Optional[str]:
    if rank is None:
        return None
    if rank < 0.33:
        return "LOW_VOLATILITY"
    if rank > 0.66:
        return "HIGH_VOLATILITY"
    return "MID_VOLATILITY"


def _segments(feat: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if feat.get("regime"):
        out["regime"] = feat["regime"]
    vb = _vol_band(feat.get("volatility_rank"))
    if vb:
        out["volatility"] = vb
    if feat.get("session"):
        out["session"] = feat["session"]
    return out


def discover(user_id: str) -> List[Dict[str, Any]]:
    """Find outcome divergences in completed signals; store as hypotheses."""
    from .adaptive import features
    store = get_store = _store()
    completed = store.list("signals", filters={"userId": user_id, "completed": True},
                           limit=2000)
    created: List[Dict[str, Any]] = []

    by_strat: Dict[str, List[dict]] = {}
    for s in completed:
        by_strat.setdefault(s.get("strategy_id"), []).append(s)

    for strategy_id, sigs in by_strat.items():
        if strategy_id is None or len(sigs) < MIN_OVERALL:
            continue
        wins = sum(1 for s in sigs if s.get("outcome") == "WIN")
        overall_wr = 100.0 * wins / len(sigs)
        rs = [float(s.get("r_multiple", 0.0)) for s in sigs]
        overall_stats = {"n": len(sigs), "win_rate": round(overall_wr, 1),
                         "avg_r": round(sum(rs) / len(rs), 3)}

        buckets: Dict[tuple, List[dict]] = {}
        for s in sigs:
            feat = features(s)
            market = s.get("market")
            for seg_type, label in _segments(feat).items():
                buckets.setdefault((market, seg_type, label), []).append(s)

        for (market, seg_type, label), subset in sorted(buckets.items()):
            if len(subset) < MIN_SEGMENT:
                continue
            seg_wins = sum(1 for s in subset if s.get("outcome") == "WIN")
            seg_wr = 100.0 * seg_wins / len(subset)
            delta = round(seg_wr - overall_wr, 1)
            if abs(delta) < DIVERGENCE_PP:
                continue
            seg_rs = [float(s.get("r_multiple", 0.0)) for s in subset]
            seg_rs_sorted = sorted(seg_rs)
            median_r = round(seg_rs_sorted[len(seg_rs_sorted) // 2], 2)

            dedupe_key = f"{strategy_id}|{market}|{seg_type}|{label}"
            existing = store.list("research_hypotheses",
                                  filters={"dedupe_key": dedupe_key}, limit=1)
            if existing:
                continue

            seg_txt = label.replace("_", " ").lower()
            if seg_type == "session":
                claim = (f"{strategy_id} signals on {market} during the "
                         f"{seg_txt} session show a different outcome "
                         f"distribution ({seg_wr:.1f}% vs {overall_wr:.1f}% "
                         f"overall).")
            elif seg_type == "volatility":
                claim = (f"{strategy_id} on {market} appears "
                         f"{'weaker' if delta < 0 else 'stronger'} during "
                         f"{seg_txt} conditions ({seg_wr:.1f}% vs "
                         f"{overall_wr:.1f}% overall).")
            else:
                claim = (f"{strategy_id} on {market} shows different behavior "
                         f"in {seg_txt} regime ({seg_wr:.1f}% vs "
                         f"{overall_wr:.1f}% overall).")

            doc = store.create("research_hypotheses", {
                "userId": user_id,
                "strategy_id": strategy_id,
                "market": market,
                "segment_type": seg_type,
                "segment_label": label,
                "claim": claim,
                "kind": "UNTESTED HYPOTHESIS",
                "status": "DISCOVERED",
                "sample_size": len(subset),
                "segment_win_rate": round(seg_wr, 1),
                "segment_avg_r": round(sum(seg_rs) / len(seg_rs), 3),
                "segment_median_r": median_r,
                "overall": overall_stats,
                "divergence_pp": delta,
                "dedupe_key": dedupe_key,
                "evidence": [
                    {"signal_id": s.get("signal_id"), "outcome": s.get("outcome"),
                     "r": s.get("r_multiple"), "time": s.get("createdAt")}
                    for s in subset[:25]
                ],
                "note": ("A pattern observed in historical data. It is NOT a "
                         "fact about future performance and NOT a strategy "
                         "change. Test it with a one-variable experiment."),
                "createdAt": datetime.now(timezone.utc).isoformat(),
            })
            created.append(doc)
    return created


def design_experiment(user_id: str, research_doc: Dict[str, Any],
                      variable: str, new_value: Any) -> Dict[str, Any]:
    """Turn a discovery into a ONE-VARIABLE experiment hypothesis.

    Uses the existing create_hypothesis validation: only experimentable
    strategy variables are accepted, values are range-checked, and the
    normal PROPOSED -> experiment -> READY_FOR_REVIEW -> approval flow
    takes over from here. Nothing is applied to the live strategy.
    """
    from .hypotheses import create_hypothesis
    reason = (f"Research discovery ({research_doc.get('segment_type')}: "
              f"{research_doc.get('segment_label')}, {research_doc.get('sample_size')} "
              f"signals, {research_doc.get('segment_win_rate')}% vs "
              f"{(research_doc.get('overall') or {}).get('win_rate')}% overall): "
              f"{research_doc.get('claim')}")
    return create_hypothesis(
        user_id, research_doc["strategy_id"], variable, new_value,
        reason=reason,
        expected_effect=("Reduce the outcome divergence observed in this "
                         "segment - to be proven or disproven by the "
                         "one-variable experiment."),
        source_lesson=research_doc.get("id"),
    )


def _store():
    from ..db.store import get_store
    return get_store()
