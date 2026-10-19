"""EVIDENCE-BASED TRADE PROBABILITY (owner brief 2026-10-10, section 5).

Calibrated probability that TP is reached before SL, estimated ONLY from the
account's own closed-signal history and only when the sample is large
enough. Where evidence is insufficient the estimate is explicitly marked
INSUFFICIENT_EVIDENCE and callers must omit any percentage (never invent
confidence, never present a heuristic score as a probability).

Method: stratify closed signals by (strategy, market, direction, ATR-regime
bucket). Within a stratum, p = wins / n shrunk toward 0.5 with a Laplace
prior whose weight is MIN_SAMPLE (calibration shrinkage, not optimism).
A 90% Wilson interval is included so callers can see the uncertainty.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

MIN_SAMPLE = 30            # below this: INSUFFICIENT_EVIDENCE (no number)


def _atr_bucket(atr_pct: Optional[float]) -> str:
    if atr_pct is None:
        return "na"
    if atr_pct < 0.4:
        return "low_vol"
    if atr_pct > 1.0:
        return "high_vol"
    return "mid_vol"


def _wilson(p: float, n: int, z: float = 1.64) -> Dict[str, float]:
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"low": round(max(0.0, centre - half), 3),
            "high": round(min(1.0, centre + half), 3)}


def estimate(strategy_id: str, market: str, direction: str,
             atr_pct: Optional[float], closed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calibrated P(TP before SL) for a new signal from closed history.

    `closed` rows need: strategy_id, market, direction, outcome (WIN/LOSS),
    optional atr_pct. Returns {"status": "OK", "p_tp": float, "n": int,
    "wilson90": {...}} or {"status": "INSUFFICIENT_EVIDENCE", "n": int}."""
    try:
        bucket = _atr_bucket(atr_pct)
        rows = [r for r in (closed or [])
                if str(r.get("strategy_id")) == strategy_id
                and str(r.get("market")) == market
                and str(r.get("direction")) == direction
                and str(r.get("outcome")) in ("WIN", "LOSS")
                and _atr_bucket(r.get("atr_pct")) == bucket]
        n = len(rows)
        if n < MIN_SAMPLE:
            # fall back to the WIDER stratum (no ATR bucket) before giving up
            rows = [r for r in (closed or [])
                    if str(r.get("strategy_id")) == strategy_id
                    and str(r.get("market")) == market
                    and str(r.get("direction")) == direction
                    and str(r.get("outcome")) in ("WIN", "LOSS")]
            n = len(rows)
            bucket = "all"
            if n < MIN_SAMPLE:
                return {"status": "INSUFFICIENT_EVIDENCE", "n": n,
                        "min_required": MIN_SAMPLE,
                        "note": "no probability is published without evidence"}
        wins = sum(1 for r in rows if str(r.get("outcome")) == "WIN")
        p = (wins + MIN_SAMPLE * 0.5) / (n + MIN_SAMPLE)   # Laplace shrink
        return {"status": "OK", "p_tp": round(p, 3), "n": n,
                "wins": wins, "stratum": bucket,
                "wilson90": _wilson(p, n + MIN_SAMPLE),
                "method": "laplace_shrunk_hit_rate_closed_signals"}
    except Exception as exc:
        return {"status": "INSUFFICIENT_EVIDENCE",
                "note": f"estimation error: {type(exc).__name__}"}


def attach_to_signal(signal_doc: Dict[str, Any], store) -> None:
    """Best-effort: attach the calibrated estimate onto a new signal doc and
    its Telegram payload decision (evidence only)."""
    try:
        closed = store.list("signals", filters={"completed": True}, limit=1000) or []
        est = estimate(signal_doc.get("strategy_id", ""),
                       signal_doc.get("market", ""),
                       signal_doc.get("direction", ""),
                       (signal_doc.get("market_conditions") or {}).get("atr_pct"),
                       closed)
        if est.get("status") == "OK":
            signal_doc["tp_probability"] = est
        else:
            signal_doc["tp_probability"] = {"status": "INSUFFICIENT_EVIDENCE"}
    except Exception:
        signal_doc["tp_probability"] = {"status": "INSUFFICIENT_EVIDENCE"}
