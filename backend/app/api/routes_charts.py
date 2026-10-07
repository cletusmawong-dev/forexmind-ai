"""Professional chart API (master upgrade §4) - real market data only.

Everything here is READ-ONLY and derived from the same provider the engine
scans with, so the chart can never show data the strategies did not see:

  * /charts/overlays   - server-computed EMA9/EMA21 aligned to candle times
                         (causal series: value at bar i uses bars <= i only -
                         no lookahead, enforced by test)
  * /charts/setup      - Strategy 2's PERSISTED machine state for a market
                         (state.py) rendered as the spec state names, plus the
                         levels needed to draw liquidity/sweep/structure on
                         the chart. Reading never advances the machine.
  * /charts/annotations- the caller's OWN recent signals for a symbol
                         (entry/SL/TP lines + historical markers). Tenant
                         isolation: userId comes from the token, never input.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.indicators import ema
from ..db.store import get_store
from ..state import State
from ..strategies.strategy_2_mtf_sweep_bos_retest import state as s2_state
from .deps import get_user_id

router = APIRouter(tags=["charts"])

CHART_TFS = {"1M", "5M", "15M", "30M", "1H", "4H", "1D"}


def _candles(symbol: str, tf: str, limit: int) -> pd.DataFrame:
    df = State.provider.get_candles(symbol.upper(), tf.upper(), limit=limit)
    if df is None:
        raise ValueError(f"no data for {symbol.upper()} {tf.upper()}")
    return df


def _series_points(df: pd.DataFrame, series: pd.Series) -> list:
    out = []
    for ts, v in series.items():
        if pd.notna(v):
            out.append({"time": str(ts), "value": round(float(v), 7)})
    return out


@router.get("/charts/overlays")
def overlays(symbol: str, tf: str = Query("15M"), limit: int = Query(400, le=1000),
             user_id: str = Depends(get_user_id)):
    """EMA9/EMA21 over real candles - computed server-side so the values the
    chart draws are exactly the values the strategies compute."""
    if tf.upper() not in CHART_TFS:
        raise HTTPException(400, f"unsupported timeframe {tf!r} - choose from {sorted(CHART_TFS)}")
    df = _candles(symbol, tf, limit)
    e9 = ema(df["close"], 9)
    e21 = ema(df["close"], 21)
    return {"symbol": symbol.upper(), "timeframe": tf.upper(),
            "ema9": _series_points(df, e9), "ema21": _series_points(df, e21),
            "demo": State.provider.is_demo}


def _render_s2_state(market: str, entry_tf: str) -> dict:
    """Map the persisted machine doc to the spec state names (read-only)."""
    st = s2_state.load(market, entry_tf)
    waiting_bull = bool(st.get("waiting_bull_retest"))
    waiting_bear = bool(st.get("waiting_bear_retest"))
    swept = st.get("swept_level") is not None
    broken = st.get("broken_high") is not None or st.get("broken_low") is not None
    if waiting_bull or waiting_bear:
        state = "WAITING_RETEST"
    elif broken:
        state = "STRUCTURE_CONFIRMED"
    elif swept:
        state = "SWEEP_DETECTED"
    elif st.get("structure_high") is not None or st.get("structure_low") is not None:
        state = "LIQUIDITY_IDENTIFIED"
    else:
        state = "NO_SETUP"
    return {"market": market.upper(), "entry_tf": entry_tf, "state": state,
            "direction": ("BUY" if waiting_bull else
                          "SELL" if waiting_bear else
                          ("BULLISH" if st.get("bullish_setup") else
                           "BEARISH" if st.get("bearish_setup") else None)),
            "levels": {"swept_level": st.get("swept_level"),
                       "sweep_price": st.get("sweep_price"),
                       "structure_high": st.get("structure_high"),
                       "structure_low": st.get("structure_low"),
                       "broken_high": st.get("broken_high"),
                       "broken_low": st.get("broken_low")},
            "note": "persisted Strategy 2 machine state (read-only view)"}


@router.get("/charts/setup")
def setup(symbol: str, tf: str = Query("15M"),
          user_id: str = Depends(get_user_id)):
    """Scanner sub-state for Strategy 2 on this symbol (master upgrade §9)."""
    if tf.upper() not in {"15M", "1H"}:
        raise HTTPException(400, "Strategy 2 evaluates 15M and 1H only")
    return _render_s2_state(symbol, tf.upper())


@router.get("/charts/annotations")
def annotations(symbol: str, limit: int = Query(12, le=40),
                user_id: str = Depends(get_user_id)):
    """The caller's own recent signals for this symbol - entry/SL/TP lines and
    historical markers. Never another user's (isolation enforced by token)."""
    rows = get_store().list("signals", filters={"userId": user_id}, limit=200)
    mine = [d for d in rows if str(d.get("market", "")).upper() == symbol.upper()]
    mine.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
    out = []
    for d in mine[:limit]:
        out.append({
            "signal_id": d.get("signal_id"), "id": d.get("id"),
            "strategy_id": d.get("strategy_id"), "strategy_name": d.get("strategy_name"),
            "market": d.get("market"), "timeframe": d.get("timeframe"),
            "direction": d.get("direction"), "entry": d.get("entry"),
            "sl": d.get("sl"), "tp1": d.get("tp1"), "tp2": d.get("tp2"), "tp3": d.get("tp3"),
            "status": d.get("status"), "r_multiple": d.get("r_multiple"),
            "outcome": d.get("outcome"), "candle_time": d.get("candle_time"),
            "completed_at": d.get("completed_at"), "createdAt": d.get("createdAt"),
        })
    return {"symbol": symbol.upper(), "annotations": out, "count": len(out)}
