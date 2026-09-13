"""Market overview routes (SPEC §6 Market Overview)."""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from ..config import INITIAL_MARKETS
from ..learning.versions import active_params
from ..state import State
from ..strategies import get_strategy

router = APIRouter(prefix="/markets", tags=["markets"])


def _market_card(symbol: str) -> dict:
    provider = State.provider
    price = provider.latest_price(symbol)
    card = {"symbol": symbol, "price": price, "demo": provider.is_demo,
            "data_status": provider.data_health(symbol)["status"]}
    df = provider.get_candles(symbol, "15M", limit=1200)
    if df is None or len(df) < 300 or price is None:
        card.update({"bias": "N/A", "timeframe": "15M", "status": "NO DATA"})
        return card
    s1 = get_strategy("strategy_1_zero_lag")
    state = s1.compute(df, active_params("strategy_1_zero_lag"))
    trend = int(state["trend"].iloc[-1])
    zl = float(state["zlema"].iloc[-1]); vol = float(state["vol"].iloc[-1])
    bias = {1: "BUY BIAS", -1: "SELL BIAS", 0: "NEUTRAL"}[trend]
    px = float(df["close"].iloc[-1])
    dist = abs(px - zl) / vol if vol and vol > 0 else 9
    if dist < 0.35 and trend != 0:
        status = "SETUP FORMING"
    elif trend == 0:
        status = "NO SETUP"
    else:
        status = "WATCHING"
    # has the last closed bar produced an entry?
    if s1.detect_on_bar(state, len(df) - 1):
        status = "SIGNAL READY"
    else:
        s2 = get_strategy("strategy_2_ema_atr")
        st2 = s2.compute(df, active_params("strategy_2_ema_atr"))
        if s2.detect_on_bar(st2, len(df) - 1):
            status = "SIGNAL READY"
    mtf = s1.mtf_trend(provider.higher_frames(symbol, "15M"),
                       active_params("strategy_1_zero_lag"))
    card.update({
        "bias": bias, "timeframe": "15M", "status": status, "mtf": mtf,
        "change_pct": round((px / float(df["close"].iloc[-8]) - 1) * 100, 2) if len(df) > 8 else 0,
    })
    return card


@router.get("")
def list_markets():
    return {"markets": [_market_card(m) for m in INITIAL_MARKETS],
            "demo": State.provider.is_demo}


@router.get("/{symbol}/candles")
def candles(symbol: str, tf: str = Query("15M"), limit: int = Query(160)):
    df = State.provider.get_candles(symbol.upper(), tf.upper(), limit=limit)
    if df is None:
        return {"candles": [], "demo": True, "message": "Market data unavailable."}
    rows = [{"time": str(ts), "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for ts, r in df.tail(limit).iterrows()]
    return {"symbol": symbol.upper(), "timeframe": tf.upper(), "candles": rows,
            "demo": State.provider.is_demo}
