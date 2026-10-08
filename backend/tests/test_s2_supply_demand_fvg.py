"""Strategy 2 (Supply & Demand + FVG) unit tests - owner brief 2026-10-07.

Covers: demand/supply zone detection, strict 3-candle FVGs, the full
BUY/SELL flows (zone -> displacement -> FVG -> retest -> entry), the persisted
state doc, no-signal-without-pullback, duplicate prevention, decisive-break
invalidation, zone-age timeout, no-lookahead, lifecycle registry,
RETIRED->DISABLED reconciliation and the 9/21 EMA regression.
"""
import os
import sys
from datetime import timedelta

import pandas as pd
import pytest

os.environ["REPLAY_ENABLED"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.strategies.strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy  # noqa: E402


# ---------------------------------------------------------------------------
# deterministic frame builders (15M bars, UTC index)
# ---------------------------------------------------------------------------
def _frame(closes, opens=None, start="2026-10-01 00:00:00+00:00", spread=0.3):
    """Build an OHLCV frame; each bar's high/low wrap the body by `spread`."""
    n = len(closes)
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    rows = []
    for o, c in zip(opens, closes):
        hi = max(o, c) + spread
        lo = min(o, c) - spread
        rows.append({"open": o, "high": hi, "low": lo, "close": c, "volume": 100.0})
    return pd.DataFrame(rows, index=idx)


def demand_setup_frame(extra_pullback: bool):
    """Range -> bearish base -> bullish displacement -> bullish FVG.

    bar 40: bearish base (open 100.6 close 99.7) -> demand zone body 99.7-100.6
    bar 42: bullish displacement (open 100.2 close 102.6)
    FVG   : high[41]=100.5 < low[43]=102.3 -> gap 100.5-102.3 (mid 101.4)
    """
    base = [100.0 + (0.1 if i % 3 else -0.1) for i in range(40)]
    base += [99.7, 100.2,             # 40 bearish base, 41 small up
             102.6, 102.9,            # 42 displacement, 43 continuation
             103.1, 103.3]            # 44,45 drift (lows stay above the FVG mid)
    o = [c - 0.1 for c in base[:40]] + [100.6, 99.7, 100.2, 102.6, 103.4, 103.1]
    if extra_pullback:
        # dips into the FVG: bar 47 low 100.9 <= mid 101.4 -> TOUCH, close 101.2
        # stays above fvg_low 100.5 (gap not consumed)
        base += [102.4, 101.2]
        o += [103.4, 102.4]
    return _frame(base, o)


def supply_setup_frame(extra_pullback: bool):
    """Range -> bullish base -> bearish displacement -> bearish FVG.

    bar 40: bullish base (open 97.4 close 98.3) -> supply zone body 97.4-98.3
    bar 42: bearish displacement (open 97.8 close 95.4)
    FVG   : low[41]=97.4 > high[43]=96.6 -> gap 96.6-97.4 (mid 97.0)
    """
    base = [98.0 + (0.1 if i % 3 else -0.1) for i in range(40)]
    base += [98.3, 97.7,              # 40 bullish base, 41 small down
             95.4, 95.1,              # 42 displacement, 43 continuation
             94.8, 94.9]              # 44,45 drift (highs stay below the FVG mid)
    o = [c + 0.1 for c in base[:40]] + [97.4, 98.3, 97.8, 96.3, 94.6, 94.7]
    if extra_pullback:
        # rises into the gap: bar 47 high 97.5 >= mid 97.0 -> TOUCH, close 97.2
        # stays below fvg_high 97.4 (gap not consumed)
        base += [95.6, 97.2]
        o += [94.6, 95.6]
    return _frame(base, o)


@pytest.fixture()
def params():
    return dict(SupplyDemandFvgStrategy.base_params)


@pytest.fixture()
def fresh_store(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    State.store = store
    return store


def _detect(df, market="XAUUSD", tf="15M", p=None):
    st = SupplyDemandFvgStrategy()
    return st.detect_signal(df, market, tf, params=p or dict(st.base_params),
                            score_context={"session": "London"})


# ---------------------------------------------------------------------------
# helpers / zones / FVG
# ---------------------------------------------------------------------------
def test_demand_zone_detected(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg import machine
    df = demand_setup_frame(extra_pullback=False)
    a = machine.atr(df, 14)
    d = 42
    assert machine.displacement_at(df, d, a, 0.60, 1.1) == "bull"
    z = machine.zone_before(df, d, "bull", 3)
    assert z and z["type"] == "demand"
    assert z["zone_low"] == pytest.approx(99.7)
    assert z["zone_high"] == pytest.approx(100.6)


def test_supply_zone_detected(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg import machine
    df = supply_setup_frame(extra_pullback=False)
    a = machine.atr(df, 14)
    d = 42
    assert machine.displacement_at(df, d, a, 0.60, 1.1) == "bear"
    z = machine.zone_before(df, d, "bear", 3)
    assert z and z["type"] == "supply"
    assert z["zone_low"] == pytest.approx(97.4)
    assert z["zone_high"] == pytest.approx(98.3)


def test_bullish_fvg_strict_three_candle(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg import machine
    df = demand_setup_frame(extra_pullback=False)
    f = machine.fvg_centered(df, 42, "bull", 0.0)
    assert f and f["type"] == "bullish_fvg"
    assert f["fvg_low"] == pytest.approx(100.5)     # high[c1]
    assert f["fvg_high"] == pytest.approx(102.3)    # low[c3]
    assert f["fvg_mid"] == pytest.approx((100.5 + 102.3) / 2)


def test_bearish_fvg_strict_three_candle(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg import machine
    df = supply_setup_frame(extra_pullback=False)
    f = machine.fvg_centered(df, 42, "bear", 0.0)
    assert f and f["type"] == "bearish_fvg"
    assert f["fvg_low"] == pytest.approx(96.6)      # high[c3]
    assert f["fvg_high"] == pytest.approx(97.4)     # low[c1]


def test_no_fvg_without_real_gap(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg import machine
    df = _frame([100, 100, 100, 100, 100] * 10)
    assert machine.fvg_centered(df, 42, "bull", 0.0) is None


# ---------------------------------------------------------------------------
# full flows
# ---------------------------------------------------------------------------
def test_full_demand_flow_generates_buy(params, fresh_store):
    cand = _detect(demand_setup_frame(extra_pullback=True))
    assert cand is not None, "demand + FVG + retest must produce a BUY"
    assert cand.direction == "BUY"
    assert cand.extra["zone_type"] == "demand"
    assert cand.extra["fvg_type"] == "bullish_fvg"
    assert cand.extra["fvg_low"] <= cand.entry <= cand.extra["fvg_high"]
    assert cand.sl < cand.extra["zone_low"]          # SL below the demand zone
    assert cand.entry > cand.sl
    assert cand.tps == pytest.approx([cand.entry + (cand.entry - cand.sl),
                                      cand.entry + 2 * (cand.entry - cand.sl),
                                      cand.entry + 3 * (cand.entry - cand.sl)])
    assert cand.extra["retest_time"]
    assert cand.extra["setup_state"] == "SIGNAL"


def test_full_supply_flow_generates_sell(params, fresh_store):
    cand = _detect(supply_setup_frame(extra_pullback=True))
    assert cand is not None, "supply + FVG + retest must produce a SELL"
    assert cand.direction == "SELL"
    assert cand.extra["zone_type"] == "supply"
    assert cand.extra["fvg_type"] == "bearish_fvg"
    assert cand.extra["fvg_low"] <= cand.entry <= cand.extra["fvg_high"]
    assert cand.sl > cand.extra["zone_high"]          # SL above the supply zone
    assert cand.sl > cand.entry > cand.tps[0]         # profits downward
    assert cand.tps == pytest.approx([cand.entry - (cand.sl - cand.entry),
                                      cand.entry - 2 * (cand.sl - cand.entry),
                                      cand.entry - 3 * (cand.sl - cand.entry)])


def test_state_transitions_recorded(params, fresh_store):
    from app.strategies.strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy
    from app.strategies.strategy_2_supply_demand_fvg.state import COLLECTION, doc_id
    _detect(demand_setup_frame(extra_pullback=False))  # no pullback -> arms only
    doc = fresh_store.get(COLLECTION, doc_id("XAUUSD", "15M"))
    assert doc is not None, "machine state must persist"
    assert doc["state"] == "WAITING_RETEST"
    assert doc["direction"] == "bull"
    assert doc["zone"]["type"] == "demand"
    assert doc["fvg"]["fvg_mid"] is not None


def test_no_signal_without_pullback(params, fresh_store):
    assert _detect(demand_setup_frame(extra_pullback=False)) is None


def test_duplicate_prevention_one_entry_per_fvg(params, fresh_store):
    first = _detect(demand_setup_frame(extra_pullback=True))
    assert first is not None
    # same window again: the FVG is consumed - no duplicate signal
    assert _detect(demand_setup_frame(extra_pullback=True)) is None


def test_invalidation_on_decisive_zone_break(params, fresh_store):
    _detect(demand_setup_frame(extra_pullback=False))  # armed
    df = demand_setup_frame(extra_pullback=False)
    crash = _frame([99.0, 98.0], start=str(df.index[-1] + timedelta(minutes=15)))
    from app.strategies.strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy
    st = SupplyDemandFvgStrategy()
    cand = st.detect_signal(pd.concat([df, crash]), "XAUUSD", "15M",
                            params=params, score_context={"session": "London"})
    assert cand is None
    from app.strategies.strategy_2_supply_demand_fvg.state import COLLECTION, doc_id
    doc = fresh_store.get(COLLECTION, doc_id("XAUUSD", "15M"))
    assert doc["state"] == "NO_SETUP"


def test_timeout_returns_to_no_setup(params, fresh_store):
    _detect(demand_setup_frame(extra_pullback=False))  # armed at bar 43
    from app.strategies.strategy_2_supply_demand_fvg.state import COLLECTION, doc_id
    doc = fresh_store.get(COLLECTION, doc_id("XAUUSD", "15M"))
    assert doc["state"] == "WAITING_RETEST"
    p = dict(params)
    p["zone_max_age_bars"] = 2
    df = demand_setup_frame(extra_pullback=False)   # 44 bars, armed at bar 43
    # bars 44,45 hold (no touch, ages 1-2); bar 46 touches at age 3 > 2 -> timeout
    drift = _frame([102.4, 102.3, 101.9], start=str(df.index[-1] + timedelta(minutes=15)))
    from app.strategies.strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy
    st = SupplyDemandFvgStrategy()
    cand = st.detect_signal(pd.concat([df, drift]), "XAUUSD", "15M",
                            params=p, score_context={"session": "London"})
    assert cand is None  # too old: must NOT fire on the touch
    doc = fresh_store.get(COLLECTION, doc_id("XAUUSD", "15M"))
    assert doc["state"] == "NO_SETUP"


def test_no_lookahead_single_evaluation_per_bar(params, fresh_store):
    df = demand_setup_frame(extra_pullback=True)
    upto = len(df) - 1                                # hide the final bar
    early = _detect(df.iloc[:upto].copy())
    full = _detect(df.copy())
    if early is not None:
        assert early.candle_time == str(df.index[upto - 1])
    if full is not None:
        assert full.candle_time == str(df.index[-1])  # signal only on close


# ---------------------------------------------------------------------------
# registry + safety
# ---------------------------------------------------------------------------
def test_lifecycle_new_s2_live_old_retired():
    from app.learning.versions import LIFECYCLE, is_live
    assert LIFECYCLE["strategy_2_supply_demand_fvg"] == "LIVE"
    assert LIFECYCLE["strategy_2_mtf_sweep_bos_retest"] == "RETIRED"
    assert is_live("strategy_2_supply_demand_fvg")
    assert not is_live("strategy_2_mtf_sweep_bos_retest")


def test_reconciliation_retires_old_s2_doc(fresh_store):
    """A prod-like old-S2 doc (ACTIVE + existing version doc) must flip to
    DISABLED on ensure_strategy_docs - on EVERY call, not just first creation."""
    from app.learning.versions import ensure_strategy_docs
    fresh_store.create("strategies", {"id": "strategy_2_mtf_sweep_bos_retest",
                                      "name": "old S2", "status": "ACTIVE",
                                      "active_version": "1.0.0"},
                       doc_id="strategy_2_mtf_sweep_bos_retest")
    fresh_store.create("strategy_versions",
                       {"strategy_id": "strategy_2_mtf_sweep_bos_retest",
                        "version": "1.0.0", "params": {}, "active": True},
                       doc_id="strategy_2_mtf_sweep_bos_retest-v1.0.0")
    fresh_store.create("strategies", {"id": "strategy_2_supply_demand_fvg",
                                      "name": "S2 new", "status": "DISABLED",
                                      "active_version": "2.0.0"},
                       doc_id="strategy_2_supply_demand_fvg")
    fresh_store.create("strategy_versions",
                       {"strategy_id": "strategy_2_supply_demand_fvg",
                        "version": "2.0.0", "params": {}, "active": True},
                       doc_id="strategy_2_supply_demand_fvg-v2.0.0")
    ensure_strategy_docs()
    old = fresh_store.get("strategies", "strategy_2_mtf_sweep_bos_retest")
    new = fresh_store.get("strategies", "strategy_2_supply_demand_fvg")
    assert old["status"] == "DISABLED"                # RETIRED -> DISABLED
    assert new["status"] == "ACTIVE"                  # LIVE -> re-enabled
    evs = [e for e in fresh_store.list("version_events", limit=100)
           if e.get("kind") == "LIFECYCLE"]
    assert any("strategy_2_mtf_sweep_bos_retest" == e.get("strategy_id") for e in evs)


def test_ema_9_21_regression_causal(params, fresh_store):
    """The 9/21 EMA strategy's core series stay causal (no lookahead)."""
    from app.core.indicators import ema
    df = demand_setup_frame(extra_pullback=False)
    e9, e21 = ema(df["close"], 9), ema(df["close"], 21)
    assert pd.notna(e9.iloc[-1]) and pd.notna(e21.iloc[-1])
    # causality: appending a future bar must not change earlier values
    df2 = pd.concat([df, _frame([101.0], start=str(df.index[-1] + timedelta(minutes=15)))])
    e9b = ema(df2["close"], 9)
    assert e9b.iloc[:len(df)].equals(e9)


def test_engine_gates_still_apply(params, fresh_store):
    """Signal generation must stay inside the engine's allowed markets."""
    cand = _detect(demand_setup_frame(extra_pullback=True), market="XAUUSD")
    assert cand is not None
    st = SupplyDemandFvgStrategy()
    assert "XAUUSD" in st.markets
    df = demand_setup_frame(extra_pullback=True)
    assert st.detect_signal(df, "USDCHF", "15M",
                            params=dict(st.base_params),
                            score_context={"session": "London"}) is None
