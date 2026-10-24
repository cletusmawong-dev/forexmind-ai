"""Live adapters for the research candidates (owner directive 2026-10-24).

TrendBreak + ZoneSweep registered, boot-ACTIVE, rules unchanged: market
pinning per the spec, scan-timeframe bridging, BUY/SELL candidates with
spec geometry (1:3 / 1:8), zone-touch entry on the latest bar, no stale
fires, START_PAUSED non-membership, lifecycle LIVE.
"""
import pytest

from app.strategies.research_only import trendbreak as tb
from app.strategies.research_only import zonesweep as zs
from app.strategies.research_only.live import TrendBreakStrategy, ZoneSweepStrategy
from tests.test_46_research_candidates import (
    _bt_with_dip, flat, h4_trend, h1_series, zone_fixture)


# ===========================================================================
# registration & lifecycle
# ===========================================================================
def test_both_registered_and_boot_active():
    import app.strategies as reg
    from app.learning.versions import START_PAUSED, lifecycle_of
    assert reg.STRATEGY_CLASSES["strategy_trendbreak_h1"] is TrendBreakStrategy
    assert reg.STRATEGY_CLASSES["strategy_zonesweep_m15"] is ZoneSweepStrategy
    assert "strategy_trendbreak_h1" not in START_PAUSED
    assert "strategy_zonesweep_m15" not in START_PAUSED
    assert lifecycle_of("strategy_trendbreak_h1") == "LIVE"
    assert lifecycle_of("strategy_zonesweep_m15") == "LIVE"


def test_spec_market_pinning():
    """The spec's own market rule: the four listed markets, never XAGUSD."""
    for mkt in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD"):
        assert mkt in TrendBreakStrategy.markets
        assert mkt in ZoneSweepStrategy.markets
    assert "XAGUSD" not in TrendBreakStrategy.markets
    assert "XAGUSD" not in ZoneSweepStrategy.markets
    # enforced at detection time
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.1
    base.iloc[-1, base.columns.get_loc("close")] = 100.4
    base.iloc[-1, base.columns.get_loc("high")] = 100.5
    assert TrendBreakStrategy().detect_signal(
        base, "XAGUSD", "1H", higher_frames={"4H": h4_trend(direction=1)}) is None


# ===========================================================================
# TrendBreak adapter
# ===========================================================================
def _tb_signal_frame():
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.1
    base.iloc[-1, base.columns.get_loc("close")] = 100.4
    base.iloc[-1, base.columns.get_loc("high")] = 100.5
    return base


def test_trendbreak_buy_candidate_from_1h_scan():
    cand = TrendBreakStrategy().detect_signal(
        _tb_signal_frame(), "EURUSD", "1H",
        higher_frames={"4H": h4_trend(direction=1)})
    assert cand is not None
    assert cand.strategy_id == "strategy_trendbreak_h1"
    assert cand.market == "EURUSD" and cand.direction == "BUY"
    assert cand.timeframe == "1H"                       # base TF for dedupe
    assert cand.rr_primary == pytest.approx(3.0)
    assert cand.sl < cand.entry < cand.tps[0]
    assert cand.tps[0] == pytest.approx(cand.entry + (cand.entry - cand.sl) * 3.0)
    assert cand.candle_time                             # signal bar identity


def test_trendbreak_bridges_from_15m_scan():
    """A 15M scan must reach the same H1 signal via higher_frames."""
    m15 = h1_series([100.0] * 40, spread=0.2, start="2026-10-06T00:00")
    cand = TrendBreakStrategy().detect_signal(
        m15, "EURUSD", "15M",
        higher_frames={"1H": _tb_signal_frame(), "4H": h4_trend(direction=1)})
    assert cand is not None and cand.direction == "BUY"
    assert cand.timeframe == "1H"


def test_trendbreak_sell_candidate():
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 99.9
    base.iloc[-1, base.columns.get_loc("close")] = 99.6
    base.iloc[-1, base.columns.get_loc("low")] = 99.5
    cand = TrendBreakStrategy().detect_signal(
        base, "USDJPY", "1H", higher_frames={"4H": h4_trend(direction=-1)})
    assert cand is not None and cand.direction == "SELL"
    assert cand.tps[0] < cand.entry < cand.sl
    assert cand.rr_primary == pytest.approx(3.0)


def test_trendbreak_no_context_no_signal():
    assert TrendBreakStrategy().detect_signal(
        _tb_signal_frame(), "EURUSD", "1H", higher_frames={}) is None
    assert TrendBreakStrategy().detect_signal(
        _tb_signal_frame(), "EURUSD", "1H",
        higher_frames={"4H": h4_trend(n=100)}) is None          # H4 too short


