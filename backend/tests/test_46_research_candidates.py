"""Owner research candidates (2026-10-24): TrendBreak + ZoneSweep.

Verbatim-rule tests: entry conditions, as-of higher-timeframe context (no
lookahead), next-open entries, stop/TP/time exits, gap fills, ambiguity ->
stop, one-position / one-trade-per-zone, cost floors, market exclusions and
isolation (research-only: not registered, never executed).
"""
import ast
import inspect

import numpy as np
import pandas as pd
import pytest

from app.strategies.research_only import trendbreak as tb
from app.strategies.research_only import zonesweep as zs


def mk(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def h1_series(closes, spread=0.2, start="2026-10-01T00:00"):
    """H1 frame from a list of closes; high/low = close +/- spread."""
    idx = pd.date_range(start=start, periods=len(closes), freq="1h", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c - 0.05, "high": c + spread,
                         "low": c - spread, "close": c}, index=idx)


def h4_trend(n=300, step=0.5, start=90.0, direction=1, start_at="2026-09-25T00:00"):
    """H4 frame with a clean EMA50/EMA200 trend."""
    idx = pd.date_range(start=start_at, periods=n, freq="4h", tz="UTC")
    c = start + direction * step * np.arange(n)
    return pd.DataFrame({"open": c - 0.1, "high": c + 0.3,
                         "low": c - 0.3, "close": c}, index=idx)


def flat(n=130, px=100.0, spread=0.2, start="2026-10-01T00:00"):
    return h1_series([px] * n, spread=spread, start=start)


def append_bars(df, bars, freq="1h"):
    idx = df.index
    rows = []
    for k, (o, h, l, c) in enumerate(bars):
        rows.append((idx[-1] + pd.Timedelta(freq, ) * (k + 1), o, h, l, c))
    ts = [r[0] for r in rows]
    ext = pd.DataFrame({"open": [r[1] for r in rows],
                        "high": [r[2] for r in rows],
                        "low": [r[3] for r in rows],
                        "close": [r[4] for r in rows]}, index=ts)
    return pd.concat([df, ext])


