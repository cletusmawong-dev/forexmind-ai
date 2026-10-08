"""Strategy 2 v2 (Supply & Demand + FVG) - deterministic setup machine.

Pipeline (owner brief 2026-10-07): IMPULSE -> ZONE IDENTIFICATION -> FVG ->
RETEST -> ENTRY.  Completed candles only; one evaluation per closed bar; no
repaint, no lookahead (the machine only ever reads bars <= i).

States (persisted per market|tf in strategy2_sd_fvg_state):
    NO_SETUP -> DISPLACEMENT_CONFIRMED -> WAITING_RETEST -> FVG_RETEST
             -> SIGNAL -> RESET   (invalidation/timeout -> NO_SETUP)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ...core.indicators import atr

_MIN_BARS = 40            # matches SupplyDemandFvgStrategy.min_bars


# ----------------------------------------------------------------------------
# deterministic helpers (each pure: reads df only, bars <= i)
# ----------------------------------------------------------------------------
def displacement_at(df: pd.DataFrame, i: int, atr_series: pd.Series,
                    body_ratio: float, atr_mult: float) -> Optional[str]:
    """A meaningful impulse candle on bar i: body dominates the range AND the
    candle travels >= atr_mult * ATR. Returns 'bull' | 'bear' | None."""
    o = float(df["open"].iloc[i]); c = float(df["close"].iloc[i])
    h = float(df["high"].iloc[i]); l = float(df["low"].iloc[i])
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    if body < body_ratio * rng:                     # body strength
        return None
    a = float(atr_series.iloc[i]) if i < len(atr_series) else 0.0
    if not a or a <= 0 or body < atr_mult * a:      # meaningful movement
        return None
    if c > o:
        return "bull"
    if c < o:
        return "bear"
    return None


def zone_before(df: pd.DataFrame, d: int, direction: str,
                lookback: int) -> Optional[Dict[str, Any]]:
    """The base the impulse exploded from: the LAST opposite-direction candle
    inside the `lookback` bars before the displacement.  The zone is the body
    of that candle (explainable, no arbitrary padding)."""
    start = max(0, d - max(1, lookback))
    for j in range(d - 1, start - 1, -1):
        o = float(df["open"].iloc[j]); c = float(df["close"].iloc[j])
        if direction == "bull" and c < o:           # demand: final bearish base
            return {"index": j, "type": "demand",
                    "zone_high": max(o, c), "zone_low": min(o, c)}
        if direction == "bear" and c > o:           # supply: final bullish base
            return {"index": j, "type": "supply",
                    "zone_high": max(o, c), "zone_low": min(o, c)}
    return None


def fvg_centered(df: pd.DataFrame, d: int, direction: str,
                 min_gap: float = 0.0) -> Optional[Dict[str, Any]]:
    """Strict 3-candle Fair Value Gap around displacement candle d.

    bullish FVG: high[d-1] < low[d+1]   bearish FVG: low[d-1] > high[d+1]
    The gap itself is the zone. min_gap is an ABSOLUTE price floor (pass
    fvg_min_atr * ATR; 0 = accept every valid FVG - never over-filter).
    """
    if d < 1 or d + 1 >= len(df):
        return None
    c1_high = float(df["high"].iloc[d - 1]); c1_low = float(df["low"].iloc[d - 1])
    c3_low = float(df["low"].iloc[d + 1]); c3_high = float(df["high"].iloc[d + 1])
    if direction == "bull":
        lo, hi = c1_high, c3_low
        if not lo < hi:                             # no imbalance
            return None
        kind = "bullish_fvg"
    else:
        lo, hi = c3_high, c1_low
        if not hi > lo:
            return None
        kind = "bearish_fvg"
    if (hi - lo) < min_gap:
        return None
    return {"type": kind, "fvg_low": lo, "fvg_high": hi,
            "fvg_mid": (lo + hi) / 2.0,
            "created_index": d + 1, "displacement_index": d,
            "displacement_time": str(df.index[d]),
            "created_time": str(df.index[d + 1])}


def fvg_entry_level(fvg: Dict[str, Any], method: str,
                    deep_frac: float = 0.25) -> float:
    """Configurable entry refinement (researchable).

    touch   -> the near edge of the gap (first tap)
    midpoint-> the 50% level (default)
    deep    -> a fraction deeper into the gap from the near edge
    """
    lo, hi, mid = fvg["fvg_low"], fvg["fvg_high"], fvg["fvg_mid"]
    if method == "touch":
        return hi                                   # near edge for both sides
    if method == "deep":
        if deep_frac >= 0.5:
            return mid
        return hi - deep_frac * (hi - lo)           # fraction below the near edge
    return mid


def consumed_fully(fvg: Dict[str, Any], df: pd.DataFrame, direction: str,
                   from_index: int) -> bool:
    """An FVG fully traded through is dead - it must never re-fire."""
    for j in range(from_index, len(df)):
        if direction == "bull" and float(df["close"].iloc[j]) < fvg["fvg_low"]:
            return True
        if direction == "bear" and float(df["close"].iloc[j]) > fvg["fvg_high"]:
            return True
    return False


def zone_invalidated(df: pd.DataFrame, zone: Dict[str, Any], direction: str,
                     i: int, atr_value: float, buffer_mult: float) -> bool:
    """Decisive close through the far side of the zone kills the setup."""
    buffer = buffer_mult * max(atr_value, 1e-9)
    c = float(df["close"].iloc[i])
    if direction == "bull":
        return c < zone["zone_low"] - buffer
    return c > zone["zone_high"] + buffer


def fvg_key(market: str, timeframe: str, fvg: Dict[str, Any]) -> str:
    return (f"{market}|{timeframe}|{round(fvg['fvg_low'], 7)}|"
            f"{round(fvg['fvg_high'], 7)}|{fvg['displacement_time']}")


def default_state() -> Dict[str, Any]:
    return {
        "state": "NO_SETUP",
        "direction": None,             # 'bull' | 'bear'
        "zone": None,                  # {type, zone_high, zone_low, index}
        "fvg": None,                   # {fvg_low, fvg_high, fvg_mid, ...}
        "setup_index": None,           # bar where the setup was armed
        "consumed": [],                # last N fvg_keys (duplicate guard)
        "last_bar_ts": None,           # one evaluation per closed bar
        "last_signal_key": None,
    }


# ----------------------------------------------------------------------------
# the machine
# ----------------------------------------------------------------------------
def evaluate(df: pd.DataFrame, market: str, timeframe: str,
             st: Dict[str, Any], params: Dict[str, Any],
             session: str = "") -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Advance the persisted state machine over the NOT-YET-PROCESSED closed
    bars. Returns (candidate, new_state, events). Pure: reads df only."""
    n = len(df)
    events: List[str] = []
    atr_series = atr(df, int(params.get("atr_length", 14)))
    body_ratio = float(params.get("displacement_body_ratio", 0.60))
    atr_mult = float(params.get("displacement_atr_mult", 1.1))
    lookback = int(params.get("zone_base_lookback", 3))
    fvg_min = float(params.get("fvg_min_atr", 0.0))
    method = str(params.get("fvg_entry_method", "midpoint"))
    deep_frac = float(params.get("fvg_deep_fraction", 0.25))
    max_age = int(params.get("zone_max_age_bars", 96))
    sl_buf = float(params.get("sl_buffer_atr", 0.25))
    inv_buf = float(params.get("invalidation_buffer_atr", 0.30))
    tp1r = float(params.get("tp1_r", 1.0))
    tp2r = float(params.get("tp2_r", 2.0))
    tp3r = float(params.get("tp3_r", 3.0))

    candidate: Optional[Dict[str, Any]] = None
    last_ts = st.get("last_bar_ts")
    if last_ts is not None:
        start = n - 1
        try:
            prev = df.index.get_indexer([pd.Timestamp(float(last_ts), unit="s", tz="UTC")])
            if prev and prev[0] >= 0:
                start = max(start, prev[0] + 1)
        except Exception:
            start = n - 1
    else:
        # cold start (fresh deploy/restart): replay the recent window so an
        # ALREADY-ARMED setup is not lost - bounded by zone freshness
        start = max(1, n - 1 - (max_age + 10))
    if start >= n:
        return None, st, events

    for i in range(max(1, start), n):
        st["last_bar_ts"] = int(df.index[i].timestamp())
        a = float(atr_series.iloc[i]) if i < len(atr_series) else None
        if not a or a <= 0 or i < _MIN_BARS // 2:
            continue

        # ---- active setup management --------------------------------
        if st["state"] == "WAITING_RETEST" and st.get("zone") and st.get("fvg"):
            direction = st["direction"]
            age = i - int(st["setup_index"] or i)
            if age > max_age:
                events.append(f"TIMEOUT: setup expired after {age} bars -> NO_SETUP")
                st.update(default_state(), consumed=st.get("consumed", []),
                          last_bar_ts=st["last_bar_ts"])
                continue
            if zone_invalidated(df, st["zone"], direction, i, a, inv_buf):
                events.append("INVALIDATED: zone structure decisively broken -> NO_SETUP")
                st.update(default_state(), consumed=st.get("consumed", []),
                          last_bar_ts=st["last_bar_ts"])
                continue
            fvg = st["fvg"]
            zone = st["zone"]
            level = fvg_entry_level(fvg, method, deep_frac)
            touched = (float(df["low"].iloc[i]) <= level
                       if direction == "bull"
                       else float(df["high"].iloc[i]) >= level)
            if not touched:
                continue
            key = fvg_key(market, timeframe, fvg)
            if key in (st.get("consumed") or []):
                st["state"] = "NO_SETUP"
                continue
            st["state"] = "FVG_RETEST"
            entry = level
            if direction == "bull":
                sl = zone["zone_low"] - sl_buf * a
                risk = entry - sl
                if risk <= 0:
                    st["state"] = "NO_SETUP"
                    continue
                tps = [entry + tp1r * risk, entry + tp2r * risk, entry + tp3r * risk]
            else:
                sl = zone["zone_high"] + sl_buf * a
                risk = sl - entry
                if risk <= 0:
                    st["state"] = "NO_SETUP"
                    continue
                tps = [entry - tp1r * risk, entry - tp2r * risk, entry - tp3r * risk]
            disp_idx = int(fvg["displacement_index"])
            disp_range = abs(float(df["close"].iloc[disp_idx])
                             - float(df["open"].iloc[disp_idx]))
            score = int(max(40, min(100, 40 + min(20.0, 20.0 * (disp_range / a) / 2.0)
                    + (20 if age <= max_age // 3 else 10)
                    + (20 if method != "touch" else 12))))
            candidate = {
                "direction": "BUY" if direction == "bull" else "SELL",
                "entry": entry, "sl": sl, "tps": tps, "risk": risk,
                "state": "SIGNAL", "score": score,
                "zone": zone, "fvg": fvg,
                "session": session, "atr": a,
                "retest_time": str(df.index[i]),
                "signal_key": key,
            }
            st["state"] = "RESET"
            st["last_signal_key"] = key
            consumed = list(st.get("consumed") or [])
            consumed.append(key)
            st["consumed"] = consumed[-20:]
            st["setup_index"] = i + 1
            events.append(f"SIGNAL: {candidate['direction']} {market} {timeframe} "
                          f"at FVG {fvg['fvg_low']:,.5g}-{fvg['fvg_high']:,.5g} -> RESET")
            continue

        # ---- arming: only when no live setup (fresh setup after a signal) ----
        if st["state"] in ("WAITING_RETEST", "FVG_RETEST"):
            continue
        disp = displacement_at(df, i, atr_series, body_ratio, atr_mult)
        if not disp:
            continue
        zone = zone_before(df, i, disp, lookback)
        if not zone:
            continue
        if i + 1 >= n:
            # displacement is the newest closed bar: no candle d+1 yet -> the
            # FVG can only be confirmed on the NEXT bar (never lookahead)
            st["state"] = "DISPLACEMENT_CONFIRMED"
            st["direction"] = disp
            st["zone"] = zone
            st["setup_index"] = i
            st["fvg"] = None
            events.append(f"DISPLACEMENT_CONFIRMED: {disp} displacement at "
                          f"{df.index[i]} ({zone['type']} zone "
                          f"{zone['zone_low']:,.5g}-{zone['zone_high']:,.5g})")
            continue
        fvg = fvg_centered(df, i, disp, fvg_min * a)
        if fvg is None:
            st["state"] = "DISPLACEMENT_CONFIRMED"
            st["direction"] = disp
            st["zone"] = zone
            st["setup_index"] = i
            st["fvg"] = None
            events.append(f"DISPLACEMENT_CONFIRMED: {disp} displacement at {df.index[i]}")
            continue
        # FVG must sit in the displacement direction relative to the zone
        aligned = (fvg["fvg_low"] <= zone["zone_high"] + 0.25 * a
                   if disp == "bull"
                   else fvg["fvg_high"] >= zone["zone_low"] - 0.25 * a)
        if not aligned:
            st["state"] = "DISPLACEMENT_CONFIRMED"
            st["direction"] = disp
            st["zone"] = zone
            st["setup_index"] = i
            st["fvg"] = None
            continue
        key = fvg_key(market, timeframe, fvg)
        if key in (st.get("consumed") or []):
            continue
        st["state"] = "WAITING_RETEST"
        st["direction"] = disp
        st["zone"] = zone
        st["fvg"] = fvg
        st["setup_index"] = i + 1
        events.append(f"FVG_IDENTIFIED: {'bullish' if disp == 'bull' else 'bearish'} FVG "
                      f"{fvg['fvg_low']:,.5g}-{fvg['fvg_high']:,.5g} -> WAITING_RETEST")

    return candidate, st, events


def render_state(st: Dict[str, Any]) -> Dict[str, Any]:
    """/charts/setup view: spec state names + drawable levels (read-only)."""
    state = st.get("state", "NO_SETUP")
    direction = st.get("direction")
    zone = st.get("zone") or {}
    fvg = st.get("fvg") or {}
    levels = {
        "zone_type": zone.get("type"),
        "zone_high": zone.get("zone_high"),
        "zone_low": zone.get("zone_low"),
        "fvg_high": fvg.get("fvg_high"),
        "fvg_low": fvg.get("fvg_low"),
        "fvg_mid": fvg.get("fvg_mid"),
        "displacement_time": fvg.get("displacement_time") or zone.get("index"),
    }
    return {"state": state,
            "direction": ("BUY" if direction == "bull" else
                          "SELL" if direction == "bear" else None),
            "levels": levels,
            "note": "persisted Supply & Demand + FVG machine state (read-only view)"}
