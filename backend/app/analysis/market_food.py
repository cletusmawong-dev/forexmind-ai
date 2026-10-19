"""MARKET FOOD - 1H/4H liquidity & key-level intelligence (owner brief
"Brain & Intelligent Trade Management Upgrade", 2026-10-10, section 2).

Pure, deterministic, completed-candles-only analysis of what price has left
behind on the higher timeframes. It SUPPORTS trade management; it never
replaces or rewrites a strategy's entry rules and never invents
probabilities (section 5: evidence vs heuristic is explicit).

Everything here is descriptive context:
  * swing highs/lows            (fractal pivots on completed bars)
  * major vs internal levels    (clustered by prominence/touches)
  * buy-side / sell-side liquidity pools (equal highs/lows, old extremes)
  * supply/demand zones         (last opposing candle before displacement)
  * displacement & structure    (BOS / CHoCH on COMPLETED closes only)
  * room to the next opposing level (in R and ATR units)

A wick through a level is data, never proof; breakout confirmation always
requires a completed candle CLOSE beyond the level (used by the position
policy, section 3).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..strategies.gold_specs.indicators import prepare, wilder_atr

PIVOT_LEFT = 3            # bars left of a pivot
PIVOT_RIGHT = 3           # bars right of a pivot (completed candles only)
CLUSTER_ATR = 0.30        # levels within this ATR multiple cluster together
EQUAL_TOLERANCE_ATR = 0.10  # "equal highs/lows" tolerance (liquidity pools)
ZONE_MAX_AGE = 120        # a zone older than this many bars is stale
SWEEP_LOOKBACK = 40       # look left this many bars for swept liquidity


# ------------------------------------------------------------------ pivots
def _swing_points(d: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    """Fractal swing highs/lows: a bar whose high/low is the extreme within
    PIVOT_LEFT bars before and PIVOT_RIGHT bars after (all completed)."""
    h = d["high"].to_numpy()
    l = d["low"].to_numpy()
    n = len(d)
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        win_h = h[i - PIVOT_LEFT:i + PIVOT_RIGHT + 1]
        win_l = l[i - PIVOT_LEFT:i + PIVOT_RIGHT + 1]
        if h[i] >= win_h.max() and (win_h == h[i]).sum() == 1:
            highs.append({"price": float(h[i]), "index": i,
                          "time": str(d.index[i])})
        if l[i] <= win_l.min() and (win_l == l[i]).sum() == 1:
            lows.append({"price": float(l[i]), "index": i,
                         "time": str(d.index[i])})
    return {"highs": highs, "lows": lows}


def _cluster(points: List[Dict[str, Any]], atr: float,
             price: float) -> List[Dict[str, Any]]:
    """Cluster nearby swing points into level objects with touch counts."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p["price"])
    clusters: List[List[Dict[str, Any]]] = [[pts[0]]]
    for p in pts[1:]:
        if p["price"] - clusters[-1][-1]["price"] <= CLUSTER_ATR * atr:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    out = []
    for cl in clusters:
        prices = [p["price"] for p in cl]
        level = sum(prices) / len(prices)
        out.append({
            "price": round(level, 5),
            "touches": len(cl),
            "last_time": cl[-1]["time"],
            "side": "above" if level > price else "below",
            "distance": round(abs(level - price), 5),
        })
    return out


