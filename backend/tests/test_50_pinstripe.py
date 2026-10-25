"""PINSTRIPE V6 (owner locked rules, 2025) - both strategies.

Hybrid_CRT_TBS (secondary) + Fade_CRT_Extension (primary): CRT definition
(0.65 / 0.85 / 20-avg), hybrid validity rule (5-candle sweep or body 0.75),
1H sweep -> Model #1 (0.58) -> close-beyond entry, fade extension (0.15) ->
close-back-inside -> confirmation (0.55) -> close-beyond entry, SL geometry,
TP1 = CRT midpoint / TP2 = opposite side, exact 50% partial + breakeven
management, independent same-pair trading, ticket TP = TP2.
"""
import pandas as pd
import pytest

from app.strategies.research_only import fade_crt_extension as fade
from app.strategies.research_only import hybrid_crt_tbs as hy
from app.strategies.research_only import pinstripe_common as pc


# ===========================================================================
# fixtures (verified mechanics)
# ===========================================================================
IDX4 = pd.date_range("2026-09-01", periods=40, freq="4h", tz="UTC")
# bar 39 = 2026-09-07 12:00, closes 16:00


def _h4(crt_row=None):
    """crt_row = (open, high, low, close) of the LAST H4 bar."""
    rows = []
    for i in range(40):
        row = (100.0, 100.4, 99.6, 100.0)               # range 0.8 flat doji
        if i == 39 and crt_row:
            row = crt_row
        rows.append(row)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=IDX4)


BEARISH_CRT = (101.0, 101.2, 98.6, 98.8)     # body 2.2/2.6 = 0.846, range 2.6
IDX1 = pd.date_range("2026-09-07 16:00", periods=4, freq="1h", tz="UTC")
IDX1_5 = pd.date_range("2026-09-07 16:00", periods=5, freq="1h", tz="UTC")


def _hist():
    hist = pd.DataFrame({"open": [100.0] * 60, "high": [100.3] * 60,
                         "low": [99.7] * 60, "close": [100.0] * 60},
                        index=pd.date_range(end=IDX4[39] - pd.Timedelta("5h"),
                                            periods=60, freq="1h", tz="UTC"))
    hist.iloc[55, hist.columns.get_loc("high")] = 100.9    # swing high
    hist.iloc[54, hist.columns.get_loc("high")] = 100.5
    hist.iloc[56, hist.columns.get_loc("high")] = 100.5
    hist.iloc[35, hist.columns.get_loc("low")] = 99.2      # swing low
    hist.iloc[34, hist.columns.get_loc("low")] = 99.5
    hist.iloc[36, hist.columns.get_loc("low")] = 99.5
    return hist


# sweep 100.9 swing high -> M1 bearish 0.75 -> trigger close 99.7 < M1 low
HYBRID_WIN = [(100.6, 101.0, 100.2, 100.4),
              (100.4, 100.5, 100.1, 100.2),
              (100.2, 100.25, 99.85, 99.9),
              (99.9, 99.95, 99.6, 99.7)]

# extension below (low < 98.6 - 0.39) -> close back inside -> bullish conf
# (body 0.762) -> trigger close above conf high
FADE_WIN = [(100.9, 101.0, 97.0, 97.2),
            (97.2, 97.6, 96.9, 97.4),
            (97.4, 99.0, 97.2, 98.9),
            (98.9, 99.85, 98.8, 99.7),
            (99.7, 100.2, 99.6, 100.1)]


def _h1(win, idx):
    return pd.concat([_hist(), pd.DataFrame(
        win, columns=["open", "high", "low", "close"], index=idx)])


# ===========================================================================
# locked values live in the SPECs
# ===========================================================================
def test_locked_percentages_untouched():
    assert pc.COMMON["crt_body_min"] == 0.65
    assert pc.COMMON["crt_range_mult"] == 0.85
    assert pc.COMMON["strong_body"] == 0.75
    assert pc.COMMON["validity_lookback"] == 5
    assert pc.COMMON["m1_body_min"] == 0.58
    assert pc.COMMON["conf_body_min"] == 0.55
    assert pc.COMMON["extension_frac"] == 0.15
    assert pc.COMMON["partial_fraction"] == 0.5
    assert pc.COMMON["breakeven_after_partial"] is True
    assert hy.SPEC["m1_body_min"] == 0.58 and hy.SPEC["role"] == "secondary"
    assert fade.SPEC["conf_body_min"] == 0.55 and fade.SPEC["role"] == "primary"
    assert fade.SPEC["extension_frac"] == 0.15


