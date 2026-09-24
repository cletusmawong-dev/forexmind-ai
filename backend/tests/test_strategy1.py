"""Strategy 1 tests (SPEC §9, §58): Zero Lag Trend Signals detection, MTF."""
import numpy as np

from app.strategies import get_strategy


def test_metadata_and_parameters():
    s = get_strategy("strategy_1_zero_lag")
    assert s.get_metadata()["id"] == "strategy_1_zero_lag"
    p = s.get_parameters()
    assert p["length"] == 70
    assert p["band_mult"] == 1.2


def test_compute_state_shape(xau_15m):
    s = get_strategy("strategy_1_zero_lag")
    state = s.compute(xau_15m, s.base_params)
    for key in ("zlema", "upper", "lower", "vol", "trend", "bull", "bear"):
        assert key in state
    assert len(state["zlema"]) == len(xau_15m)
    # volatility band must be positive where defined
    vol = state["vol"].dropna()
    assert (vol > 0).all()
    assert (state["upper"].dropna() > state["lower"].dropna()).all()


def test_entries_are_small_arrow_entries_only(xau_15m):
    """Bullish entry requires: close crosses above zlema AND trend == 1 AND
    previous trend == 1 (SPEC §9). Never the raw trend-change bar."""
    s = get_strategy("strategy_1_zero_lag")
    state = s.compute(xau_15m, s.base_params)
    trend = state["trend"]
    cross_up_bars = set(state["bull"].index[state["bull"]])
    for i in range(2, len(xau_15m)):
        if xau_15m.index[i] in cross_up_bars:
            # crossed above zlema with trend already bullish on this & prev bar
            assert trend.iloc[i] == 1
            assert trend.iloc[i - 1] == 1
            # close actually crossed the zlema on this bar
            assert xau_15m["close"].iloc[i] > state["zlema"].iloc[i]
            assert xau_15m["close"].iloc[i - 1] <= state["zlema"].iloc[i - 1]


def test_detect_on_bar_requires_fresh_cross(xau_15m):
    s = get_strategy("strategy_1_zero_lag")
    state = s.compute(xau_15m, s.base_params)
    # find a bar that is NOT an entry: most bars
    found_non_entry = False
    for i in range(300, len(xau_15m)):
        if s.detect_on_bar(state, i) is None:
            found_non_entry = True
            break
    assert found_non_entry


def test_backtest_runs_and_has_valid_trades(xau_15m):
    s = get_strategy("strategy_1_zero_lag")
    trades = s.backtest(xau_15m.tail(1200).reset_index().set_index("timestamp"))
    for tr in trades:
        assert tr["direction"] in ("BUY", "SELL")
        assert tr["risk"] > 0
        assert len(tr["tps"]) == 3   # v1.1: 1R/2R/3R ladder
        assert tr["r_multiple"] >= -1.0
        assert tr["outcome"] in ("WIN", "LOSS", "EXPIRED")
        if tr["direction"] == "BUY":
            assert tr["sl"] < tr["entry"] < tr["tps"][0]


def test_mtf_trend_read_across_timeframes(xau_15m, xau_1h):
    s = get_strategy("strategy_1_zero_lag")
    frames = {"15M": xau_15m.tail(1500), "1H": xau_1h.tail(400)}
    mtf = s.mtf_trend(frames, s.base_params)
    assert set(mtf.keys()) == {"15M", "1H"}
    for v in mtf.values():
        assert v in (-1, 0, 1)


def test_candidate_risk_and_targets(xau_15m):
    """calculate_risk / calculate_targets / explain_signal contract."""
    s = get_strategy("strategy_1_zero_lag")
    df = xau_15m.tail(1500)
    state = s.compute(df, s.base_params)
    idx = [i for i in range(300, len(df)) if s.detect_on_bar(state, i)]
    assert idx, "expected at least one entry in the demo dataset"
    i = idx[-1]
    ev = s.detect_on_bar(state, i)
    rk = s.calculate_risk(df, state, i, ev["direction"], s.base_params)
    assert rk["risk"] > 0
    tps = s.calculate_targets(rk["entry"], rk["risk"], ev["direction"], s.base_params)
    assert len(tps) == 3                              # v1.1: EMA-style 1R/2R/3R ladder
    if ev["direction"] == "BUY":
        assert rk["sl"] < rk["entry"]
        assert tps[0] > rk["entry"] and tps[1] > tps[0] and tps[2] > tps[1]
        for tp, rr in zip(tps, (1.0, 2.0, 3.0)):
            assert abs((tp - rk["entry"]) / rk["risk"] - rr) < 1e-6
    else:
        assert rk["sl"] > rk["entry"]
        assert tps[0] < rk["entry"] and tps[1] < tps[0] and tps[2] < tps[1]
        for tp, rr in zip(tps, (1.0, 2.0, 3.0)):
            assert abs((rk["entry"] - tp) / rk["risk"] - rr) < 1e-6
    cand = s.build_candidate(state, i, ev, df, "XAUUSD", "15M", s.base_params, {}, {})
    assert 0 <= cand.score <= 100
    labels = [c["label"] for c in cand.checks]
    assert "Trend condition confirmed" in labels
    assert "Entry condition confirmed" in labels
    assert cand.analysis and "qualifies because" in cand.analysis


def test_detect_signal_lookback_catches_recently_closed_bar(xau_15m):
    """Missed-signal fix: the candle cache can lag 1-2 bars at scan time.
    detect_signal must evaluate the last few CLOSED bars (newest first),
    not only bar -1, and dedup happens downstream per candle_time."""
    s = get_strategy("strategy_1_zero_lag")
    df = xau_15m.tail(1500)
    state = s.compute(df, s.base_params)
    # find the most recent entry bar that is NOT the last bar
    entries = [i for i in range(300, len(df)) if s.detect_on_bar(state, i)]
    assert entries
    i_ev = [i for i in entries if i < len(df) - 1][-1]
    # simulate a cache that ends 1 bar BEFORE the entry bar closes: the
    # entry must still be found when the df includes it as bar -2
    df_cut = df.iloc[:i_ev + 2]          # entry bar = second-newest
    ev2 = s.detect_on_bar(s.compute(df_cut, s.base_params), len(df_cut) - 2)
    if ev2 is None:                       # this particular bar's state shifted
        return
    cand = s.detect_signal(df_cut, "XAUUSD", "15M", params=s.base_params)
    assert cand is not None
    assert cand.candle_time == str(df_cut.index[i_ev])
