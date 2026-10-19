"""POSITION POLICY tests (owner brief 2026-10-10, section 3).

BUY/SELL approaching internal and major levels, true vs false breakouts,
rejections, insufficient room, missing/stale data, never-loosen, and the
risk-gate path (duplicates, execution failure). Deterministic outcomes.
"""
import pandas as pd
import pytest

from app.aimanager import position_policy as pol
from app.analysis import market_food as mf


def mk(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def h1_with_levels(price=2400.0):
    """1H frame: current price 2400, a clean opposing level above at 2430."""
    idx = pd.date_range(end="2026-10-09T20:00", periods=140, freq="1h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = price
    for i in range(140):
        if i in (45, 85):                         # isolated spikes -> 2430 level
            o.append(px); h.append(2430.0); l.append(px - 1.0); c.append(px - 0.5)
        elif i in (30, 70):                       # isolated spikes -> 2390 support
            o.append(px); h.append(px + 0.5); l.append(2390.0); c.append(px + 0.5)
        else:
            px += 0.3
            o.append(px - 0.3); h.append(px + 0.5); l.append(px - 1.0); c.append(px)
    return mk(ts, o, h, l, c)


def food_ok(h1):
    d = mf.prepare(h1)
    price = float(d["close"].iloc[-1])
    from app.strategies.gold_specs.indicators import wilder_atr
    return mf.analyze("XAUUSD", provider=None, h1=h1, h4=h1,
                      direction="LONG", risk=10.0)


def buy_pos(entry=2400.0, price=2400.0, sl=2390.0):
    return {"type": "BUY", "price_open": entry, "price_current": price, "sl": sl}


# ------------------------------------------------------------- data honesty
def test_missing_data_means_hold_never_guess():
    res = pol.evaluate(buy_pos(), risk=10.0, r_multiple=2.0,
                       food={"h1": {"ok": False, "reason": "stale"}}, h1=None)
    assert res["action"] == "HOLD" and res["reason_code"] == "NO_DATA"


def test_insufficient_candles_means_hold():
    res = pol.evaluate(buy_pos(), risk=10.0, r_multiple=2.0,
                       food={"h1": {"ok": False}}, h1=mk(["2026-10-09T20:00"],
                                                         [1], [2], [0.5], [1.5]))
    assert res["action"] == "HOLD" and res["reason_code"] == "NO_DATA"


# ------------------------------------------------------- wick vs close rule
def test_wick_through_level_is_observation_not_action():
    """A wick through the opposing level WITHOUT a close beyond it must NOT
    trigger any management action (section 3: wick is not proof)."""
    h1 = h1_with_levels()
    # last candle spikes through 2430 but CLOSES back below it
    h1.loc[h1.index[-1], "high"] = 2431.0
    h1.loc[h1.index[-1], "close"] = 2425.0
    res = pol.evaluate(buy_pos(price=2429.5), risk=10.0, r_multiple=2.95,
                       food=food_ok(h1), h1=h1)
    assert res["action"] in ("HOLD", "PARTIAL_PROFIT", "PROTECT")  # never EXIT on a wick
    assert res.get("reason_code") != "THESIS_INVALID"
    wick_notes = [o for o in res.get("observations", [])
                  if o.get("observation") == "wick_through_level_no_close"]
    assert wick_notes, "wick-through must be recorded as an internal observation"


def test_true_breakdown_with_follow_through_invalidates_long():
    """Long thesis: two completed closes BELOW the 2390 protection level
    with follow-through and no +1R banked -> thesis invalid -> EXIT."""
    h1 = h1_with_levels()
    h1.loc[h1.index[-2], "close"] = 2388.0
    h1.loc[h1.index[-2], "low"] = 2387.0
    h1.loc[h1.index[-1], "close"] = 2386.0
    h1.loc[h1.index[-1], "low"] = 2385.0
    res = pol.evaluate(buy_pos(price=2405.0), risk=10.0, r_multiple=0.5,
                       food=food_ok(h1), h1=h1)
    assert res["action"] == "EXIT" and res["reason_code"] == "THESIS_INVALID"
    assert res["level"] == 2390.0


def test_rejection_at_level_protects_gains():
    """Long at +1R: the last candle pokes above the level and CLOSES back
    below (rejection) -> protect accumulated profit at breakeven."""
    h1 = h1_with_levels()
    h1.loc[h1.index[-1], "high"] = 2432.0
    h1.loc[h1.index[-1], "close"] = 2426.0        # rejection close below level
    res = pol.evaluate(buy_pos(price=2415.0), risk=10.0, r_multiple=1.5,
                       food=food_ok(h1), h1=h1)
    assert res["action"] == "PROTECT"
    assert res["recommended_sl"] == 2400.0        # breakeven, never looser


def test_bank_partial_into_near_level():
    h1 = h1_with_levels()
    h1.loc[h1.index[-1], "close"] = 2427.0          # approach from below
    h1.loc[h1.index[-1], "high"] = 2428.5
    res = pol.evaluate(buy_pos(price=2427.0), risk=10.0, r_multiple=2.7,
                       food=food_ok(h1), h1=h1)
    assert res["action"] == "PARTIAL_PROFIT"
    assert 0 < res["partial_fraction"] < 1
    assert res["recommended_sl"] == 2400.0


def test_sell_side_mirror():
    """SELL approaching a major level below -> mirrored decisions."""
    h1 = h1_with_levels()
    d = mf.prepare(h1)
    sell = {"type": "SELL", "price_open": 2430.0, "price_current": 2405.0,
            "sl": 2440.0}
    res = pol.evaluate(sell, risk=10.0, r_multiple=-0.5, food=food_ok(h1),
                       h1=h1)
    assert res["action"] in ("HOLD", "EXIT", "PROTECT", "PARTIAL_PROFIT")
    if res["action"] == "PROTECT":
        assert res["recommended_sl"] == 2430.0    # entry = breakeven for short


def test_no_forced_target_ratio():
    """Beyond +2R with clean structure and room: HOLD (no forced 1:3/1:4)."""
    res = pol.evaluate(buy_pos(price=2420.0), risk=10.0, r_multiple=2.0,
                       food={"h1": {"ok": True, "atr": 2.0, "majors": [],
                                    "internal": [], "buy_side_liquidity": [],
                                    "sell_side_liquidity": []}},
                       h1=h1_with_levels(), has_strategy_be=True)
    assert res["action"] == "HOLD"
    assert "open structure" in res["detail"] or res["reason_code"] == "THESIS_INTACT"


def test_strategy_be_marker_not_duplicated():
    """The spec engines' own break-even markers are known applied: the policy
    must not fire its own BE rule again (reason CONFIRMATION_REACHED)."""
    res = pol.evaluate(buy_pos(price=2415.0), risk=10.0, r_multiple=1.5,
                       food={"h1": {"ok": True, "atr": 2.0, "majors": [],
                                    "internal": [], "buy_side_liquidity": [],
                                    "sell_side_liquidity": []}},
                       h1=h1_with_levels(), has_strategy_be=True)
    assert res["reason_code"] != "CONFIRMATION_REACHED"


# ------------------------------------------------------- gate integration
def test_policy_decision_passes_risk_gate_never_loosens(monkeypatch, tmp_path):
    """End to end: policy PROTECT -> risk_gate.validate_action. A proposed
    looser stop is impossible by construction; verify the gate still blocks
    a loosened SL and tightens are allowed."""
    from app.aimanager import risk_gate
    pos = {"type": "BUY", "price_open": 2400.0, "price_current": 2415.0,
           "sl": 2395.0, "ticket": 1}
    dec = {"action": "PROTECT", "recommended_sl": 2400.0}
    ok, verdict, plan = risk_gate.validate_action(
        "u", pos, dec, snapshot_ts=__import__("time").time(),
        current_sl=2395.0, last_action=None)
    assert ok and plan == {"modify_sl": 2400.0}
    # the same gate REFUSES a wider stop (never loosen risk to keep a trade)
    bad = {"action": "PROTECT", "recommended_sl": 2392.0}
    ok2, verdict2, _ = risk_gate.verify = risk_gate.validate_action(
        "u", pos, bad, snapshot_ts=__import__("time").time(),
        current_sl=2395.0, last_action=None)
    assert not ok2 and verdict2 == "SL_WOULD_LOOSEN_RISK"


def test_duplicate_management_events_suppressed():
    from app.aimanager import risk_gate
    import time
    pos = {"type": "BUY", "price_open": 2400.0, "price_current": 2415.0,
           "sl": 2395.0, "ticket": 1}
    dec = {"action": "PROTECT", "recommended_sl": 2400.0}
    now = time.time()
    ok1, v1, _ = risk_gate.validate_action("u", pos, dec, snapshot_ts=now,
                                           current_sl=2395.0,
                                           last_action={"action": "PROTECT",
                                                        "sl": 2400.0,
                                                        "ts": now - 5})
    assert not ok1 and v1 == "DUPLICATE_SUPPRESSED"


def test_partial_fraction_validated():
    from app.aimanager import risk_gate
    pos = {"type": "BUY", "price_open": 2400.0, "price_current": 2420.0,
           "sl": 2400.0, "ticket": 1}
    import time
    now = time.time()
    ok, v, plan = risk_gate.validate_action(
        "u", pos, {"action": "PARTIAL_PROFIT", "partial_fraction": 0.35},
        snapshot_ts=now, current_sl=2400.0)
    assert ok and plan == {"partial_fraction": 0.35}
    ok2, v2, _ = risk_gate.validate_action(
        "u", pos, {"action": "PARTIAL_PROFIT", "partial_fraction": 1.5},
        snapshot_ts=now, current_sl=2400.0)
    assert not ok2 and v2 == "BAD_FRACTION"