# ----------------------------------------------------------------- levels
def key_levels(d: Optional[pd.DataFrame], price: float,
               atr: Optional[float]) -> Dict[str, Any]:
    """Major/internal S/R + liquidity pools around `price` on ONE timeframe.
    Missing/short data -> {"ok": False} (callers must treat as no context,
    never as a signal)."""
    d2 = prepare(d)
    if d2 is None or len(d2) < (PIVOT_LEFT + PIVOT_RIGHT + 10) \
            or price <= 0 or not atr:
        return {"ok": False,
                "reason": "insufficient or stale candles for level analysis"}

    swings = _swing_points(d2)
    all_pts = swings["highs"] + swings["lows"]
    levels = _cluster(all_pts, atr, price)
    majors = [lv for lv in levels if lv["touches"] >= 3] or \
             (sorted(levels, key=lambda x: -x["touches"])[:1] if levels else [])
    internal = [lv for lv in levels if lv not in majors]

    # liquidity pools: equal highs (buy-side) / equal lows (sell-side) -
    # two or more swing extremes agreeing within a tight tolerance = a
    # cluster of resting stops (look left across the whole mapped history)
    eq_tol = EQUAL_TOLERANCE_ATR * atr
    bsl: List[float] = []
    ssl: List[float] = []
    hi_pts = sorted((p["price"] for p in swings["highs"]), reverse=True)
    lo_pts = sorted((p["price"] for p in swings["lows"]))
    used = set()
    for i, v in enumerate(hi_pts):
        if i in used:
            continue
        group = [w for j, w in enumerate(hi_pts)
                 if abs(w - v) <= eq_tol and not (j in used and w != v)]
        if len(group) >= 2:
            bsl.append(round(max(group), 5))
            used.update(j for j, w in enumerate(hi_pts)
                        if abs(w - v) <= eq_tol)
    used = set()
    for i, v in enumerate(lo_pts):
        if i in used:
            continue
        group = [w for j, w in enumerate(lo_pts)
                 if abs(w - v) <= eq_tol and not (j in used and w != v)]
        if len(group) >= 2:
            ssl.append(round(min(group), 5))
            used.update(j for j, w in enumerate(lo_pts)
                        if abs(w - v) <= eq_tol)

    return {"ok": True, "price": price, "atr": round(atr, 5),
            "majors": majors, "internal": internal,
            "buy_side_liquidity": sorted(set(bsl), reverse=True),
            "sell_side_liquidity": sorted(set(ssl)),
            "swing_highs": [s["price"] for s in swings["highs"][-6:]],
            "swing_lows": [s["price"] for s in swings["lows"][-6:]]}