# ===========================================================================
# CRT definition
# ===========================================================================
def test_crt_definition_math():
    tab = pc.crt_table(_h4(BEARISH_CRT))
    assert bool(tab["is_crt"].iloc[38]) is False          # flat bars: body 0
    assert bool(tab["is_crt"].iloc[39]) is True
    assert tab["direction"].iloc[39] == -1
    assert tab["body"].iloc[39] == pytest.approx(2.2 / 2.6)
    # body ratio exactly at 0.65 with a big enough range IS a CRT
    h4 = _h4(None)
    h4.iloc[38] = [100.0, 100.65, 99.65, 100.65]          # body 0.65/1.0
    tab2 = pc.crt_table(h4)
    assert tab2["body"].iloc[38] == pytest.approx(0.65)
    assert bool(tab2["is_crt"].iloc[38]) is True          # >= holds
    # doji at the same range: body < 0.65 -> NOT a CRT
    h4.iloc[38] = [100.0, 100.65, 99.65, 100.2]
    tab3 = pc.crt_table(h4)
    assert bool(tab3["is_crt"].iloc[38]) is False
    # zero-range bar: no body, never a CRT
    h4.iloc[37] = [100.0, 100.0, 100.0, 100.0]
    tab4 = pc.crt_table(h4)
    assert bool(tab4["is_crt"].iloc[37]) is False


def test_hybrid_validity_rule():
    # bearish CRT sweeping the prior-5 high: prior highs 100.4 < 101.2 -> valid
    crt = pc.find_latest_crt(pc._frame(_h4(BEARISH_CRT), "h4"), require_validity=True)
    assert crt is not None and crt["direction"] == -1
    # body 0.69 (< 0.75) with high 100.55 > prior-5 high 100.4 -> sweep-valid
    weak = (100.5, 100.55, 99.55, 99.81)       # (o,h,l,c) body 0.69/1.0
    crt = pc.find_latest_crt(pc._frame(_h4(weak), "h4"), require_validity=True)
    assert crt is not None
    # body 0.80 (>= 0.75 strong) without any sweep -> valid via the strong body
    nosweep = (100.3, 100.35, 99.35, 99.5)     # body 0.8/1.0, high < prior high
    crt = pc.find_latest_crt(pc._frame(_h4(nosweep), "h4"), require_validity=True)
    assert crt is not None
    # body 0.625 < 0.65 -> not a CRT at all (definition fails)
    mid = (100.2, 100.38, 99.98, 99.95)
    assert pc.find_latest_crt(pc._frame(_h4(mid), "h4"), require_validity=True) is None
    # body 0.70 (>= 0.65, < 0.75) with NO prior-5 sweep (high 100.3 < 100.4):
    # definition passes, hybrid validity FAILS for Strategy 1
    inside = (100.0, 100.3, 99.3, 99.3)        # bearish, body 0.7/1.0
    h4 = _h4(inside)
    assert pc.find_latest_crt(pc._frame(h4, "h4"), require_validity=False) is not None
    assert pc.find_latest_crt(pc._frame(h4, "h4"), require_validity=True) is None


# ===========================================================================
# Strategy 1: Hybrid_CRT_TBS
# ===========================================================================
def test_hybrid_full_sequence_fires_on_the_latest_close():
    res = hy.evaluate(_h1(HYBRID_WIN, IDX1), _h4(BEARISH_CRT), market="EURUSD")
    assert res["status"] == hy.SIGNAL, res
    assert res["direction"] == "SELL"                    # rides the bearish CRT
    assert res["entry"] == pytest.approx(99.7)           # ON the trigger close
    assert res["sl"] == pytest.approx(101.0)             # max(M1 high 100.25, sweep 101.0)
    assert res["tp1"] == pytest.approx(99.9)             # CRT midpoint
    assert res["tp2"] == pytest.approx(98.6)             # CRT low
    assert res["rr_primary"] == pytest.approx((99.7 - 98.6) / 1.3, abs=1e-3)
    assert res["geometry_valid"] is True
    assert res["management"]["partial_fraction"] == 0.5