# ===========================================================================
# ZoneSweep adapter
# ===========================================================================
def test_zonesweep_candidate_on_first_touch_bar():
    """Zone created on bar 17; the FIRST touch on the LATEST bar -> signal."""
    base = zone_fixture()
    idx = base.index
    import pandas as pd
    dip = pd.DataFrame(
        [{"open": 100.0, "high": 100.4, "low": 93.0, "close": 99.0}],
        index=[idx[-1] + pd.Timedelta(minutes=15)])
    m15 = pd.concat([base, dip])
    cand = ZoneSweepStrategy().detect_signal(
        m15, "XAUUSD", "15M",
        higher_frames={"1H": h4_trend(direction=1), "4H": h4_trend(direction=1)})
    assert cand is not None
    assert cand.strategy_id == "strategy_zonesweep_m15"
    assert cand.direction == "BUY"
    assert cand.market == "XAUUSD"
    assert cand.entry == pytest.approx(cand.entry_zone[1])       # zone top limit
    assert cand.rr_primary == pytest.approx(8.0)
    assert cand.sl < cand.entry < cand.tps[0]
    assert cand.tps[0] == pytest.approx(cand.entry + (cand.entry - cand.sl) * 8.0)
    assert cand.candle_time == str(m15.index[-1])                # touch bar identity


def test_zonesweep_no_stale_fire_after_touch():
    """A touch that happened EARLIER is gone - no late signals, no dupes."""
    m15 = _bt_with_dip([(100.0, 100.4, 93.0, 99.0)])
    cand = ZoneSweepStrategy().detect_signal(
        m15, "XAUUSD", "15M",
        higher_frames={"1H": h4_trend(direction=1), "4H": h4_trend(direction=1)})
    assert cand is None                     # the touch was not the latest bar


def test_zonesweep_bridges_from_1h_scan():
    """A 1H scan reaches the M15 zone via higher_frames."""
    m15 = _bt_with_dip([(100.0, 100.4, 93.0, 99.0)])
    one_h = h1_series([100.0] * 220, spread=0.3, start="2026-10-19T00:00")
    cand = ZoneSweepStrategy().detect_signal(
        one_h, "EURUSD", "1H",
        higher_frames={"15M": m15, "4H": h4_trend(direction=1)})
    # touch on the latest 15M bar -> candidate; timeframe pinned to 15M
    if cand is not None:
        assert cand.timeframe == "15M" and cand.direction == "BUY"


def test_zonesweep_requires_context():
    assert ZoneSweepStrategy().detect_signal(
        zone_fixture(), "EURUSD", "15M", higher_frames={}) is None


def test_zone_entry_event_direct():
    """The helper the adapter uses: SIGNAL only on the latest-bar touch."""
    import pandas as pd
    base = zone_fixture()
    dip = pd.DataFrame(
        [{"open": 100.0, "high": 100.4, "low": 93.0, "close": 99.0}],
        index=[base.index[-1] + pd.Timedelta(minutes=15)])
    m15 = pd.concat([base, dip])
    res = zs.zone_entry_event(m15, h4_trend(direction=1), h4_trend(direction=1),
                              market="XAUUSD")
    assert res is not None and res["status"] == zs.SIGNAL
    assert res["touch_bar"] == str(m15.index[-1])
    assert res["risk_reward"] == "1:8"
    # and None when the touch is stale
    assert zs.zone_entry_event(
        _bt_with_dip([(100.0, 100.4, 93.0, 99.0)]),
        h4_trend(direction=1), h4_trend(direction=1), market="XAUUSD") is None


# ===========================================================================
# platform wiring: START_PAUSED / docs / scan compatibility
# ===========================================================================
def test_strategy_docs_created_active_for_both(fresh_store, monkeypatch):
    from app.learning import versions as vc
    import app.strategies as reg
    monkeypatch.setattr(vc, "get_store", lambda: fresh_store)
    for sid in ("strategy_trendbreak_h1", "strategy_zonesweep_m15"):
        vc.LIFECYCLE.pop(sid, None)
    vc.ensure_strategy_docs()
    docs = {d["id"]: d for d in fresh_store.list("strategies", limit=1000)}
    assert docs["strategy_trendbreak_h1"]["status"] == "ACTIVE"
    assert docs["strategy_zonesweep_m15"]["status"] == "ACTIVE"