# --------------------------------------------------- structure & zones
def structure(d: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """BOS / CHoCH + displacement on COMPLETED closes. Descriptive only."""
    d2 = prepare(d)
    if d2 is None or len(d2) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return {"ok": False, "reason": "insufficient candles for structure"}
    c = d2["close"].to_numpy()
    swings = _swing_points(d2)
    last_hi = swings["highs"][-1]["price"] if swings["highs"] else None
    last_lo = swings["lows"][-1]["price"] if swings["lows"] else None
    i_hi = swings["highs"][-1]["index"] if swings["highs"] else -1
    i_lo = swings["lows"][-1]["index"] if swings["lows"] else -1
    bos = None
    if last_hi is not None and c[i_hi + 1:].max() > last_hi:
        bos = {"type": "BULLISH_BOS", "level": round(last_hi, 5)}
    elif last_lo is not None and c[i_lo + 1:].min() < last_lo:
        bos = {"type": "BEARISH_BOS", "level": round(last_lo, 5)}
    atr = wilder_atr(d2, 14)
    disp = None
    if atr:
        for i in range(len(d2) - 1, len(d2) - 6, -1):
            body = abs(c[i] - float(d2["open"].iloc[i]))
            if body >= 1.5 * atr:
                disp = {"index": i, "direction": "UP" if c[i] > float(d2["open"].iloc[i]) else "DOWN",
                        "body_atr": round(body / atr, 2),
                        "time": str(d2.index[i])}
                break
    return {"ok": True, "last_event": bos,
            "recent_displacement": disp,
            "trend_note": ("close above the last confirmed swing high"
                           if bos and bos["type"] == "BULLISH_BOS" else
                           "close below the last confirmed swing low"
                           if bos else "no confirmed structural break")}


def supply_demand(d: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Recent supply/demand zones: the last opposing candle before a
    displacement leg (base candle), with staleness tracking."""
    d2 = prepare(d)
    if d2 is None or len(d2) < 30:
        return {"ok": False, "reason": "insufficient candles for zones"}
    atr = wilder_atr(d2, 14)
    if not atr:
        return {"ok": False, "reason": "ATR unavailable"}
    o = d2["open"].to_numpy(); c = d2["close"].to_numpy()
    h = d2["high"].to_numpy(); l = d2["low"].to_numpy()
    zones: List[Dict[str, Any]] = []
    n = len(d2)
    for i in range(n - 4, PIVOT_LEFT, -1):
        body = abs(c[i] - o[i])
        if body < 1.5 * atr or i == 0:
            continue
        up = c[i] > o[i]
        base = i - 1
        if base < 0:
            break
        if up and c[base] < o[base] and (h[i] - l[i]) > 0:
            zones.append({"type": "DEMAND", "high": round(max(h[base], l[base] + 0.1 * atr), 5),
                          "low": round(l[base], 5), "created": str(d2.index[base]),
                          "age_bars": n - 1 - base,
                          "displacement_body_atr": round(body / atr, 2)})
        elif not up and c[base] > o[base]:
            zones.append({"type": "SUPPLY", "high": round(h[base], 5),
                          "low": round(min(l[base], h[base] - 0.1 * atr), 5),
                          "created": str(d2.index[base]),
                          "age_bars": n - 1 - base,
                          "displacement_body_atr": round(body / atr, 2)})
        if len(zones) >= 2:
            break
    fresh = [z for z in zones if z["age_bars"] <= ZONE_MAX_AGE]
    return {"ok": True, "zones": fresh, "stale_zones_dropped": len(zones) - len(fresh)}


# ------------------------------------------------------ direction summary
def room_to_next_level(levels: Dict[str, Any], price: float,
                       direction: str, risk: float) -> Dict[str, Any]:
    """For a trade direction: the nearest OPPOSING level in the trade's
    path, the distance to it, and the available movement expressed in R.
    Descriptive only - never a promise that price will travel there."""
    if not levels.get("ok"):
        return {"ok": False, "reason": levels.get("reason", "no levels")}
    up = direction == "LONG"
    pool = (levels["buy_side_liquidity"] if up else levels["sell_side_liquidity"])
    candidates = [lv["price"] for lv in levels.get("majors", []) + levels.get("internal", [])
                  if (lv["price"] > price if up else lv["price"] < price)]
    candidates += [p for p in pool if (p > price if up else p < price)]
    if not candidates:
        return {"ok": True, "next_level": None,
                "note": "no mapped opposing level ahead - open structure"}
    nxt = min(candidates) if up else max(candidates)
    dist = abs(nxt - price)
    return {"ok": True, "next_level": nxt, "distance": round(dist, 5),
            "distance_r": round(dist / risk, 2) if risk > 0 else None,
            "distance_atr": round(dist / levels["atr"], 2) if levels.get("atr") else None}


def analyze(market: str, direction: Optional[str] = None, risk: float = 0.0,
            provider=None, h1: Optional[pd.DataFrame] = None,
            h4: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """One-call MARKET FOOD summary for a market (optionally for a trade
    direction). Uses completed 1H + 4H candles. Never throws."""
    try:
        if provider is not None:
            if h1 is None:
                h1 = provider.get_candles(market, "1H", limit=400)
            if h4 is None:
                h4 = provider.get_candles(market, "4H", limit=300)
        h1d = prepare(h1)
        h4d = prepare(h4)
        price = float(h1d["close"].iloc[-1]) if h1d is not None and len(h1d) else 0.0
        atr = wilder_atr(h1d, 14) if h1d is not None and len(h1d) > 15 else None
        h4atr = wilder_atr(h4d, 14) if h4d is not None and len(h4d) > 15 else None
        out: Dict[str, Any] = {
            "market": market, "price": price, "label": "MARKET_FOOD",
            "evidence_only": True,
            "h1": key_levels(h1, price, atr),
            "h4": key_levels(h4, price, h4atr),
            "structure_h1": structure(h1),
            "structure_h4": structure(h4),
            "zones_h1": supply_demand(h1),
        }
        if direction and risk > 0:
            out["room"] = room_to_next_level(out["h1"], price, direction, risk)
            out["room_h4"] = room_to_next_level(out["h4"], price, direction, risk)
        return out
    except Exception as exc:                       # context must never throw
        return {"market": market, "ok": False, "label": "MARKET_FOOD",
                "error": f"{type(exc).__name__}: {exc}",
                "note": "market food unavailable - manage without it"}