def test_hybrid_stale_trigger_no_signal():
    """Entry is on the candle close - a trigger on an OLDER bar never fires."""
    h1 = _h1(HYBRID_WIN + [(99.7, 99.8, 99.5, 99.65)],
             pd.date_range("2026-09-07 16:00", periods=5, freq="1h", tz="UTC"))
    res = hy.evaluate(h1, _h4(BEARISH_CRT), market="EURUSD")
    assert res["status"] == hy.NO_SIGNAL
    assert res["checks"][-1]["detail"].startswith("state DONE")


def test_hybrid_no_valid_crt_no_signal():
    res = hy.evaluate(_h1(HYBRID_WIN, IDX1), _h4(None), market="EURUSD")
    assert res["status"] == hy.NO_SIGNAL
    assert res["checks"][0]["ok"] is False


def test_hybrid_requires_model1_direction_match():
    """M1 candle must point the SAME way as the CRT - a bullish M1 blocks."""
    win = [(100.6, 101.0, 100.2, 100.4),
           (100.4, 100.5, 100.1, 100.2),
           (100.2, 100.55, 100.15, 100.5),              # bullish candle, body 0.8
           (100.5, 100.9, 100.45, 100.85)]              # close above its high
    res = hy.evaluate(_h1(win, IDX1), _h4(BEARISH_CRT), market="EURUSD")
    assert res["status"] == hy.NO_SIGNAL


def test_hybrid_m1_body_threshold():
    """A 0.57-body candle is NOT a Model #1 (0.58 locked); 0.58 is."""
    sweep = (100.6, 101.0, 100.2, 100.4)
    pull = (100.4, 100.5, 100.1, 100.2)
    doji = (99.9, 99.95, 99.85, 99.9)                    # body 0 -> never M1
    idx5 = pd.date_range("2026-09-07 16:00", periods=5, freq="1h", tz="UTC")
    # 0.57: body 0.285/0.5 -> rejected as M1; the later close below its low
    # is irrelevant (no M1 -> no trigger)
    weak = (100.2, 100.25, 99.75, 99.915)
    trig = (99.915, 99.95, 99.7, 99.7)                   # closes below the weak low
    res = hy.evaluate(_h1([sweep, pull, weak, doji, trig], idx5),
                      _h4(BEARISH_CRT), market="EURUSD")
    assert res["status"] == hy.NO_SIGNAL
    # 0.58 exactly: body 0.29/0.5 -> the Model #1 -> the trigger fires
    m1 = (100.2, 100.25, 99.75, 99.91)
    res = hy.evaluate(_h1([sweep, pull, m1, doji, trig], idx5),
                      _h4(BEARISH_CRT), market="EURUSD")
    assert res["status"] == hy.SIGNAL, res


def test_hybrid_buy_mirror():
    """Bullish CRT sweeping the prior-5 LOW + buy-side sequence."""
    bull = (98.9, 101.3, 98.7, 101.2)                    # body 2.3/2.6 = 0.885, low 98.7 < prior lows 99.6
    win = [(99.4, 99.5, 99.0, 99.6),                     # sweep of the 99.2 swing low? 99.0 < 99.2, close back above 99.6
           (99.6, 99.7, 99.3, 99.4),
           (99.4, 99.85, 99.35, 99.8),                   # M1 bullish body 0.45/0.5 = 0.9
           (99.8, 100.3, 99.75, 100.25)]                 # close > M1 high
    res = hy.evaluate(_h1(win, IDX1), _h4(bull), market="GBPUSD")
    assert res["status"] == hy.SIGNAL, res
    assert res["direction"] == "BUY"
    assert res["sl"] == pytest.approx(min(99.35, 99.0))
    assert res["tp2"] == pytest.approx(101.3)            # CRT high


