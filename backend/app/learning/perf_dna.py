"""STRATEGY PERFORMANCE DNA (3.0 spec section 12) - derived, never invented.

Strengths/weaknesses computed STRICTLY from the learning matrix cells
(strategy x market x session x regime, min sample per segment). Written to the
strategy doc as read-only `performance_dna` + served via API. No LLM involved.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MIN_SEGMENT_N = 5


def _segments(cells: List[dict], strategy_id: str, key: str) -> List[dict]:
    agg: Dict[str, dict] = {}
    for c in cells:
        if c["strategy_id"] != strategy_id or not c.get(key):
            continue
        a = agg.setdefault(c[key], {"n": 0, "wins": 0.0, "r": 0.0})
        n = c.get("n") or 0
        a["n"] += n
        a["wins"] += (c.get("win_rate") or 0) * n / 100.0
        a["r"] += (c.get("avg_r") or 0) * n
    rows = []
    for name, a in agg.items():
        if a["n"] >= MIN_SEGMENT_N:
            rows.append({"name": name, "n": a["n"],
                         "wr": round(100 * a["wins"] / a["n"], 1),
                         "avg_r": round(a["r"] / a["n"], 3)})
    rows.sort(key=lambda r: (r["wr"], r["avg_r"]), reverse=True)
    return rows


def build_dna(user_id: str, strategy_id: str) -> dict:
    from ..learning.matrix import build_matrix
    mx = build_matrix(user_id)
    cells = mx.get("cells", [])
    st = (mx.get("strategies") or {}).get(strategy_id) or {}
    by_market = _segments(cells, strategy_id, "market")
    by_session = [r for r in _segments(cells, strategy_id, "session")]
    by_regime = _segments(cells, strategy_id, "regime")

    def _strong(rows): return rows[:2] if rows else []
    def _weak(rows): return sorted(rows, key=lambda r: (r["wr"], r["avg_r"]))[:2] if rows else []

    total_n = int(st.get("n") or 0)
    return {
        "strategy_id": strategy_id,
        "sample_size": total_n,
        "sufficient": total_n >= 20,
        "strengths": {"markets": _strong(by_market), "sessions": _strong(by_session),
                      "regimes": _strong(by_regime)},
        "weaknesses": {"markets": _weak(by_market), "sessions": _weak(by_session),
                       "regimes": _weak(by_regime)},
        "detail": {"by_market": by_market, "by_session": by_session,
                   "by_regime": by_regime},
        "basis": f"derived from {len(cells)} matrix cells, min segment n={MIN_SEGMENT_N}",
        "note": "statistical association from completed signals - not guaranteed",
    }