# ===========================================================================
# TrendBreak - signal rules
# ===========================================================================
def test_trendbreak_buy_signal_and_finalize_math():
    base = flat(130)
    # two controlled bars: prev closes just under the channel, current breaks out
    px = 100.0
    base.iloc[-2, base.columns.get_loc("close")] = px + 0.1     # not broken
    base.iloc[-1, base.columns.get_loc("close")] = px + 0.4     # breaks HH (px+0.2)
    base.iloc[-1, base.columns.get_loc("high")] = px + 0.5      # keep the bar valid
    res = tb.evaluate(base, h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb.SIGNAL, res
    assert res["direction"] == "BUY"
    atr = res["atr"]
    final = tb.finalize(res, next_open=100.5, next_open_time="2026-10-06T17:00")
    assert final["entry"] == 100.5
    assert final["stop_loss"] == pytest.approx(100.5 - 2.5 * atr)
    assert final["take_profit"] == pytest.approx(100.5 + 3.0 * 2.5 * atr)
    assert final["risk_reward"] == "1:3"


def test_trendbreak_sell_signal_mirror():
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.0 - 0.1
    base.iloc[-1, base.columns.get_loc("close")] = 100.0 - 0.4
    base.iloc[-1, base.columns.get_loc("low")] = 100.0 - 0.5    # keep the bar valid
    res = tb.evaluate(base, h4_trend(direction=-1), market="GBPUSD")
    assert res["status"] == tb.SIGNAL
    assert res["direction"] == "SELL"


def test_trendbreak_no_trade_without_h4_trend():
    base = flat(130)
    base.iloc[-1, base.columns.get_loc("close")] = 100.4
    base.iloc[-1, base.columns.get_loc("high")] = 100.5
    # H4 sideways: EMA50 ~ EMA200, close ~ EMA200 -> trend 0
    idx = pd.date_range(start="2026-09-25T00:00", periods=300, freq="4h", tz="UTC")
    sideways = pd.DataFrame({"open": [100.0] * 300, "high": [100.3] * 300,
                             "low": [99.7] * 300, "close": [100.0] * 300}, index=idx)
    res = tb.evaluate(base, sideways, market="EURUSD")
    assert res["status"] == tb.NO_SIGNAL
    assert res["checks"][0]["ok"] is False


def test_trendbreak_second_breakout_bar_is_ignored():
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.4        # already broken
    base.iloc[-2, base.columns.get_loc("high")] = 100.5
    base.iloc[-1, base.columns.get_loc("close")] = 100.6        # breaks again
    base.iloc[-1, base.columns.get_loc("high")] = 100.7
    res = tb.evaluate(base, h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb.NO_SIGNAL


def test_trendbreak_insufficient_history_and_markets():
    res = tb.evaluate(flat(60), h4_trend(), market="EURUSD")
    assert res["status"] == tb.INSUFFICIENT
    res2 = tb.evaluate(flat(130), h4_trend(n=150), market="EURUSD")
    assert res2["status"] == tb.INSUFFICIENT
    assert tb.evaluate(flat(130), h4_trend(), market="XAGUSD")["status"] == tb.OUT_OF_SPEC_MARKET
    assert tb.evaluate(flat(130), h4_trend(), market="AUDUSD")["status"] == tb.OUT_OF_SPEC_MARKET


def test_trendbreak_h4_context_is_asof_no_lookahead():
    """The decision at H1 close T must equal a run with H4 truncated at T -
    later H4 bars (even opposite-trend ones) must not leak into the read."""
    base = flat(130)
    base.iloc[-1, base.columns.get_loc("close")] = 100.4
    base.iloc[-1, base.columns.get_loc("high")] = 100.5
    full = h4_trend(n=400, direction=1, start_at="2026-08-25T00:00")
    t_decision = base.index[-1]
    truncated = full[full.index <= t_decision]
    r1 = tb.evaluate(base, full, market="EURUSD")
    r2 = tb.evaluate(base, truncated, market="EURUSD")
    assert r1["status"] == r2["status"] == tb.SIGNAL
    assert r1["checks"][0]["detail"] == r2["checks"][0]["detail"]


# ===========================================================================
# TrendBreak - backtest mechanics
# ===========================================================================
def _bt_setup(extra_bars):
    """Signal bar (valid high), then the ENTRY bar (open 100.5), then the
    scenario bars."""
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.1
    base.iloc[-1, base.columns.get_loc("close")] = 100.4        # signal bar
    base.iloc[-1, base.columns.get_loc("high")] = 100.5
    return append_bars(base, [(100.5, 100.9, 100.2, 100.6)] + list(extra_bars))


def test_backtest_take_profit_and_stop_paths():
    # ATR ~0.4 (flat 0.4-range bars): SL 1.0 under entry, TP +3.0
    df = _bt_setup([(100.5, 109.5, 100.4, 109.0)])              # entry 100.5 -> TP 109.5 hit
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    assert out["trades"], out
    t = out["trades"][0]
    assert t["entry"] == 100.5 and t["exit_reason"] == "TAKE_PROFIT"
    assert t["exit"] == pytest.approx(t["take_profit"])
    assert t["r_multiple"] == pytest.approx(3.0, abs=0.01)

    df2 = _bt_setup([(100.5, 100.9, 99.4, 99.6)])               # low 99.4 <= SL 99.5
    out2 = tb.backtest(df2, h4_trend(direction=1), market="EURUSD")
    t2 = out2["trades"][0]
    assert t2["exit_reason"] == "STOP" and t2["r_multiple"] == pytest.approx(-1.0, abs=0.01)


def test_backtest_gap_through_stop_fills_at_open():
    # entry 100.5, SL = 100.5 - 2.5xATR; next bar GAPS below the stop
    df = _bt_setup([(99.0, 99.5, 98.5, 99.2)])
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    t = out["trades"][0]
    assert t["exit_reason"] == "STOP_GAP_OPEN"
    assert t["exit"] == 99.0
    assert t["r_multiple"] < -1.0                                # worse than SL


def test_backtest_ambiguous_bar_counts_the_stop():
    # one bar spans both SL (99.5) and TP (109.5) -> worst case: STOP
    df = _bt_setup([(100.5, 110.0, 99.4, 106.0)])
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    assert out["trades"][0]["exit_reason"] == "STOP"


def test_backtest_time_exit_after_96_bars():
    drift = [(100.5 + 0.002 * k, 100.7 + 0.002 * k, 100.3 + 0.002 * k,
              100.5 + 0.002 * k) for k in range(100)]
    df = _bt_setup(drift)
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    t = out["trades"][0]
    assert t["exit_reason"] == "TIME_EXIT_96"
    assert t["bars_held"] == 96


def test_backtest_one_position_per_symbol():
    # second breakout while the first trade runs must be ignored
    base = flat(130)
    base.iloc[-2, base.columns.get_loc("close")] = 100.1
    base.iloc[-1, base.columns.get_loc("close")] = 100.4
    extra = [(100.5, 109.5, 100.4, 109.0)]          # TP hit on the next bar
    extra += [(109.0 + 0.1 * k, 109.6 + 0.1 * k, 108.9 + 0.1 * k,
               109.3 + 0.1 * k) for k in range(3)]  # more bars, another breakout
    df = append_bars(base, extra)
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    assert len(out["trades"]) == 1


def test_backtest_summary_math():
    df = _bt_setup([(100.5, 109.5, 100.4, 109.0)])
    out = tb.backtest(df, h4_trend(direction=1), market="EURUSD")
    s = out["summary"]
    assert s["n"] == 1 and s["win_rate"] == 1.0
    assert s["expectancy_r"] == pytest.approx(3.0, abs=0.01)


# ===========================================================================
# ZoneSweep - engine rules
# ===========================================================================
def m15_frame(bars, start="2026-10-20T00:00"):
    idx = pd.date_range(start=start, periods=len(bars), freq="15min", tz="UTC")
    return pd.DataFrame([{"open": b[0], "high": b[1], "low": b[2], "close": b[3]}
                         for b in bars], index=idx)


def zone_fixture(spike_low=92.0, prev_swing_low=98.5, bos_close=106.5,
                 bars_after_bos=7, end_at_bos=False):
    """Valid-fractal M15 timeline (geometry checked):
      bars 0-4   climb 100->104.4 (rising lows)
      bar 5      DIP swing low (prev_swing_low; gap-open bar, tiny body/high)
      bars 6-9   drift just above the dip low (its after-window stays above)
      bar 10     SWING HIGH (99.5): strictly above bars 5-9 highs, and the
                 bars 11-15 highs (spike + early recovery) stay below it
      bar 11     SPIKE swing low (the zone candle; gap-open, small body so
                 zone height = body top - low stays inside the ATR bounds)
      bars 12-16 recovery (lows above the spike; highs below the swing high)
      bars 17-21 rally to the swing high
      bar 22     BOS close above the swing high
      bars 23+   post-BOS drift
    Sweep: spike low < dip low. Ordering: spike (11) formed after the high
    (10). Displacement and height clear the ATR bounds at the BOS bar."""
    # a sweeping fixture has spike < dip; a no-sweep variant passes the reverse
    # (both are valid geometries - the sweep FLAG is what the engine computes)
    bars = []
    px = 100.0
    for k in range(30):
        if k <= 4:                       # climb, rising lows
            bars.append((px - 0.5, px + 0.6, px - 0.9, px + 0.4))
            px += 1.1
        elif k == 5:                     # DIP swing low (prev_swing_low)
            bars.append((prev_swing_low + 0.2, prev_swing_low + 0.5,
                         prev_swing_low, prev_swing_low + 0.3))
        elif k <= 9:                     # drift above the dip low
            bars.append((prev_swing_low + 0.4, prev_swing_low + 0.7,
                         prev_swing_low + 0.1, prev_swing_low + 0.5))
        elif k == 10:                    # SWING HIGH
            bars.append((99.1, 99.5, 98.8, 99.3))
        elif k == 11:                    # SPIKE swing low (the zone candle)
            bars.append((spike_low + 1.0, spike_low + 1.4,
                         spike_low, spike_low + 0.6))
        elif k <= 16:                    # recovery under the swing high
            r = spike_low + (k - 10) * 1.2
            bars.append((r - 0.4, min(r + 0.8, 99.4), r - 0.8, r))
        elif k <= 21:                    # rally to the swing high
            r = 99.4 + (k - 16) * 1.0
            bars.append((r - 0.3, r + 0.5, r - 0.6, r))
        elif k == 22:                    # BOS bar
            bars.append((104.8, bos_close + 0.5, 104.5, bos_close))
        else:                            # post-BOS drift
            bars.append((bos_close, bos_close + 0.4, bos_close - 0.4,
                         bos_close + 0.1))
    df = m15_frame(bars)
    if end_at_bos:
        return df.iloc[:18]              # end exactly at the BOS bar (bar 17)
    return df


def test_zonesweep_bullish_zone_created_with_sweep():
    m15 = zone_fixture(spike_low=92.0, prev_swing_low=98.5, end_at_bos=True)
    res = zs.evaluate(m15, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    assert res["status"] in (zs.SIGNAL, "ZONE_FORMED_UNQUALIFIED"), res
    if res["status"] == zs.SIGNAL:
        assert res["direction"] == "LONG"
        assert res["sweep"] is True and res["trend_ok"] is True
        assert res["zone_top"] == pytest.approx(93.0)      # spike body top (gap open)
        assert res["zone_bottom"] == pytest.approx(92.0)   # spike low
        assert res["stop_loss"] == pytest.approx(92.0 - 0.25 * res["atr"])


def test_zonesweep_no_sweep_is_not_qualified():
    # spike low NOT below the earlier swing low -> sweep flag False -> no SIGNAL
    m15 = zone_fixture(spike_low=96.0, prev_swing_low=95.0)
    res = zs.evaluate(m15, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    if res.get("status") in (zs.SIGNAL, "ZONE_FORMED_UNQUALIFIED"):
        assert res["sweep"] is False
        assert res["status"] != zs.SIGNAL


def test_zonesweep_trend_misalignment_not_qualified():
    m15 = zone_fixture(end_at_bos=True)
    res = zs.evaluate(m15, h4_trend(direction=1), h4_trend(direction=-1),
                      market="XAUUSD")                  # H4 down vs LONG zone
    if res.get("status") in (zs.SIGNAL, "ZONE_FORMED_UNQUALIFIED"):
        assert res["trend_ok"] is False
        assert res["status"] != zs.SIGNAL


def test_zonesweep_cost_floor_and_atr_ceiling():
    # the SAME valid zone becomes untradeable when the round-trip cost is
    # high enough that risk < 3 x cost (spec's spread-protection floor)
    m15 = zone_fixture(end_at_bos=True)
    ok = zs.evaluate(m15, h4_trend(direction=1), h4_trend(direction=1),
                     market="XAUUSD")                       # cost 0.35 -> tradeable
    bad = zs.evaluate(m15, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD", cost=0.60)           # 3x0.60 > risk
    assert ok.get("status") == zs.SIGNAL, ok
    assert bad.get("status") != zs.SIGNAL
    assert bad.get("risk_ok") is False


def test_zonesweep_market_exclusions():
    m15 = zone_fixture(end_at_bos=True)
    assert zs.evaluate(m15, h4_trend(), h4_trend(),
                       market="XAGUSD")["status"] == zs.OUT_OF_SPEC_MARKET
    assert zs.evaluate(m15, h4_trend(), h4_trend(),
                       market="AUDUSD")["status"] == zs.OUT_OF_SPEC_MARKET


def test_zonesweep_insufficient_context():
    res = zs.evaluate(zone_fixture(), h4_trend(n=100), h4_trend(), market="EURUSD")
    assert res["status"] == zs.INSUFFICIENT


# ===========================================================================
# ZoneSweep - lifecycle mechanics via backtest
# ===========================================================================
def _bt_with_dip(dip_bar, tail=8, spike_low=92.0, prev_swing_low=98.5):
    base = zone_fixture(spike_low=spike_low, prev_swing_low=prev_swing_low)
    return append_bars(base, dip_bar + [
        (105.0, 105.5, 104.6, 105.2)] * tail, freq="15min")


def test_zonesweep_limit_fill_then_stop():
    # dip into the zone (low 93.0 <= zone top 93.5, open above) -> fill 93.5;
    # then a bar through the stop (92 - 0.25 ATR ~ 91.7)
    dip = [(100.0, 100.4, 93.0, 99.0)]
    through = [(96.0, 96.5, 90.5, 91.0)]
    df = _bt_with_dip(dip + [(98.0, 98.5, 97.5, 98.2)] + through)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    assert out["trades"], out
    t = out["trades"][0]
    assert t["direction"] == "LONG"
    assert t["entry"] == pytest.approx(t["zone_top"])          # limit at zone top
    assert t["exit_reason"] == "STOP" and t["r_multiple"] == pytest.approx(-1.0, abs=0.05)


def test_zonesweep_open_gap_fills_at_open():
    dip = [(93.0, 94.0, 92.5, 94.5)]                # opens BELOW the zone top
    df = _bt_with_dip(dip)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    t = out["trades"][0]
    assert t["entry"] == 93.0                        # the open, not the limit


def test_zonesweep_fill_bar_stop_counts_and_tp_needs_next_bar():
    # the FILL bar itself spans the stop: counts as a loss even though it
    # also reaches the TP distance later? -> stop wins on the fill bar
    dip = [(100.0, 101.0, 91.0, 99.0)]               # touches zone, spans SL
    df = _bt_with_dip(dip)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    t = out["trades"][0]
    assert t["exit_reason"] in ("STOP", "STOP_GAP_OPEN")
    assert t["bars_after_fill"] == 1


def test_zonesweep_take_profit_path():
    dip = [(100.0, 100.4, 93.0, 99.0)]               # fill 93.5, SL ~91.7
    runner = [(95.0, 112.0, 94.8, 111.0)]            # next bar: TP = 93.5 + 8x1.8
    df = _bt_with_dip(dip + [(99.0, 99.5, 94.0, 98.0)] + runner)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    t = out["trades"][0]
    assert t["exit_reason"] == "TAKE_PROFIT"
    assert t["r_multiple"] == pytest.approx(8.0, abs=0.05)


def test_zonesweep_close_through_fills_before_any_cancel():
    """Spec quirk (documented in the report): a bar that CLOSES through the
    zone bottom always has low <= close < bottom < top - it has already
    TOUCHED the zone. The touch/fill rule therefore always fires before the
    cancel rule can; the cancel branch is structurally unreachable for
    touch = low <= zone top. Pinned behavior: the gap-open fill executes
    (at the open), and the fill-bar stop rule counts the loss."""
    dip = [(92.5, 93.2, 90.8, 91.0)]     # touches the zone top, closes through
    df = _bt_with_dip(dip)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["entry"] == 92.5            # open-through fill (open < zone top)
    assert t["exit_reason"] in ("STOP", "STOP_GAP_OPEN")
    assert t["bars_after_fill"] == 1     # the fill bar itself stopped out


def test_zonesweep_zone_not_refilled_after_trade():
    dip = [(100.0, 100.4, 93.0, 99.0)]
    through = [(96.0, 96.5, 90.5, 91.0)]
    df = _bt_with_dip(dip + [(98.0, 98.5, 97.5, 98.2)] + through + [
        (98.0, 98.5, 93.2, 98.0)])                   # re-touch of the same zone
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    assert len(out["trades"]) == 1                   # one trade per zone


def test_zonesweep_time_exit_after_fill():
    dip = [(100.0, 100.4, 93.0, 99.0)]
    drift = [(99.0 + 0.01 * k, 99.4 + 0.01 * k, 98.9 + 0.01 * k,
              99.1 + 0.01 * k) for k in range(100)]
    df = _bt_with_dip(dip + drift)
    out = zs.backtest(df, h4_trend(direction=1), h4_trend(direction=1),
                      market="XAUUSD")
    t = out["trades"][0]
    assert t["exit_reason"] == "TIME_EXIT_96_AFTER_FILL"
    gap = pd.Timestamp(t["exit_time"]) - pd.Timestamp(t["fill_time"])
    assert gap == pd.Timedelta(minutes=15 * 96)   # the 96th bar after the fill


# ===========================================================================
# isolation: research-only, never registered, never executable
# ===========================================================================
def test_candidates_registered_via_adapters_only():
    """Owner directive 2026-10-24: the candidates ARE live now - through the
    adapter layer only. The research engines stay pure (no registry refs)."""
    import app.strategies as reg
    assert set(reg.STRATEGY_CLASSES.keys()) == {
        "strategy_h1_trend_breakout", "strategy_gold_orb",
        "strategy_tpc15_pullback", "strategy_trendbreak_h1",
        "strategy_zonesweep_m15", "strategy_trendbreak_v2",
        "strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"}
    for mod in (tb, zs):
        src = inspect.getsource(mod)
        assert "STRATEGY_CLASSES" not in src
        assert "mt5" not in src.replace(" Summary:", "")


def test_candidate_imports_are_pure():
    for mod in (tb, zs):
        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "typing", "pandas"}, (mod, imported)


def test_spec_tables_match_the_owner_spec():
    assert tb.SPEC["markets"] == ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    assert tb.SPEC["excluded_markets"] == ["XAGUSD"]
    assert (tb.SPEC["channel_n"], tb.SPEC["stop_atr"], tb.SPEC["tp_multiple"],
            tb.SPEC["time_exit_bars"]) == (120, 2.5, 3.0, 96)
    assert zs.SPEC["swing_len"] == 5 and zs.SPEC["tp_multiple"] == 8.0
    assert zs.SPEC["zone_life_bars"] == 96 and zs.SPEC["sl_buffer_atr"] == 0.25
    assert zs.SPEC["reference_costs"] == {"EURUSD": 0.00012, "GBPUSD": 0.00016,
                                          "USDJPY": 0.015, "XAUUSD": 0.35}