def test_hybrid_insufficient():
    assert hy.evaluate(pd.DataFrame(
        {"open": [1] * 10, "high": [1] * 10, "low": [1] * 10, "close": [1] * 10},
        index=pd.date_range("2026-09-01", periods=10, freq="1h", tz="UTC")),
        _h4(BEARISH_CRT), market="EURUSD")["status"] == hy.INSUFFICIENT


def test_hybrid_backtest_tp1_then_tp2_exact_math():
    """50% off at TP1 + SL to breakeven, remainder exits TP2 - exact R math."""
    # SELL entry 99.7, SL 101.0 (risk 1.3), TP1 99.9, TP2 98.6
    post = [(99.7, 99.8, 99.55, 99.6),                   # hi < tp1 99.9 (no partial yet)
            (99.6, 100.05, 99.4, 99.5),                  # hi 100.05 >= 99.9 -> TP1; lo 99.4 < BE? BE not set yet
            (99.5, 99.6, 98.4, 98.5)]                    # lo 98.4 <= tp2 98.6 -> TP2 for the remainder
    h1 = _h1(HYBRID_WIN + post, pd.date_range("2026-09-07 16:00", periods=7,
                                              freq="1h", tz="UTC"))
    bt = hy.backtest(h1, _h4(BEARISH_CRT), "EURUSD")
    assert bt["status"] == "OK" and len(bt["trades"]) == 1
    t = bt["trades"][0]
    r1 = (99.9 - 99.7) / 1.3 * -1                        # SELL to a HIGHER tp1 = -0.1538
    r2 = (98.6 - 99.7) / 1.3 * -1                        # SELL profit
    assert t["exit_reason"] == "TP2"
    assert t["r_tp1"] == pytest.approx(round(r1, 4))
    assert t["r_remainder"] == pytest.approx(round(r2, 4))
    assert t["r_multiple"] == pytest.approx(round(0.5 * r1 + 0.5 * r2, 4))
    assert t["partial_50_at_tp1"] is True
    assert t["breakeven_after_partial"] is True


def test_hybrid_backtest_stop_before_tp1_is_full_loss():
    post = [(99.7, 101.3, 99.6, 101.1)]                  # hi 101.3 >= SL 101.0
    h1 = _h1(HYBRID_WIN + post, pd.date_range("2026-09-07 16:00", periods=5,
                                              freq="1h", tz="UTC"))
    bt = hy.backtest(h1, _h4(BEARISH_CRT), "EURUSD")
    t = bt["trades"][0]
    assert t["exit_reason"] == "STOP_LOSS"
    assert t["r_multiple"] == pytest.approx(-1.0)


def test_hybrid_backtest_be_stop_after_partial():
    """After TP1 the SL sits at entry - a return to entry closes the
    remainder at breakeven (r = 0.5*TP1 + 0.5*0)."""
    post = [(99.7, 99.8, 99.55, 99.6),
            (99.6, 100.05, 99.4, 99.5),                  # TP1 partial, SL -> 99.7
            (99.5, 99.75, 99.65, 99.7)]                  # lo 99.65 <= BE 99.7 -> remainder at BE
    h1 = _h1(HYBRID_WIN + post, pd.date_range("2026-09-07 16:00", periods=7,
                                              freq="1h", tz="UTC"))
    bt = hy.backtest(h1, _h4(BEARISH_CRT), "EURUSD")
    t = bt["trades"][0]
    assert t["exit_reason"] == "BE_STOP_REMAINDER"
    r1 = (99.9 - 99.7) / 1.3 * -1
    assert t["r_multiple"] == pytest.approx(round(0.5 * r1 + 0.5 * 0.0, 4))


# ===========================================================================
# Strategy 2: Fade_CRT_Extension
# ===========================================================================
def test_fade_full_sequence_fires_on_the_latest_close():
    res = fade.evaluate(_h1(FADE_WIN, IDX1_5), _h4(BEARISH_CRT), market="XAUUSD")
    assert res["status"] == fade.SIGNAL, res
    assert res["direction"] == "BUY"                     # fade of the bearish CRT
    assert res["entry"] == pytest.approx(100.1)
    assert res["sl"] == pytest.approx(96.9)              # furthest extension low
    assert res["tp1"] == pytest.approx(99.9)
    assert res["tp2"] == pytest.approx(101.2)            # CRT high
    assert res["geometry_valid"] is True


