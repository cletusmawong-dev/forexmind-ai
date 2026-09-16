"""HTF-alignment confidence tests (user directive 2026-09-16).

Strategy 2's 0-100 confidence must be based on higher-timeframe alignment:
80 pts from agreement of the frames above the signal timeframe (measured by
this strategy's own fast/slow EMA relationship), 20 pts local crossover
quality. Per-frame trends land on the candidate's mtf field (shown as MTF
chips) and the signal's checks state the alignment in words.
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


def _trend_df(up=True):
    n = 60
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


def test_all_htfs_aligned_confidence_100():
    cand = _candidate({"1H": _trend_df(True), "4H": _trend_df(True),
                       "1D": _trend_df(True)})
    assert cand is not None and cand.direction == "BUY"
    assert cand.mtf == {"1H": 1, "4H": 1, "1D": 1}
    assert cand.score_components["HTF alignment"] == 80.0
    assert cand.score == int(round(sum(cand.score_components.values()))) >= 80
    detail = next(c["detail"] for c in cand.checks if "Higher-timeframe" in c["label"])
    assert "3 of 3 higher timeframes agree" in detail


def test_all_htfs_against_confidence_floor():
    cand = _candidate({"1H": _trend_df(False), "4H": _trend_df(False),
                       "1D": _trend_df(False)})
    assert cand.mtf == {"1H": -1, "4H": -1, "1D": -1}
    assert cand.score_components["HTF alignment"] == 0.0
    assert cand.score == int(round(sum(cand.score_components.values())))
    assert cand.score < 30  # alignment against -> confidence low
    detail = next(c["detail"] for c in cand.checks if "Higher-timeframe" in c["label"])
    assert "0 of 3 higher timeframes agree" in detail


def test_mixed_htfs_partial_confidence_and_chips():
    cand = _candidate({"1H": _trend_df(True), "4H": _trend_df(True),
                       "1D": _trend_df(False)})
    assert cand.mtf == {"1H": 1, "4H": 1, "1D": -1}
    assert cand.score_components["HTF alignment"] == pytest.approx(53.3)
    detail = next(c["detail"] for c in cand.checks if "Higher-timeframe" in c["label"])
    assert "2 of 3 higher timeframes agree" in detail
    assert "1D bearish" in detail


def test_missing_frames_honest():
    cand = _candidate({"1H": _trend_df(True)})  # 4H / 1D unavailable
    assert cand.mtf == {"1H": 1}
    assert cand.score_components["HTF alignment"] == 80.0
    detail = next(c["detail"] for c in cand.checks if "Higher-timeframe" in c["label"])
    assert "1 of 1 higher timeframes agree" in detail

    cand2 = _candidate({})  # nothing available at all
    assert cand2.mtf == {}
    assert cand2.score_components["HTF alignment"] == 0.0
    detail2 = next(c["detail"] for c in cand2.checks if "Higher-timeframe" in c["label"])
    assert "unavailable" in detail2
