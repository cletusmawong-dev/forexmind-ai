"""Advanced Intelligence routes - Signal DNA, forensics, replay, regimes,
strategy versions + rollback, Learning Lab observations (SPEC §27-§53).

All endpoints are informational or explicit-user-action only. Nothing here can
auto-modify a live strategy or enable MT5 execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..market_data import candle_store
from ..learning import forensics
from ..learning import regime
from ..state import State
from .deps import get_user_id

router = APIRouter(tags=["intelligence"])


def _own_signal(signal_id: str, user_id: str) -> Dict[str, Any]:
    doc = State.store.get("signals", signal_id)
    if not doc:
        # signals are also looked up by their public signal_id
        found = State.store.list("signals", filters={"signal_id": signal_id}, limit=1)
        doc = found[0] if found else None
    if not doc:
        raise HTTPException(404, "Signal not found")
    if doc.get("userId") not in (None, user_id):
        raise HTTPException(403, "Not your signal")
    return doc


@router.get("/signals/{signal_id}/dna")
def signal_dna(signal_id: str, user_id: str = Depends(get_user_id)):
    doc = _own_signal(signal_id, user_id)
    dna = doc.get("dna")
    return {
        "signal_id": signal_id,
        "dna": dna,
        "captured": dna is not None,
        "note": None if dna is not None else
                "Signal predates DNA capture - no snapshot is stored for it.",
    }


@router.get("/signals/{signal_id}/forensics")
def signal_forensics(signal_id: str, user_id: str = Depends(get_user_id)):
    doc = _own_signal(signal_id, user_id)
    stored = doc.get("forensics")
    if stored:
        return {"signal_id": signal_id, "forensics": stored, "source": "stored"}
    if not doc.get("completed"):
        return {"signal_id": signal_id, "forensics": None, "source": "none",
                "note": "Forensics run when the signal completes."}
    result = forensics.analyze(doc)  # never raises
    try:
        State.store.update("signals", doc["id"], {"forensics": result})
    except Exception:
        pass
    return {"signal_id": signal_id, "forensics": result, "source": "computed"}


@router.get("/signals/{signal_id}/replay")
def signal_replay(signal_id: str, user_id: str = Depends(get_user_id),
                  before: int = Query(60, ge=5, le=200),
                  after: int = Query(240, ge=20, le=400)):
    """Forensic replay: stored candles around the signal + levels + outcome."""
    doc = _own_signal(signal_id, user_id)
    market = doc.get("market", "")
    candles = candle_store.history(market, limit=candle_store.KEEP)

    sig_ts = _parse_ts(doc.get("candle_time") or doc.get("created_at"))
    window: List[Dict[str, Any]] = []
    coverage = "unavailable"
    note = "No stored candles cover this signal yet (storage began recently)."
    if sig_ts is not None and candles:
        start = sig_ts - before * 60
        end = sig_ts + after * 60
        window = [c for c in candles if start <= c["ts"] <= end]
        have_after = [c for c in candles if c["ts"] >= sig_ts]
        if window:
            coverage = "full" if len(have_after) >= min(after, 60) else "partial"
            note = None if coverage == "full" else (
                "Partial coverage - replay shows every stored candle around the "
                "signal; more history was not recorded at signal time.")

    return {
        "signal": {
            "id": doc.get("id"), "signal_id": doc.get("signal_id"),
            "market": market, "timeframe": doc.get("timeframe"),
            "strategy_id": doc.get("strategy_id"),
            "strategy_name": doc.get("strategy_name"),
            "direction": doc.get("direction"),
            "entry": doc.get("entry"), "sl": doc.get("sl"),
            "tp1": doc.get("tp1"), "tp2": doc.get("tp2"), "tp3": doc.get("tp3"),
            "candle_time": doc.get("candle_time"),
            "status": doc.get("status"), "outcome": doc.get("outcome"),
            "r_multiple": doc.get("r_multiple"),
            "tp_hits": doc.get("tp_hits"),
        },
        "markers": {
            "entry": doc.get("entry"), "sl": doc.get("sl"),
            "tp1": doc.get("tp1"), "tp2": doc.get("tp2"), "tp3": doc.get("tp3"),
        },
        "candles": window,
        "coverage": coverage,
        "note": note,
        "dna": doc.get("dna"),
        "forensics": doc.get("forensics"),
        "demo": State.provider.is_demo,
    }


@router.get("/market-regimes")
def market_regimes(market: str = Query("XAUUSD"), days: int = Query(7, ge=1, le=30),
                   user_id: str = Depends(get_user_id)):
    return {
        "market": market.upper(),
        "current": regime.current(market.upper()),
        "history": regime.history(market.upper(), days),
        "informational_only": True,
    }


@router.get("/learning/observations")
def learning_observations(user_id: str = Depends(get_user_id)):
    """AI OBSERVATIONS feed for the Learning Lab - informational only.

    Every observation is labeled FACT / POSSIBLE_EXPLANATION / UNTESTED
    HYPOTHESIS and carries its sample size. Nothing is claimed beyond data.
    """
    signals = State.store.list("signals", filters={"userId": user_id}, limit=500)
    completed = [s for s in signals if s.get("completed")]
    observations: List[Dict[str, Any]] = []

    # Regime performance (FACT once sample >= 5, otherwise reported as untested)
    by_regime: Dict[str, List[Dict[str, Any]]] = {}
    for s in completed:
        r = (s.get("dna") or {}).get("regime") or \
            (s.get("market_conditions") or {}).get("regime")
        if r:
            by_regime.setdefault(r, []).append(s)
    for r, sigs in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        wins = [s for s in sigs if s.get("outcome") == "WIN"]
        rs = [s.get("r_multiple") for s in sigs if isinstance(s.get("r_multiple"), (int, float))]
        entry: Dict[str, Any] = {
            "kind": "FACT" if len(sigs) >= 5 else "UNTESTED HYPOTHESIS",
            "regime": r, "sample_size": len(sigs),
            "wins": len(wins), "losses": len(sigs) - len(wins),
        }
        if rs:
            rs_sorted = sorted(rs)
            mid = len(rs_sorted) // 2
            entry["median_r"] = round(rs_sorted[mid], 2)
        if len(sigs) >= 5:
            entry["text"] = (f"In {r} regime this account has {len(wins)}/{len(sigs)} "
                             f"winning completed signals"
                             + (f", median {entry['median_r']}R." if rs else "."))
        else:
            entry["text"] = (f"Only {len(sigs)} completed signal(s) in {r} regime so "
                             "far - not enough data for any conclusion.")
        observations.append(entry)

    # Forensic patterns across completed signals (POSSIBLE_EXPLANATION with counts)
    flags: Dict[str, List[Dict[str, Any]]] = {}
    for s in completed:
        for f in (s.get("forensics") or {}).get("findings") or []:
            if f.get("kind") == "POSSIBLE_EXPLANATION":
                flags.setdefault(f.get("label", "?"), []).append(s)
    for label, sigs in flags.items():
        if len(sigs) < 3:
            continue
        losses = [s for s in sigs if s.get("outcome") == "LOSS"]
        if losses:
            observations.append({
                "kind": "POSSIBLE_EXPLANATION",
                "text": (f"{len(losses)} of {len(sigs)} completed signals with "
                         f"\"{label}\" were losses. This pattern co-occurs with "
                         "losses in this account's history."),
                "sample_size": len(sigs),
            })

    if not observations:
        observations.append({
            "kind": "FACT",
            "text": "No completed signals with DNA/forensics data yet - the engine "
                    "records observations as signals complete.",
            "sample_size": 0,
        })
    return {"observations": observations, "informational_only": True,
            "completed_analyzed": len(completed)}


def _parse_ts(value: Any) -> Optional[int]:
    """Signal timestamps are stored as ISO strings or epoch seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 10**12 else value)  # ms -> s
    try:
        return int(pd_ts(value).timestamp())
    except Exception:
        return None


def pd_ts(value: Any):
    import pandas as pd
    return pd.Timestamp(value)