def test_fade_extension_threshold_is_exact():
    """A low at exactly CRT_low - 0.15*range does NOT extend (strict >)."""
    thr = 98.6 - 0.15 * 2.6                              # 98.21
    win = [(100.9, 101.0, thr, 98.9),                    # low == thr: no extension
           (98.9, 99.0, 98.8, 98.95)]
    res = fade.evaluate(_h1(win, pd.date_range("2026-09-07 16:00", periods=2,
                                               freq="1h", tz="UTC")),
                        _h4(BEARISH_CRT), market="XAUUSD")
    assert res["status"] == fade.NO_SIGNAL
    assert res["checks"][-1]["detail"].startswith("state SEEK_EXT")


def test_fade_reversal_requires_close_inside():
    """A close below the CRT low is NOT 'back inside the range'."""
    win = [(100.9, 101.0, 97.0, 97.2),
           (97.2, 97.6, 96.9, 97.4),
           (97.4, 98.4, 96.8, 98.2),                     # closes BELOW crt_low 98.6
           (98.2, 99.8, 98.1, 99.7),                     # conf-eligible body
           (99.7, 100.2, 99.6, 100.1)]
    res = fade.evaluate(_h1(win, IDX1_5), _h4(BEARISH_CRT), market="XAUUSD")
    assert res["status"] == fade.NO_SIGNAL
    # the intrabar extension + inside close completes the reversal, but no
    # trigger ever fires (the conf bar is the last bar)
    assert "SEEK_TRIG" in res["checks"][-1]["detail"]


def test_fade_confirmation_body_threshold_and_direction():
    """Confirmation needs body >= 0.55 AND the OPPOSITE direction."""
    win = [(100.9, 101.0, 97.0, 97.2),
           (97.2, 97.6, 96.9, 97.4),
           (97.4, 99.0, 97.2, 98.9),
           (99.9, 100.3, 99.8, 100.25),                  # bullish conf, body 0.35/0.5 = 0.7
           (100.25, 100.5, 100.2, 100.45)]               # trigger close 100.45 > conf high 100.3
    res = fade.evaluate(_h1(win, IDX1_5), _h4(BEARISH_CRT), market="XAUUSD")
    assert res["status"] == fade.SIGNAL
    # weak body 0.50 (< 0.55): NOT a confirmation -> no trigger
    win2 = [(100.9, 101.0, 97.0, 97.2),
            (97.2, 97.6, 96.9, 97.4),
            (97.4, 99.0, 97.2, 98.9),
            (99.4, 100.4, 99.4, 99.9),                   # body 0.5/1.0 = 0.50
            (99.9, 100.9, 99.8, 100.8)]                  # beyond the weak candle - irrelevant
    res = fade.evaluate(_h1(win2, pd.date_range("2026-09-07 16:00", periods=5,
                                                freq="1h", tz="UTC")),
                        _h4(BEARISH_CRT), market="XAUUSD")
    assert res["status"] == fade.NO_SIGNAL               # no valid conf -> no trigger


def test_fade_sell_mirror_from_bullish_crt():
    """Bullish CRT extends UP -> fade = SELL."""
    bull = (98.0, 101.4, 97.9, 101.0)                    # bullish CRT, range 3.5, thr 101.925
    win = [(101.0, 102.2, 100.8, 102.0),                 # ext high 102.2 > 101.925
           (102.0, 102.5, 101.9, 102.4),                 # furthest 102.5
           (102.4, 102.6, 101.0, 101.1),                 # close 101.1 inside [97.9, 101.4]
           (101.1, 101.2, 100.2, 100.3),                 # conf bearish body 0.8/1.0
           (100.3, 100.4, 99.7, 99.8)]                   # close 99.8 < conf low 100.2
    res = fade.evaluate(_h1(win, IDX1_5), _h4(bull), market="USDJPY")
    assert res["status"] == fade.SIGNAL, res
    assert res["direction"] == "SELL"
    assert res["sl"] == pytest.approx(102.6)   # the furthest wick (bar 4 high)
    assert res["tp2"] == pytest.approx(97.9)


