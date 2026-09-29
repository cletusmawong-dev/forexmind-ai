"""MARKET WORLD MODEL - compact FINGERPRINT (3.0 spec sections 10-11).

Builds the structured per-instrument snapshot from the EXISTING world model
(no new data paths, no fabrication): trend, structure, volatility, momentum,
spread, session, news risk, regime, timeframe alignment. Every consumer that
records a trade/evidence can embed this fingerprint. Missing inputs are None,
never guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _trend_of(ema_fast: Optional[float], ema_slow: Optional[float],
              close: Optional[float]) -> Optional[str]:
    if None in (ema_fast, ema_slow, close):
        return None
    if ema_fast > ema_slow and close > ema_fast:
        return "BULLISH"
    if ema_fast < ema_slow and close < ema_fast:
        return "BEARISH"
    return "MIXED"


def _band(v: Optional[float], lo: float, mid: float) -> Optional[str]:
    if v is None:
        return None
    return "LOW" if v < lo else ("MEDIUM" if v < mid else "HIGH")


def build_fingerprint(world: dict, now: Optional[datetime] = None) -> dict:
    """Deterministic projection of a world model into a fingerprint."""
    tfs = world.get("timeframes") or {}
    m15 = (tfs.get("15M") or {})
    h4 = (tfs.get("H4") or {})
    d1 = (tfs.get("D1") or {})
    px = _num(m15.get("close"))

    session = None
    try:
        hour = (now or datetime.now(timezone.utc)).hour
        session = "Asian" if hour < 8 else "London" if hour < 13 else \
            "NewYork" if hour < 21 else "Late"
    except Exception:
        pass

    atr = _num(m15.get("atr14")) or _num(h4.get("atr14"))
    spread = _num((world.get("spread") or {}).get("value")
                  if isinstance(world.get("spread"), dict) else world.get("spread"))
    regime = (world.get("regime") or {}).get("label") if \
        isinstance(world.get("regime"), dict) else world.get("regime")

    align = [ _trend_of(_num(tf.get("ema9")), _num(tf.get("ema21")), _num(tf.get("close")))
              for tf in (m15, h4, d1) ]
    known = [a for a in align if a]
    alignment = None if not known else ("ALIGNED" if len(set(known)) == 1 else "MIXED")

    news = world.get("news") or {}
    news_risk = "HIGH" if news.get("blackout") else \
        ("MEDIUM" if news.get("next_high_impact_min") is not None
         and news["next_high_impact_min"] < 180 else "LOW")

    return {
        "market": world.get("market"),
        "trend": _trend_of(_num(m15.get("ema9")), _num(m15.get("ema21")), px),
        "structure_m15": m15.get("structure"),
        "structure_h4": h4.get("structure"),
        "structure_d1": d1.get("structure"),
        "volatility": _band(atr, None, None) if atr is None else
        ("LOW" if atr == 0 else "NORMAL"),
        "atr14": atr,
        "momentum": m15.get("momentum") or None,
        "spread": spread,
        "spread_state": (world.get("spread") or {}).get("state")
        if isinstance(world.get("spread"), dict) else None,
        "session": world.get("session") or session,
        "news_risk": news_risk,
        "regime": regime,
        "timeframe_alignment": alignment,
        "data_freshness": (world.get("freshness") or {}).get("verdict"),
        "built_at": (now or datetime.now(timezone.utc)).isoformat(),
        "note": "snapshot of what the system knew at build time - not a prediction",
    }
