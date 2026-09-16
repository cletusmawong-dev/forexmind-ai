"""HTF-alignment + 100 EMA confidence tests (user directives 2026-09-16).

Strategy 2's 0-100 confidence: 60 pts from higher-timeframe alignment
(fast/slow EMA relationship per HTF frame), 20 pts from the higher-
timeframe 100 EMA assist (price above assists BUYs, below assists SELLs;
frames with <130 bars are honestly excluded), 20 pts local crossover
quality. Entries/SL/TPs are never affected.
"""
import numpy as np
import pandas as pd
import pytest


def _base_df():
    """15M frame whose LAST closed bar is a bullish 9/21 cross (>=300 bars)."""
    close = np.concatenate([4000 - np.arange(340) * 0.5,
                            3830 + np.arange(60) * 0.5])
    idx = pd.date_range("2026-08-01", periods=400, freq="15min")
    df = pd.DataFrame({"open": close, "high": close + 0.4,
                       "low": close - 0.4, "close": close,
                       "volume": 1.0}, index=idx)
    from app.core.indicators import crossover, ema
    cross_idx = int(np.argmax(crossover(ema(df["close"], 9), ema(df["close"], 21)).values))
    assert cross_idx >= 300
    return df.iloc[:cross_idx + 1]


def _trend_df(up=True, n=150):
    """Tall frame so the 100 EMA is valid; trend sets close vs EMAs."""
    close = (3000 + np.arange(n) * 1.0) if up else (3000 - np.arange(n) * 1.0)
    idx = pd.date_range("2026-08-01", periods=n, freq="1h")
    return pd.DataFrame({"open": close, "high": close + 0.4,
                         "low": close - 0.4, "close": close,
                         "volume": 1.0}, index=idx)


def _candidate(higher):
    from app.learning.versions import active_params
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    df = _base_df()
    strat = EmaAtrStrategy()
    state = strat.compute(df.dropna(), active_params("strategy_2_ema_atr"))
    event = {"direction": "BUY", "i": len(df) - 1}
    return strat.build_candidate(
        state, len(df) - 1, event, df, "XAUUSD", "15M",
        params=active_params("strategy_2_ema_atr"),
        higher_frames=higher, score_context={"session": "London"})


def _htf_detail(cand):
    return next(c["detail"] for c in cand.checks if "Higher-timeframe" in c["label"])


def test_full_alignment_plus_100ema():
    cand = _candidate({"1H": _trend_df(True), "4H": _trend_df(True),
                       "1D": _trend_df(True)})
    assert cand.mtf == {"1H": 1, "4H": 1, "1D": 1}
    comps = cand.score_components
    assert comps["HTF alignment"] == 60.0
    assert comps["HTF 100 EMA"] == 20.0      # price above 100 EMA on all frames
    assert cand.score == int(round(sum(comps.values())))
    assert cand.score >= 80  # 60 alignment + 20 assist before crossover pts
    d = _htf_detail(cand)
    assert "3 of 3 higher timeframes agree" in d
    assert "3 of 3 assist the buy" in d
    assert "above 100 EMA" in d


def test_all_against_floor():
    cand = _candidate({"1H": _trend_df(False), "4H": _trend_df(False),
                       "1D": _trend_df(False)})
    assert cand.mtf == {"1H": -1, "4H": -1, "1D": -1}
    comps = cand.score_components
    assert comps["HTF alignment"] == 0.0
    assert comps["HTF 100 EMA"] == 0.0       # price below 100 EMA fights a BUY
    assert cand.score == int(round(sum(comps.values())))
    assert cand.score < 30
    d = _htf_detail(cand)
    assert "0 of 3 higher timeframes agree" in d
    assert "0 of 3 assist the buy" in d


def test_mixed_partial():
    cand = _candidate({"1H": _trend_df(True), "4H": _trend_df(True),
                       "1D": _trend_df(False)})
    comps = cand.score_components
    assert comps["HTF alignment"] == pytest.approx(40.0)   # 2/3 x 60
    assert comps["HTF 100 EMA"] == pytest.approx(13.3)     # 2/3 x 20
    d = _htf_detail(cand)
    assert "2 of 3 higher timeframes agree" in d
    assert "1D bearish" in d


def test_short_frames_excluded_honestly():
    """60-bar frames: valid for the 9/21 trend, NOT for the 100 EMA."""
    cand = _candidate({"1H": _trend_df(True, n=60), "4H": _trend_df(True, n=60),
                       "1D": _trend_df(True, n=60)})
    assert cand.mtf == {"1H": 1, "4H": 1, "1D": 1}
    comps = cand.score_components
    assert comps["HTF alignment"] == 60.0
    assert comps["HTF 100 EMA"] == 0.0
    d = _htf_detail(cand)
    assert "insufficient higher-timeframe history" in d


def test_no_frames_at_all():
    cand = _candidate({})
    assert cand.mtf == {}
    comps = cand.score_components
    assert comps["HTF alignment"] == 0.0 and comps["HTF 100 EMA"] == 0.0
    assert "unavailable" in _htf_detail(cand)


def test_entries_untouched_by_scoring():
    """Scoring must never move entry/SL/TP: same frames, same levels."""
    higher = {"1H": _trend_df(True), "4H": _trend_df(True), "1D": _trend_df(True)}
    cand = _candidate(higher)
    levels = (cand.entry, cand.sl, tuple(cand.tps))
    cand2 = _candidate({})
    assert (cand2.entry, cand2.sl, tuple(cand2.tps)) == levels