def test_fade_insufficient():
    assert fade.evaluate(pd.DataFrame(
        {"open": [1] * 10, "high": [1] * 10, "low": [1] * 10, "close": [1] * 10},
        index=pd.date_range("2026-09-01", periods=10, freq="1h", tz="UTC")),
        _h4(BEARISH_CRT), market="XAUUSD")["status"] == fade.INSUFFICIENT


def test_fade_backtest_tp1_then_tp2_exact_math():
    """BUY entry 100.1, SL 96.9 (risk 3.2), TP1 99.9, TP2 101.2."""
    post = [(100.1, 100.3, 99.8, 100.0),                 # lo 99.8 <= tp1 99.9 -> partial r1 = -0.0625
            (100.15, 101.6, 100.12, 101.4)]              # hi >= 101.2 -> TP2 r2 = +0.34375
    h1 = _h1(FADE_WIN + post, pd.date_range("2026-09-07 16:00", periods=7,
                                            freq="1h", tz="UTC"))
    bt = fade.backtest(h1, _h4(BEARISH_CRT), "XAUUSD")
    assert len(bt["trades"]) == 1
    t = bt["trades"][0]
    r1 = (99.9 - 100.1) / 3.2
    r2 = (101.2 - 100.1) / 3.2
    assert t["exit_reason"] == "TP2"
    assert t["r_multiple"] == pytest.approx(round(0.5 * r1 + 0.5 * r2, 4))


def test_fade_backtest_sl_first_full_loss():
    post = [(100.1, 100.4, 96.5, 96.8)]                  # lo 96.5 <= SL 96.9
    h1 = _h1(FADE_WIN + post, pd.date_range("2026-09-07 16:00", periods=6,
                                            freq="1h", tz="UTC"))
    bt = fade.backtest(h1, _h4(BEARISH_CRT), "XAUUSD")
    t = bt["trades"][0]
    assert t["exit_reason"] == "STOP_LOSS"
    assert t["r_multiple"] == pytest.approx(-1.0)


# ===========================================================================
# live adapters
# ===========================================================================
def test_adapters_identity_and_independence():
    from app.strategies.research_only.live import (FadeCrtExtensionStrategy,
                                                   HybridCrtTbsStrategy)
    assert HybridCrtTbsStrategy.id == "strategy_hybrid_crt_tbs"
    assert FadeCrtExtensionStrategy.id == "strategy_fade_crt_ext"
    assert HybridCrtTbsStrategy.name == "Hybrid CRT + TBS"
    assert FadeCrtExtensionStrategy.name == "Fade CRT Extension"
    for cls in (HybridCrtTbsStrategy, FadeCrtExtensionStrategy):
        assert cls.markets is None                       # instrument-agnostic
        assert cls.configured_rr is None                 # RR is per-setup


def test_adapter_candidates_carry_spec_levels():
    from app.strategies.research_only.live import (FadeCrtExtensionStrategy,
                                                   HybridCrtTbsStrategy)
    c1 = HybridCrtTbsStrategy().detect_signal(
        _h1(HYBRID_WIN, IDX1), "EURUSD", "1H",
        higher_frames={"4H": _h4(BEARISH_CRT)})
    assert c1 is not None and c1.strategy_id == "strategy_hybrid_crt_tbs"
    assert c1.direction == "SELL"
    assert c1.tps == [pytest.approx(99.9), pytest.approx(98.6)]
    assert c1.sl == pytest.approx(101.0)
    assert c1.extra["spec_id"] == "PINSTRIPE V6"
    assert c1.extra["management"] == {"partial_fraction": 0.5,
                                      "breakeven_after_partial": True,
                                      "final_tp": "TP2"}
    c2 = FadeCrtExtensionStrategy().detect_signal(
        _h1(FADE_WIN, IDX1_5), "XAUUSD", "1H",
        higher_frames={"4H": _h4(BEARISH_CRT)})
    assert c2 is not None and c2.direction == "BUY"
    assert c2.tps == [pytest.approx(99.9), pytest.approx(101.2)]
    assert c2.extra["spec_strategy"] == "Fade_CRT_Extension"


