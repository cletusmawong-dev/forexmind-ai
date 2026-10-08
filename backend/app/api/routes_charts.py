"""Professional chart API (master upgrade §4) - real market data only.

Everything here is READ-ONLY and derived from the same provider the engine
scans with, so the chart can never show data the strategies did not see:

  * /charts/overlays   - server-computed EMA9/EMA21 aligned to candle times
                         (causal series: value at bar i uses bars <= i only -
                         no lookahead, enforced by test). For the timeframes
                         Strategy 2 trades, the response also carries the
                         S/D+FVG overlay (armed setup + recent zones).
  * /charts/setup      - Strategy 2's PERSISTED machine state for a market
                         (state.py) rendered as the spec state names, plus the
                         levels needed to draw the zone/FVG on the chart.
                         Reading never advances the machine.
  * /charts/annotations- the caller's OWN recent signals for a symbol
                         (entry/SL/TP lines + historical markers). Tenant
                         isolation: userId comes from the token, never input.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.indicators import ema
from ..db.store import get_store
from ..state import State
from ..strategies.strategy_2_supply_demand_fvg import state as s2_state
from ..strategies.strategy_2_supply_demand_fvg import machine as s2_machine
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
    chart draws are exactly the values the strategies compute. When the
    timeframe is one Strategy 2 trades, the response also carries the S/D+FVG
    overlay: the LIVE armed setup (entry/SL/TP the machine will use) plus the
    recent historical zones/FVGs on the visible window (read-only)."""
    if tf.upper() not in CHART_TFS:
        raise HTTPException(400, f"unsupported timeframe {tf!r} - choose from {sorted(CHART_TFS)}")
    df = _candles(symbol, tf, limit)
    e9 = ema(df["close"], 9)
    e21 = ema(df["close"], 21)
    return {"symbol": symbol.upper(), "timeframe": tf.upper(),
            "ema9": _series_points(df, e9), "ema21": _series_points(df, e21),
            "s2": _s2_overlays(symbol.upper(), tf.upper(), df),
            "demo": State.provider.is_demo}


