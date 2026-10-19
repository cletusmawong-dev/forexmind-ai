"""MARKET FOOD module tests (owner brief 2026-10-10, section 2).

Levels, liquidity pools, structure, zones, room-to-level, and honest
degradation on missing/stale data. Descriptive only - no probabilities.
"""
import pandas as pd
import pytest

from app.analysis import market_food as mf
from app.strategies.gold_specs.indicators import prepare


def mk(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def trending_h1(bars=300, base=2400.0, up_segments=((0, 120, 1.0), (120, 200, -1.2),
                                                    (200, 260, 1.5), (260, 300, -0.8))):
    idx = pd.date_range(end="2026-10-09T20:00", periods=bars, freq="1h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = base
    for i in range(bars):
        step = 0.0
        for a, b, s in up_segments:
            if a <= i < b:
                step = s
                break
        px += step
        o.append(px - step); h.append(px + 1.0); l.append(px - 1.5); c.append(px)
    return mk(ts, o, h, l, c)


def test_swing_points_found_and_completed_only():
    d = trending_h1()
    swings = mf._swing_points(d)
    assert swings["highs"] and swings["lows"]
    # pivots need PIVOT_RIGHT completed bars after them: none at the very end
    n = len(d)
    assert all(s["index"] < n - mf.PIVOT_RIGHT for s in swings["highs"] + swings["lows"])


def test_key_levels_cluster_and_sides():
    d = trending_h1()
    price = float(d["close"].iloc[-1])
    atr = 3.0
    lv = mf.key_levels(d, price, atr)
    assert lv["ok"]
    all_prices = [x["price"] for x in lv["majors"] + lv["internal"]]
    assert any(p > price for p in all_prices) and any(p < price for p in all_prices)
    for x in lv["majors"]:
        # majors = well-touched clusters, or the single best-touch fallback
        assert x["touches"] >= 3 or len(lv["majors"]) == 1


def test_key_levels_insufficient_data_is_honest():
    d = trending_h1(bars=12)
    lv = mf.key_levels(d, 2400.0, 3.0)
    assert not lv["ok"] and "insufficient" in lv["reason"]


def test_liquidity_pools_from_equal_extremes():
    idx = pd.date_range(end="2026-10-09T20:00", periods=120, freq="1h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = 2400.0
    for i in range(120):
        if i in (30, 60, 90):                  # three DISTINCT touches of 2410
            o.append(px); h.append(2410.0); l.append(px - 1.0); c.append(px - 0.5)
        else:
            o.append(px - 0.3); h.append(px + 0.5); l.append(px - 1.0); c.append(px)
    d = mk(ts, o, h, l, c)
    lv = mf.key_levels(d, float(d["close"].iloc[-1]), 2.0)
    assert lv["ok"] and lv["buy_side_liquidity"], "equal highs must map as buy-side liquidity"


def test_structure_bos_on_completed_closes_only():
    d = trending_h1()
    st = mf.structure(d)
    assert st["ok"]
    # with net-up data the last structural event (if any) must be bullish
    if st["last_event"]:
        assert st["last_event"]["type"] == "BULLISH_BOS"


def test_supply_demand_zones_with_staleness():
    d = trending_h1()
    z = mf.supply_demand(d)
    assert z["ok"]
    for zone in z["zones"]:
        assert zone["type"] in ("DEMAND", "SUPPLY")
        assert zone["age_bars"] <= mf.ZONE_MAX_AGE


def test_room_to_next_level_units():
    levels = {"ok": True, "atr": 2.0, "majors": [
        {"price": 2430.0, "touches": 4, "side": "above", "distance": 30.0}],
        "internal": [], "buy_side_liquidity": [2440.0], "sell_side_liquidity": []}
    room = mf.room_to_next_level(levels, 2400.0, "LONG", risk=10.0)
    assert room["ok"] and room["next_level"] == 2430.0
    assert room["distance_r"] == 3.0 and room["distance_atr"] == 15.0


def test_room_open_structure_when_nothing_ahead():
    levels = {"ok": True, "atr": 2.0, "majors": [
        {"price": 2380.0, "touches": 3, "side": "below", "distance": 20.0}],
        "internal": [], "buy_side_liquidity": [], "sell_side_liquidity": []}
    room = mf.room_to_next_level(levels, 2400.0, "LONG", risk=10.0)
    assert room["ok"] and room["next_level"] is None
    assert "open structure" in room["note"]


def test_analyze_never_throws_on_garbage():
    out = mf.analyze("XAUUSD", provider=None, h1=None, h4=None)
    assert out.get("label") == "MARKET_FOOD"
    assert not (out.get("h1") or {"ok": True}).get("ok", False)


def test_analyze_direction_aware():
    d = trending_h1()
    out = mf.analyze("XAUUSD", direction="LONG", risk=10.0, provider=None,
                     h1=d, h4=d)
    assert out["evidence_only"] is True
    assert out["room"]["ok"]