def test_adapter_no_context_no_signal():
    from app.strategies.research_only.live import HybridCrtTbsStrategy
    assert HybridCrtTbsStrategy().detect_signal(
        _h1(HYBRID_WIN, IDX1), "EURUSD", "1H") is None
    assert HybridCrtTbsStrategy().detect_signal(
        _h1(HYBRID_WIN, IDX1), "EURUSD", "1H",
        higher_frames={"4H": None}) is None


def test_both_strategies_same_pair_independently():
    """Rule 7: the same pair at the same time - both adapters produce
    candidates for the SAME market in the same window."""
    from app.strategies.research_only.live import (FadeCrtExtensionStrategy,
                                                   HybridCrtTbsStrategy)
    c1 = HybridCrtTbsStrategy().detect_signal(
        _h1(HYBRID_WIN, IDX1), "EURUSD", "1H",
        higher_frames={"4H": _h4(BEARISH_CRT)})
    c2 = FadeCrtExtensionStrategy().detect_signal(
        _h1(FADE_WIN, IDX1_5), "EURUSD", "1H",
        higher_frames={"4H": _h4(BEARISH_CRT)})
    assert c1 is not None and c2 is not None
    assert c1.strategy_id != c2.strategy_id


# ===========================================================================
# execution + management wiring
# ===========================================================================
def test_duplicate_guard_is_per_strategy(fresh_store):
    """Rule 7: a DIFFERENT strategy's setup on the same candle is NOT a
    duplicate; a same-strategy re-send still is."""
    from app.execution.mt5 import _setup_already_sent
    fresh_store.create("signals", {
        "strategy_id": "strategy_hybrid_crt_tbs", "market": "EURUSD",
        "direction": "SELL", "candle_time": "2026-10-25T14:00:00+00:00",
        "execution_status": "SUBMITTED"})
    same = {"strategy_id": "strategy_hybrid_crt_tbs", "market": "EURUSD",
            "direction": "SELL", "candle_time": "2026-10-25T14:00:00+00:00"}
    other = {"strategy_id": "strategy_fade_crt_ext", "market": "EURUSD",
             "direction": "SELL", "candle_time": "2026-10-25T14:00:00+00:00"}
    assert _setup_already_sent(fresh_store, same) is not None   # blocked
    assert _setup_already_sent(fresh_store, other) is None      # INDEPENDENT


def test_ticket_tp_is_tp2_for_pinstripe():
    from app.execution.mt5 import PINSTRIPE_TP2_STRATEGIES
    assert PINSTRIPE_TP2_STRATEGIES == {"strategy_hybrid_crt_tbs",
                                        "strategy_fade_crt_ext"}


def test_apply_fill_records_pinstripe_rr(fresh_store, monkeypatch):
    """The order RR is measured against the ticket TP2 (rr_primary)."""
    from app.execution.mt5 import _apply_fill
    doc = fresh_store.create("signals", {
        "userId": "u1", "strategy_id": "strategy_fade_crt_ext",
        "market": "XAUUSD", "direction": "BUY", "entry": 100.1,
        "sl": 96.9, "tp1": 99.9, "tp2": 101.2, "rr_primary": 0.3438,
        "order_tp": 101.2})                              # the ticket carries TP2
    monkeypatch.setattr("app.config.settings.telegram_bot_token", None)
    _apply_fill(fresh_store, {**doc}, {"ticket": 21, "price": 100.1,
                                       "volume": 0.1}, 0.1)
    out = fresh_store.get("signals", doc["id"])
    assert out["intended_rr"] == pytest.approx(0.3438)


class _Provider:
    def __init__(self, df):
        self.df = df

    def get_candles(self, market, timeframe, limit=400):
        return self.df


@pytest.fixture()
def _ps_capture(monkeypatch):
    calls = []

    def fake_execute(user_id, position, plan, reason=""):
        calls.append({"plan": dict(plan), "reason": reason})
        return {"executed": [{"op": next(iter(plan))}], "errors": []}

    import app.aimanager.risk_gate as rg
    monkeypatch.setattr(rg, "execute_plan", fake_execute)
    import app.aimanager.recorder as rec
    monkeypatch.setattr(rec, "record", lambda *a, **k: None)
    return calls