def _s2_overlays(symbol: str, tf: str, df: pd.DataFrame) -> dict:
    """Supply & Demand + FVG overlay for the chart (read-only).

    - live: the PERSISTED machine setup, with the exact entry/SL/TP levels the
      machine derives at signal time (same math, same active params).
    - zones: deterministic recent S/D + FVG boxes on the visible window, from
      the same helpers the strategy uses (no repaint: only closed bars).
    """
    out = {"enabled": False, "live": None, "zones": []}
    if tf not in {"15M", "30M", "1H"}:
        return out
    from ..learning.versions import active_params
    params = active_params("strategy_2_supply_demand_fvg")

    a = s2_machine.atr(df, int(params.get("atr_length", 14)))
    if a is None or len(a) == 0 or pd.isna(a.iloc[-1]):
        return out

    # ---- live armed setup (exact levels the machine will act on) ----------
    st = s2_state.load(symbol, tf)
    if st.get("state") in ("WAITING_RETEST", "FVG_RETEST") and st.get("zone") and st.get("fvg"):
        zone, fvg = st["zone"], st["fvg"]
        entry = s2_machine.fvg_entry_level(
            fvg, str(params.get("fvg_entry_method", "midpoint")),
            float(params.get("fvg_deep_fraction", 0.25)))
        if entry is not None:
            a_now = float(a.iloc[-1])
            tp_r = (float(params.get("tp1_r", 1.0)), float(params.get("tp2_r", 2.0)),
                    float(params.get("tp3_r", 3.0)))
            if st["direction"] == "bull":
                sl = zone["zone_low"] - float(params.get("sl_buffer_atr", 0.25)) * a_now
                risk = entry - sl
                tps = [entry + r * risk for r in tp_r] if risk > 0 else None
            else:
                sl = zone["zone_high"] + float(params.get("sl_buffer_atr", 0.25)) * a_now
                risk = sl - entry
                tps = [entry - r * risk for r in tp_r] if risk > 0 else None
            if tps:
                out["live"] = {
                    "state": st.get("state"), "direction": "BUY" if st["direction"] == "bull" else "SELL",
                    "entry": round(entry, 7), "sl": round(sl, 7),
                    "tps": [round(t, 7) for t in tps],
                    "zone_type": zone.get("type"), "zone_high": zone.get("zone_high"),
                    "zone_low": zone.get("zone_low"),
                    "fvg_high": fvg.get("fvg_high"), "fvg_low": fvg.get("fvg_low"),
                    "fvg_mid": fvg.get("fvg_mid"), "entry_method": params.get("fvg_entry_method"),
                    "armed_at": st.get("fvg", {}).get("created_time"),
                }

    # ---- recent historical zones + FVGs on the visible window -------------
    body_ratio = float(params.get("displacement_body_ratio", 0.60))
    atr_mult = float(params.get("displacement_atr_mult", 1.1))
    lookback = int(params.get("zone_base_lookback", 3))
    fvg_min = float(params.get("fvg_min_atr", 0.0))
    n = len(df)
    found = []
    for i in range(max(1, n - 300), n - 1):
        try:
            disp = s2_machine.displacement_at(df, i, a, body_ratio, atr_mult)
            if not disp:
                continue
            zone = s2_machine.zone_before(df, i, disp, lookback)
            fvg = s2_machine.fvg_centered(df, i, disp, fvg_min * float(a.iloc[i]))
            if not zone or fvg is None:
                continue
            ai = float(a.iloc[i])
            aligned = (fvg["fvg_low"] <= zone["zone_high"] + 0.25 * ai
                       if disp == "bull"
                       else fvg["fvg_high"] >= zone["zone_low"] - 0.25 * ai)
            if not aligned:
                continue
            # boxes stay open until price fully trades through, else last bar
            fvg_end = df.index[n - 1]
            for j in range(i + 1, n):
                cj = float(df["close"].iloc[j])
                if (disp == "bull" and cj < fvg["fvg_low"]) or \
                        (disp == "bear" and cj > fvg["fvg_high"]):
                    fvg_end = df.index[j]
                    break
            zone_end = df.index[n - 1]
            for j in range(i + 1, n):
                if s2_machine.zone_invalidated(df, zone, disp, j,
                                               float(a.iloc[j]),
                                               float(params.get("invalidation_buffer_atr", 0.30))):
                    zone_end = df.index[j]
                    break
            found.append({
                "kind": "setup", "type": zone["type"],
                "direction": "BUY" if disp == "bull" else "SELL",
                "zone_high": zone["zone_high"], "zone_low": zone["zone_low"],
                "zone_from": str(df.index[max(0, int(zone["index"]))]),
                "zone_to": str(zone_end), "zone_active": zone_end == df.index[n - 1],
                "fvg_type": fvg["type"], "fvg_high": fvg["fvg_high"],
                "fvg_low": fvg["fvg_low"], "fvg_mid": fvg["fvg_mid"],
                "fvg_from": str(df.index[i]), "fvg_to": str(fvg_end),
                "fvg_active": fvg_end == df.index[n - 1],
            })
        except Exception:
            continue
    out["zones"] = found[-12:]
    out["enabled"] = True
    return out


def _render_s2_state(market: str, entry_tf: str) -> dict:
    """Map the persisted Supply & Demand + FVG machine doc to the spec state
    names (read-only). Never advances the machine."""
    st = s2_state.load(market, entry_tf)
    out = s2_machine.render_state(st)
    out["market"] = market.upper()
    out["entry_tf"] = entry_tf
    return out


@router.get("/charts/setup")
def setup(symbol: str, tf: str = Query("15M"),
          user_id: str = Depends(get_user_id)):
    """Scanner sub-state for Strategy 2 on this symbol (master upgrade §9)."""
    if tf.upper() not in {"15M", "30M", "1H"}:
        raise HTTPException(400, "Strategy 2 evaluates 15M, 30M and 1H only")
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
