"""Probability-driven TP level selection (user directive 2026-09-25).

OLD BEHAVIOR: EXECUTION_TP_LEVEL=2 - every trade targets TP2, regardless of
how far price actually travels in the current context.

NEW: a constant-learning TP manager. Every completed signal records its MFE
(the best R the trade reached). Grouping comparable trades by
strategy + market + session (+ volatility regime) gives an EMPIRICAL
PROBABILITY that price reaches 1R / 2R / 3R in that context:

    p_k = share of comparable trades whose MFE reached kR

and each TP level gets an expected value in R:

    EV(k) = p_k * k  -  (1 - p_k) * 1        (miss = SL = -1R)

The manager targets the level with the highest EV. Guard rails:

  * minimum sample per bucket (TP_PROB_MIN_SAMPLE, default 20) - below it
    the manager falls back to the configured static level (default TP2);
    the probability model GROWS with the constant learning loop and takes
    over per bucket as evidence accumulates;
  * MFE is an optimistic upper bound (price TOUCHED the level, not
    necessarily filled there) - EV is therefore slightly optimistic and the
    bucket comparison (which level is best) remains meaningful;
  * every selection is recorded on the signal doc (probabilities, EVs,
    sample size, source) - fully auditable in the journal/autopsy.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..db.store import get_store


def _bucket_key(sig: dict) -> Dict[str, Any]:
    mc = sig.get("market_conditions") or {}
    return {"strategy_id": sig.get("strategy_id"),
            "market": sig.get("market"),
            "session": mc.get("session"),
            "volatility_regime": mc.get("volatility_regime")}


def _bucket_stats(comps: List[dict], direction: str) -> Optional[Dict[str, float]]:
    """Empirical P(MFE >= kR) for the comparable set."""
    mfe = [float(c.get("mfe_r") or 0.0) for c in comps
           if c.get("mfe_r") is not None]
    n = len(mfe)
    if not n:
        return None
    # SELL trades: the recorded mfe_r is already direction-normalized (the
    # tracker computes favorable excursion in R per direction), so the same
    # thresholds apply for both sides.
    return {"n": float(n),
            "p1": sum(1 for m in mfe if m >= 1.0) / n,
            "p2": sum(1 for m in mfe if m >= 2.0) / n,
            "p3": sum(1 for m in mfe if m >= 3.0) / n}


def pick_tp_level(sig: dict, min_sample: int = 20,
                  fallback_level: int = 2,
                  store=None) -> Dict[str, Any]:
    """Choose the TP level for this trade. Returns an auditable record:
    {level, source, p1, p2, p3, ev1, ev2, ev3, n, bucket}."""
    store = store or get_store()
    mc = sig.get("market_conditions") or {}
    key = _bucket_key(sig)
    rec: Dict[str, Any] = {"level": fallback_level, "source": "default",
                           "p1": None, "p2": None, "p3": None,
                           "ev1": None, "ev2": None, "ev3": None,
                           "n": 0,
                           "bucket": f"{key['strategy_id']}/{key['market']}/"
                                     f"{key['session']}/{key['volatility_regime']}"}
    try:
        comps = store.list("signals", filters={
            "userId": sig.get("userId"), "strategy_id": key["strategy_id"],
            "market": key["market"], "completed": True}, limit=1000)
        # same session/regime first; widen to market-wide if the strict
        # bucket is too thin (more evidence beats tighter segmentation)
        strict = [c for c in comps
                  if (c.get("market_conditions") or {}).get("session") == key["session"]
                  and (c.get("market_conditions") or {}).get("volatility_regime") == key["volatility_regime"]]
        pool = strict if len(strict) >= min_sample else comps
        stats = _bucket_stats(pool, sig.get("direction") or "BUY")
        if not stats or stats["n"] < min_sample:
            return rec
        p1, p2, p3 = stats["p1"], stats["p2"], stats["p3"]
        evs = {1: p1 * 1.0 - (1 - p1),
               2: p2 * 2.0 - (1 - p2),
               3: p3 * 3.0 - (1 - p3)}
        best = max(evs, key=lambda k: evs[k])
        rec.update({"level": int(best), "source": "probability",
                    "p1": round(p1, 3), "p2": round(p2, 3), "p3": round(p3, 3),
                    "ev1": round(evs[1], 3), "ev2": round(evs[2], 3),
                    "ev3": round(evs[3], 3), "n": int(stats["n"])})
        return rec
    except Exception:
        return rec