def _ps_pos(entry=100.1, tp1=99.9, tp2=101.2, cur=100.0):
    return {"ticket": 31, "type": "BUY", "app_market": "XAUUSD",
            "symbol": "XAUUSD", "price_open": entry, "price_current": cur,
            "sl": 96.9, "tp": tp2, "profit": 1.0, "volume": 0.1}


def _ps_sig():
    return {"strategy_id": "strategy_fade_crt_ext", "market": "XAUUSD",
            "direction": "BUY", "sl": 96.9, "tp1": 99.9, "tp2": 101.2}


def test_pinstripe_ladder_partial_and_breakeven_at_tp1(_ps_capture):
    from app.aimanager.engine import TradeManager
    calls = _ps_capture
    mgr = TradeManager(provider=_Provider(None), router=None)
    out = mgr._review("u1", _ps_pos(cur=99.85), _ps_sig(), "manual", False,
                      {}, 1.0)
    plans = [c["plan"] for c in calls]
    assert {"partial_fraction": 0.5} in plans            # exactly 50%
    be = [p for p in plans if "modify_sl" in p]
    assert be and be[0]["modify_sl"] == pytest.approx(100.1)   # exact entry
    assert "breakeven" in calls[1]["reason"].lower()
    assert out["actions"] is not None


def test_pinstripe_ladder_closes_remainder_at_tp2(_ps_capture):
    from app.aimanager.engine import TradeManager
    calls = _ps_capture
    mgr = TradeManager(provider=_Provider(None), router=None)
    mgr._review("u1", _ps_pos(cur=101.3), _ps_sig(), "manual", False, {}, 1.0)
    plans = [c["plan"] for c in calls]
    assert {"close_full": True} in plans
    assert not [p for p in plans if "partial_fraction" in p]   # no partial at TP2


def test_pinstripe_ladder_idempotent(_ps_capture):
    from app.aimanager.engine import TradeManager
    calls = _ps_capture
    mgr = TradeManager(provider=_Provider(None), router=None)
    sig = _ps_sig()
    mgr._review("u1", _ps_pos(cur=99.85), sig, "manual", False, {}, 1.0)
    n = len(calls)
    mgr._review("u1", _ps_pos(cur=99.85), sig, "manual", False, {}, 2.0)
    assert len(calls) == n                               # no double partial


def test_pinstripe_block_ignores_other_strategies(_ps_capture):
    from app.aimanager.engine import TradeManager
    calls = _ps_capture
    mgr = TradeManager(provider=_Provider(None), router=None)
    sig = {"strategy_id": "strategy_trendbreak_h1", "tp1": 99.9, "tp2": 101.2}
    mgr._review("u1", _ps_pos(cur=99.85), sig, "manual", False, {}, 1.0)
    assert calls == []                                   # not pinstripe -> untouched


# ===========================================================================
# registration
# ===========================================================================
def test_registry_eight_strategies_boot_active():
    import app.strategies as reg
    from app.learning.versions import START_PAUSED, lifecycle_of
    from app.strategies.research_only.live import (FadeCrtExtensionStrategy,
                                                   HybridCrtTbsStrategy)
    assert len(reg.STRATEGY_CLASSES) == 8
    assert reg.STRATEGY_CLASSES["strategy_hybrid_crt_tbs"] is HybridCrtTbsStrategy
    assert reg.STRATEGY_CLASSES["strategy_fade_crt_ext"] is FadeCrtExtensionStrategy
    for sid in ("strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"):
        assert sid not in START_PAUSED
        assert lifecycle_of(sid) == "LIVE"


def test_pinstripe_boot_docs_active(fresh_store, monkeypatch):
    from app.learning import versions as vc
    monkeypatch.setattr(vc, "get_store", lambda: fresh_store)
    for sid in ("strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"):
        vc.LIFECYCLE.pop(sid, None)
    vc.ensure_strategy_docs()
    docs = {d["id"]: d for d in fresh_store.list("strategies", limit=1000)}
    assert docs["strategy_hybrid_crt_tbs"]["status"] == "ACTIVE"
    assert docs["strategy_fade_crt_ext"]["status"] == "ACTIVE"
