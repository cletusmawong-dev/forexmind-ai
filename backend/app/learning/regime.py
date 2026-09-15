"""Market Regime Engine - classifies the CURRENT market environment.

Research/informational only: it never creates trades and never disables a
strategy by itself. Everything is computed from candles the system already
has (no invented data).

Regimes: TRENDING_BULLISH, TRENDING_BEARISH, RANGING, HIGH_VOLATILITY,
LOW_VOLATILITY, BREAKOUT, CHOPPY, UNCERTAIN.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from ..core.indicators import atr, ema
from ..db.store import get_store

REGIMES = ("TRENDING_BULLISH", "TRENDING_BEARISH", "RANGING", "HIGH_VOLATILITY",
           "LOW_VOLATILITY", "BREAKOUT", "CHOPPY", "UNCERTAIN")

_lock = threading.Lock()
_last: Dict[str, dict] = {}   # market -> last classification (cache)


def _ema_slope(series: pd.Series) -> float:
    """Relative slope of the last 10 EMA values (% per 10 bars)."""
    if len(series) < 12:
        return 0.0
    a, b = float(series.iloc[-10]), float(series.iloc[-1])
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0


def classify(df: pd.DataFrame) -> Dict[str, Any]:
    """Classify a CLOSED-candle frame. Pure function - no I/O."""
    if df is None or len(df) < 60:
        return {"regime": "UNCERTAIN", "confidence": 0, "detail": "insufficient bars"}
    close = df["close"].astype(float)
    high, low = df["high"].astype(float), df["low"].astype(float)

    ema21 = ema(close, 21)
    ema50 = ema(close, 50)
    atr14 = atr(df, 14)
    a = float(atr14.iloc[-1]) if not pd.isna(atr14.iloc[-1]) else 0.0
    px = float(close.iloc[-1])

    # --- trend components -------------------------------------------------
    slope21 = _ema_slope(ema21)                     # % over 10 bars
    above = 1.0 if px > float(ema21.iloc[-1]) else -1.0
    ema_stack = 1.0 if float(ema21.iloc[-1]) > float(ema50.iloc[-1]) else -1.0
    trend_score = max(-100.0, min(100.0, (slope21 * 25.0 + above * 30.0 + ema_stack * 20.0)))

    # --- volatility components --------------------------------------------
    atr_series = atr14.dropna()
    vol_rank = 0.5
    if len(atr_series) >= 60:
        vol_rank = float((atr_series < a).mean())    # percentile of current ATR
    atr_pct = (a / px * 100.0) if px else 0.0        # ATR as % of price
    if vol_rank >= 0.85:
        vol_label = "HIGH"
    elif vol_rank <= 0.2:
        vol_label = "LOW"
    else:
        vol_label = "NORMAL"

    # --- structure ----------------------------------------------------------
    rng = float((high - low).tail(20).mean())
    body = float((close - df["open"].astype(float)).abs().tail(20).mean())
    body_ratio = (body / rng) if rng > 0 else 0.5    # choppy when bodies are small
    window_hi, window_lo = float(high.tail(20).max()), float(low.tail(20).min())
    pos_in_range = (px - window_lo) / (window_hi - window_lo) if window_hi > window_lo else 0.5
    near_break = pos_in_range > 0.97 or pos_in_range < 0.03

    # --- decide -------------------------------------------------------------
    if abs(trend_score) >= 55:
        regime = "TRENDING_BULLISH" if trend_score > 0 else "TRENDING_BEARISH"
        confidence = min(95, 55 + abs(trend_score) * 0.4)
    elif near_break and vol_rank >= 0.7:
        regime = "BREAKOUT"
        confidence = 70
    elif vol_label == "HIGH":
        regime = "HIGH_VOLATILITY"
        confidence = 65
    elif vol_label == "LOW":
        regime = "LOW_VOLATILITY"
        confidence = 65
    elif body_ratio < 0.45 and abs(slope21) < 0.15:
        regime = "CHOPPY"
        confidence = 60
    else:
        regime = "RANGING"
        confidence = 60

    momentum = "POSITIVE" if close.iloc[-1] > close.iloc[-6] else (
        "NEGATIVE" if close.iloc[-1] < close.iloc[-6] else "FLAT")
    if regime in ("HIGH_VOLATILITY", "LOW_VOLATILITY", "CHOPPY", "RANGING"):
        confidence = min(confidence + 5, 92)

    return {
        "regime": regime,
        "trend_score": round(trend_score, 1),        # -100..100
        "volatility": vol_label,
        "atr_pct": round(atr_pct, 3),
        "volatility_rank": round(vol_rank, 2),
        "momentum": momentum,
        "body_ratio": round(body_ratio, 2),
        "confidence": round(confidence),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# history (Firestore): one doc per market+day, flat "ts|regime|strength" rows
# ---------------------------------------------------------------------------
def record(market: str, df: pd.DataFrame) -> Optional[dict]:
    """Store one classification per closed 15M candle (called from the loop)."""
    if df is None or len(df) < 60:
        return None
    m = market.upper()
    result = classify(df)
    with _lock:
        _last[m] = result
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    try:
        store = get_store()
        doc_id = f"{m}:{day}"
        doc = store.get("market_regimes", doc_id)
        row = f"{int(now.timestamp())}|{result['regime']}|{result['trend_score']}|{result['confidence']}"
        if doc:
            rows = list(doc.get("rows") or [])
            if rows and rows[-1].split("|")[1] == result["regime"] and \
                    int(rows[-1].split("|")[0]) > int(now.timestamp()) - 1500:
                return result   # same regime within the last candle - skip
            rows.append(row)
            store.update("market_regimes", doc_id,
                         {"rows": rows[-400:], "updatedAt": now.isoformat()})
        else:
            store.create("market_regimes",
                         {"market": m, "day": day, "rows": [row],
                          "createdAt": now.isoformat()}, doc_id=doc_id)
    except Exception:
        pass   # history is best-effort; classification itself already cached
    return result


def current(market: str, df: Optional[pd.DataFrame] = None) -> dict:
    m = market.upper()
    with _lock:
        cached = _last.get(m)
    if cached:
        return cached
    if df is not None:
        return classify(df)
    return {"regime": "UNCERTAIN", "confidence": 0, "detail": "not yet computed"}


def history(market: str, days: int = 3) -> List[dict]:
    """Recent regime history (oldest first)."""
    store = get_store()
    out: List[dict] = []
    for i in range(max(1, days)):
        day = datetime.now(timezone.utc).fromtimestamp(
            datetime.now(timezone.utc).timestamp() - i * 86400).strftime("%Y-%m-%d")
        doc = store.get("market_regimes", f"{market.upper()}:{day}")
        if not doc:
            continue
        for row in doc.get("rows") or []:
            try:
                ts, regime, score, conf = row.split("|")
                out.append({"ts": int(ts), "regime": regime,
                            "trend_score": float(score), "confidence": float(conf)})
            except Exception:
                continue
    return sorted(out, key=lambda x: x["ts"])
