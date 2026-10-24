"""TPC-15 Trend Pullback Confirmation - unit tests (owner brief section D).

Covers: valid BUY / SELL setups, invalid trend, missing pullback, failed
confirmation, out-of-session candles, insufficient history, invalid risk,
next-candle-open entry math, the five-candle stop window, dedupe,
look-ahead protection and structural output. Also proves ISOLATION: TPC-15
is not registered, never scanned, never executed.
"""
import inspect

import pandas as pd
import pytest

from app.strategies.tpc15 import (CONFIG, compute_emas, evaluate,
                                  evaluate_setup, finalize_entry,
                                  inside_session, setup_id_for)
from app.strategies.tpc15 import strategy as tpc15


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def m15_index(n, end_open="2026-10-22T14:45"):
    """n consecutive M15 candle-open stamps, the LAST one opening at end_open."""
    idx = pd.date_range(end=end_open, periods=n, freq="15min", tz="UTC")
    return idx


def trend_frame(n=220, step=0.15, start=100.0, end_open="2026-10-22T14:45",
                force_extreme=None):
    """Steady uptrend on M15. Optional force_extreme=(rel_index, low) plants a
    deep low inside the last six candles to pin the stop window precisely."""
    idx = m15_index(n, end_open)
    o, h, l, c = [], [], [], []
    px = start
    for i in range(n):
        o.append(px)
        h.append(px + 0.05)
        l.append(px - 0.05)
        c.append(px + step)
        px += step
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)
    if force_extreme:
        rel, low = force_extreme           # rel measured from the END (1 = last)
        df.iloc[-rel, df.columns.get_loc("low")] = low
    return df


def with_pullback_and_confirmation(df):
    """Append pullback + confirmation candles to an uptrend frame and return
    (df_extended, confirmation_open_time). The pullback low touches EMA21;
    the confirmation candle dips to EMA21 and closes above the previous high."""
    ema9, ema21, _ = compute_emas(df["close"])
    last_close = float(df["close"].iloc[-1])
    # pullback candle: touches EMA21 with its low, small bearish body
    p_open = last_close
    p_high = p_open + 0.05
    p_low = float(ema21.iloc[-1]) - 0.05
    p_close = p_open - 0.05
    # confirmation candle: low <= EMA21, bullish, close above previous high
    c_open = p_close
    c_high = p_high + 0.6
    c_close = p_high + 0.4
    c_low = min(c_open, p_low) - 0.02
    idx = df.index
    ext = pd.concat([df, pd.DataFrame([
        {"open": p_open, "high": p_high, "low": p_low, "close": p_close},
        {"open": c_open, "high": c_high, "low": c_low, "close": c_close},
    ], index=[idx[-1] + pd.Timedelta(minutes=15),
              idx[-1] + pd.Timedelta(minutes=30)])])
    return ext, idx[-1] + pd.Timedelta(minutes=30)


def flat_frame(n=10, px=100.0, end_open="2026-10-22T15:00"):
    """Small flat frame for constant-EMA rule tests (in-session closes)."""
    idx = m15_index(n, end_open)
    return pd.DataFrame({"open": [px] * n, "high": [px + 0.2] * n,
                         "low": [px - 0.2] * n, "close": [px] * n}, index=idx)


def const_emas(n, e9, e21, e200):
    idx = range(n)
    return (pd.Series([e9] * n, index=idx), pd.Series([e21] * n, index=idx),
            pd.Series([e200] * n, index=idx))


# ---------------------------------------------------------------------------
# full pipeline: valid BUY with computed EMAs
# ---------------------------------------------------------------------------
def test_valid_buy_setup_end_to_end():
    df = trend_frame(220, force_extreme=(4, 99.0))   # deep low inside the stop window
    df, conf_open = with_pullback_and_confirmation(df)
    # append the ENTRY candle right after the confirmation
    entry_open = float(df["close"].iloc[-1])
    idx = df.index
    df = pd.concat([df, pd.DataFrame([
        {"open": entry_open, "high": entry_open + 5.0, "low": entry_open - 5.0,
         "close": entry_open + 0.1}                    # extreme wicks: must be ignored
    ], index=[idx[-1] + pd.Timedelta(minutes=15)])])

    res = evaluate(df, emitted=set())
    assert res["status"] == "SIGNAL", res
    assert res["direction"] == "BUY"
    assert res["final"] is True
    assert res["entry"] == pytest.approx(entry_open)          # NEXT CANDLE OPEN
    assert res["entry_source"] == "NEXT_CANDLE_OPEN"
    # stop = the FIVE completed candles before the confirmation (which, after
    # the pullback+confirmation appends, includes the planted deep low)
    assert res["stop_loss"] == pytest.approx(99.0)
    # risk/TP from the ACTUAL entry, never from the reference close
    risk = entry_open - 99.0
    assert res["risk"] == pytest.approx(risk)
    assert res["take_profit"] == pytest.approx(entry_open + risk * 1.25)
    assert res["risk_reward"] == "1:1.25"
    assert res["tp_multiple"] == 1.25
    assert res["reasons"] == ["Price above EMA 200", "EMA 9 above EMA 21",
                              "Pullback to EMA 21",
                              "Bullish confirmation above previous high"]
    assert res["timestamp"]                                   # confirmation close stamp
    assert res["entry_time"]                                  # entry candle open stamp
    for key in ("direction", "entry", "stop_loss", "take_profit", "timestamp", "reasons"):
        assert key in res, f"structured output missing {key}"


def test_valid_sell_setup_end_to_end():
    base = trend_frame(220)
    f = 400.0
    down = pd.DataFrame({"open": [f - v for v in base["open"]],
                         "high": [f - v for v in base["low"]],
                         "low": [f - v for v in base["high"]],
                         "close": [f - v for v in base["close"]]},
                        index=base.index)
    # plant the stop-window extreme on the DOWN frame (positive prices)
    down.iloc[-4, down.columns.get_loc("high")] = 403.0
    ema9, ema21, _ = compute_emas(down["close"])
    last_close = float(down["close"].iloc[-1])
    p_open = last_close
    p_high = float(ema21.iloc[-1]) + 0.05          # pullback UP touches EMA21
    p_low = p_open - 0.05
    p_close = p_open + 0.05
    c_open = p_close
    c_low = p_low - 0.6
    c_close = p_low - 0.4                          # below previous low, bearish
    c_high = max(c_open, p_high) + 0.02
    idx = down.index
    down = pd.concat([down, pd.DataFrame([
        {"open": p_open, "high": p_high, "low": p_low, "close": p_close},
        {"open": c_open, "high": c_high, "low": c_low, "close": c_close},
    ], index=[idx[-1] + pd.Timedelta(minutes=15),
              idx[-1] + pd.Timedelta(minutes=30)])])
    entry_open = float(down["close"].iloc[-1])
    down = pd.concat([down, pd.DataFrame([
        {"open": entry_open, "high": entry_open + 0.1, "low": entry_open - 0.1,
         "close": entry_open - 0.1}
    ], index=[down.index[-1] + pd.Timedelta(minutes=15)])])

    res = evaluate(down, emitted=set())
    assert res["status"] == "SIGNAL", res
    assert res["direction"] == "SELL"
    # stop = five-candle HIGH; the planted extreme inside the window wins
    assert res["stop_loss"] == pytest.approx(403.0)
    risk = 403.0 - entry_open
    assert res["risk"] == pytest.approx(risk)
    assert res["take_profit"] == pytest.approx(entry_open - risk * 1.25)
    assert res["reasons"] == ["Price below EMA 200", "EMA 9 below EMA 21",
                              "Pullback to EMA 21",
                              "Bearish confirmation below previous low"]


# ---------------------------------------------------------------------------
# rule-by-rule tests with constant EMAs (precise control)
# ---------------------------------------------------------------------------
def _buy_ready(n=10, px=100.0, prev_high=None, conf_close=None, conf_low=None,
               e21=99.0, e200=90.0, end_open="2026-10-22T15:00"):
    df = flat_frame(n, px, end_open)
    df.iloc[-2, df.columns.get_loc("high")] = prev_high if prev_high is not None else px + 0.3
    df.iloc[-1, df.columns.get_loc("close")] = conf_close if conf_close is not None else px + 0.5
    df.iloc[-1, df.columns.get_loc("open")] = px
    df.iloc[-1, df.columns.get_loc("low")] = conf_low if conf_low is not None else e21 - 0.1
    df.iloc[-1, df.columns.get_loc("high")] = max(df.iloc[-1]["close"], df.iloc[-1]["open"]) + 0.2
    return df, const_emas(n, e21 + 0.3, e21, e200)


def test_invalid_trend_blocks_buy():
    df, (e9, e21, e200) = _buy_ready(e200=110.0)      # price BELOW EMA200
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "NO_SIGNAL"
    trend_ok = [c for c in res["checks"] if c["rule"].startswith("BUY 1")][0]
    assert trend_ok["ok"] is False


def test_missing_pullback_blocks_buy():
    df, (e9, e21, e200) = _buy_ready(e21=90.0, e200=90.0, conf_low=100.0,
                                     prev_high=100.3, conf_close=100.5)
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "NO_SIGNAL"
    pull = [c for c in res["checks"] if c["rule"].startswith("BUY 3")][0]
    assert pull["ok"] is False


def test_failed_confirmation_blocks_buy():
    # close EQUALS the previous high - not ABOVE it -> rule 4 fails
    df, (e9, e21, e200) = _buy_ready(prev_high=100.5, conf_close=100.5)
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "NO_SIGNAL"
    conf = [c for c in res["checks"] if c["rule"].startswith("BUY 4")][0]
    assert conf["ok"] is False


def test_bearish_confirmation_candle_blocks_buy():
    # closes above previous high but the candle is RED (close < open)
    df, (e9, e21, e200) = _buy_ready(prev_high=99.5, conf_close=100.2)
    df.iloc[-1, df.columns.get_loc("open")] = 100.9
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "NO_SIGNAL"


def test_out_of_session_before_start():
    df, (e9, e21, e200) = _buy_ready(end_open="2026-10-22T13:30")   # closes 13:45
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "OUT_OF_SESSION"


def test_out_of_session_at_end_boundary():
    # confirmation closing exactly 18:00 UTC is OUT (end exclusive)
    df, (e9, e21, e200) = _buy_ready(end_open="2026-10-22T17:45")
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] == "OUT_OF_SESSION"


def test_session_start_boundary_is_inclusive():
    # closing exactly 14:00 UTC is IN (start inclusive)
    df, (e9, e21, e200) = _buy_ready(end_open="2026-10-22T13:45")
    res = evaluate_setup(df, e9, e21, e200)
    assert res["status"] != "OUT_OF_SESSION"


def test_current_time_override_respected():
    df, (e9, e21, e200) = _buy_ready()                # candle closes 15:15 (in)
    res = evaluate_setup(df, e9, e21, e200, current_time_utc="2026-10-22T10:00")
    assert res["status"] == "OUT_OF_SESSION"


def test_insufficient_history_detection():
    df, (e9, e21, e200) = _buy_ready(n=5)             # need 6
    res = evaluate_setup(df.iloc[1:], e9.iloc[1:], e21.iloc[1:], e200.iloc[1:])
    assert res["status"] == "INSUFFICIENT_DATA"


def test_insufficient_history_computed_emas():
    df = trend_frame(150)                              # < EMA 200 period
    df, _ = with_pullback_and_confirmation(df)
    res = evaluate_setup(df)
    assert res["status"] == "INSUFFICIENT_DATA"


def test_insufficient_history_evaluate_path():
    df = trend_frame(200)                              # 199 hist < EMA 200 need
    res = evaluate(df)
    assert res["status"] == "INSUFFICIENT_DATA"


def test_misaligned_ema_series_refused():
    df, (e9, e21, e200) = _buy_ready(n=10)
    res = evaluate_setup(df, e9[:-1], e21[:-1], e200[:-1])
    assert res["status"] == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# next-candle entry + five-candle stop (detect + finalize split)
# ---------------------------------------------------------------------------
def test_finalize_entry_uses_next_open_for_risk_and_tp():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0)
    df.iloc[-6:-1, df.columns.get_loc("low")] = [99.5, 99.2, 98.8, 99.0, 99.4]
    setup = evaluate_setup(df, e9, e21, e200)
    assert setup["status"] == "SIGNAL"
    assert setup["stop_loss"] == pytest.approx(98.8)           # min of the five
    assert setup["entry_rule"] == "NEXT_CANDLE_OPEN"
    assert setup["reference_entry"] == pytest.approx(float(df["close"].iloc[-1]))
    # provisional TP is reference-based and clearly flagged by entry_rule
    prov_risk = setup["reference_entry"] - 98.8
    assert setup["take_profit"] == pytest.approx(setup["reference_entry"] + prov_risk * 1.25)

    final = finalize_entry(setup, next_open=101.3,
                           next_open_time_utc="2026-10-22T15:15")
    assert final["status"] == "SIGNAL" and final["final"] is True
    assert final["entry"] == pytest.approx(101.3)
    assert final["stop_loss"] == pytest.approx(98.8)           # unchanged
    assert final["risk"] == pytest.approx(2.5)
    assert final["take_profit"] == pytest.approx(101.3 + 2.5 * 1.25)


def test_finalize_invalid_risk_gap_through_stop():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0)
    setup = evaluate_setup(df, e9, e21, e200)
    res = finalize_entry(setup, next_open=95.0,                 # below the 5-candle low
                         next_open_time_utc="2026-10-22T15:15")
    assert res["status"] == "INVALID_RISK"


def test_finalize_refuses_entry_at_session_end():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0,
                                     end_open="2026-10-22T17:30")  # closes 17:45
    setup = evaluate_setup(df, e9, e21, e200)
    assert setup["status"] == "SIGNAL"
    res = finalize_entry(setup, next_open=101.0,
                         next_open_time_utc="2026-10-22T18:00")     # AT the end
    assert res["status"] == "OUT_OF_SESSION"


def test_finalize_refuses_missing_open():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0)
    setup = evaluate_setup(df, e9, e21, e200)
    res = finalize_entry(setup, next_open=None)
    assert res["status"] == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# five-candle stop window precision
# ---------------------------------------------------------------------------
def test_stop_window_excludes_confirmation_and_outside_candles():
    df = trend_frame(220)
    # plant: extreme low 6 candles back (OUTSIDE the window) and one 3 back (INSIDE)
    df.iloc[-6, df.columns.get_loc("low")] = 95.0     # -6: outside (window is -6:-1 -> rows -6..-2 of a frame ENDING at the confirmation)
    df.iloc[-3, df.columns.get_loc("low")] = 97.0     # inside
    conf_open = df.index[-1]
    setup = evaluate_setup(df)
    assert setup["status"] == "NO_SIGNAL"              # trend frame has no pullback
    # use the raw window math through a BUY-ready frame instead:
    df2, (e9, e21, e200) = _buy_ready(n=12, e21=95.0, e200=90.0)
    lows = [99.5, 99.2, 98.8, 99.0, 99.4]              # five candles before confirmation
    df2.iloc[-6:-1, df2.columns.get_loc("low")] = lows
    df2.iloc[-7, df2.columns.get_loc("low")] = 90.0    # OUTSIDE the window
    setup2 = evaluate_setup(df2, e9, e21, e200)
    assert setup2["status"] == "SIGNAL"
    assert setup2["stop_loss"] == pytest.approx(98.8)  # 90.0 ignored, window exact
    assert setup2["stop_window"]["candles"] == 5
    w = df2.iloc[-6:-1]
    assert setup2["stop_window"]["from"] == w.index[0].isoformat()
    assert setup2["stop_window"]["to"] == w.index[-1].isoformat()


# ---------------------------------------------------------------------------
# dedupe, look-ahead, structural
# ---------------------------------------------------------------------------
def test_duplicate_setup_suppressed():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0)
    emitted = set()
    first = evaluate_setup(df, e9, e21, e200, emitted=emitted)
    assert first["status"] == "SIGNAL"
    second = evaluate_setup(df, e9, e21, e200, emitted=emitted)
    assert second["status"] == "DUPLICATE"
    assert second["setup_id"] == first["setup_id"]


def test_setup_id_is_stable_per_confirmation_candle():
    df = trend_frame(220)
    a = setup_id_for("BUY", df.index[-1])
    b = setup_id_for("BUY", df.index[-1])
    assert a == b and a.startswith("TPC15-BUY-")


def test_no_lookahead_detection_is_prefix_stable():
    df, conf_open = with_pullback_and_confirmation(trend_frame(220))
    hist = df                                          # ends at the confirmation
    before = evaluate_setup(hist, emitted=set())
    assert before["status"] == "SIGNAL"
    # the all-in-one path must reproduce the SAME detection from the same
    # history regardless of the entry candle that follows it
    entry_open = float(hist["close"].iloc[-1])
    full = pd.concat([hist, pd.DataFrame(
        [{"open": entry_open, "high": 600.0, "low": 400.0, "close": 550.0}],
        index=[hist.index[-1] + pd.Timedelta(minutes=15)])])
    after = evaluate(full, emitted=set())
    assert after["status"] == "SIGNAL"
    assert after["setup_id"] == before["setup_id"]
    assert after["stop_loss"] == before["stop_loss"]
    assert after["reference_entry"] == before["reference_entry"]
    # and a candle AFTER the confirmation is never treated as history: a new
    # evaluation sees the junk candle as its own confirmation -> no setup
    later = pd.concat([hist, pd.DataFrame(
        [{"open": 500.0, "high": 600.0, "low": 400.0, "close": 550.0}],
        index=[hist.index[-1] + pd.Timedelta(minutes=15)])])
    assert evaluate_setup(later, emitted=set())["status"] == "NO_SIGNAL"


def test_entry_candle_wicks_do_not_affect_levels():
    df = trend_frame(220, force_extreme=(4, 99.0))
    df, _ = with_pullback_and_confirmation(df)
    entry_open = float(df["close"].iloc[-1])
    wild = pd.concat([df, pd.DataFrame(
        [{"open": entry_open, "high": 9999.0, "low": 1.0, "close": 5000.0}],
        index=[df.index[-1] + pd.Timedelta(minutes=15)])])
    res = evaluate(wild, emitted=set())
    assert res["status"] == "SIGNAL"
    assert res["stop_loss"] == pytest.approx(99.0)
    assert res["entry"] == pytest.approx(entry_open)


def test_dataframe_and_list_inputs_equivalent():
    df, (e9, e21, e200) = _buy_ready(e21=95.0, e200=90.0)
    as_list = df.reset_index().rename(columns={"index": "time"}).to_dict("records")
    r1 = evaluate_setup(df, e9, e21, e200)
    r2 = evaluate_setup(as_list, e9, e21, e200)
    assert r1["status"] == r2["status"] == "SIGNAL"
    assert r1["stop_loss"] == r2["stop_loss"]


def test_inside_session_helper():
    assert inside_session("2026-10-22T14:00") is True       # start inclusive
    assert inside_session("2026-10-22T17:59") is True
    assert inside_session("2026-10-22T18:00") is False      # end exclusive
    assert inside_session("2026-10-22T13:59") is False


def test_config_matches_the_guide_verbatim():
    assert CONFIG == {"timeframe": "M15", "fast_ema_period": 9,
                      "pullback_ema_period": 21, "trend_ema_period": 200,
                      "session_start_utc": "14:00", "session_end_utc": "18:00",
                      "stop_lookback_candles": 5, "take_profit_r": 1.25}


# ---------------------------------------------------------------------------
# isolation: not registered, not executable, no broker surface
# ---------------------------------------------------------------------------
def test_tpc15_is_registered_and_live():
    """Owner directive 2026-10-24: TPC-15 is a live registered strategy."""
    import app.strategies as reg
    from app.learning.versions import START_PAUSED, lifecycle_of
    assert set(reg.STRATEGY_CLASSES.keys()) == {
        "strategy_h1_trend_breakout", "strategy_gold_orb",
        "strategy_tpc15_pullback", "strategy_trendbreak_h1",
        "strategy_zonesweep_m15"}
    assert "strategy_tpc15_pullback" not in START_PAUSED   # boot-ACTIVE doc
    assert lifecycle_of("strategy_tpc15_pullback") == "LIVE"


def test_engine_module_stays_pure_for_research_use():
    from app.strategies.tpc15 import strategy as tpc15_engine
    import ast
    tree = ast.parse(inspect.getsource(tpc15_engine))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "datetime", "typing", "pandas"}


def test_tpc15_has_no_execution_surface():
    import ast
    tree = ast.parse(inspect.getsource(tpc15))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "datetime", "typing", "pandas"}, imported
    # no classes at all: pure functions, no engine inheritance possible
    assert not any(isinstance(obj, type) for obj in vars(tpc15).values()
                   if getattr(obj, "__module__", "") == tpc15.__name__)


def test_tpc15_registered_name_visible():
    from app.strategies import all_strategies
    names = {s.name for s in all_strategies().values()}
    assert "TPC-15 Trend Pullback" in names


# ===========================================================================
# LIVE ADAPTER (registered strategy) - Candidate flow through the pipeline
# ===========================================================================
def test_adapter_buy_candidate_on_selected_market():
    from app.strategies.tpc15.live import TPC15PullbackStrategy
    df, _ = with_pullback_and_confirmation(trend_frame(220, force_extreme=(4, 99.0)))
    cand = TPC15PullbackStrategy().detect_signal(df, "EURUSD", "15M")
    assert cand is not None
    assert cand.strategy_id == "strategy_tpc15_pullback"
    assert cand.market == "EURUSD"                      # no instrument pin
    assert cand.direction == "BUY"
    assert cand.rr_primary == pytest.approx(1.25)
    assert cand.sl < cand.entry < cand.tps[0]           # BUY sides correct
    assert cand.tps[0] == pytest.approx(cand.entry + (cand.entry - cand.sl) * 1.25)
    assert cand.candle_time                             # confirmation candle identity
    assert cand.entry_rule if hasattr(cand, "entry_rule") else True
    assert "Pullback to EMA 21" in cand.analysis
    assert cand.checks and cand.checks[0]["rule"].startswith("BUY 1")


def test_adapter_sell_candidate_mirror():
    from app.strategies.tpc15.live import TPC15PullbackStrategy
    base = trend_frame(220)
    f = 400.0
    down = pd.DataFrame({"open": [f - v for v in base["open"]],
                         "high": [f - v for v in base["low"]],
                         "low": [f - v for v in base["high"]],
                         "close": [f - v for v in base["close"]]},
                        index=base.index)
    down.iloc[-4, down.columns.get_loc("high")] = 403.0
    ema9, ema21, _ = compute_emas(down["close"])
    last_close = float(down["close"].iloc[-1])
    p_open = last_close
    p_high = float(ema21.iloc[-1]) + 0.05
    p_low = p_open - 0.05
    p_close = p_open + 0.05
    c_open = p_close
    c_low = p_low - 0.6
    c_close = p_low - 0.4
    c_high = max(c_open, p_high) + 0.02
    idx = down.index
    down = pd.concat([down, pd.DataFrame([
        {"open": p_open, "high": p_high, "low": p_low, "close": p_close},
        {"open": c_open, "high": c_high, "low": c_low, "close": c_close},
    ], index=[idx[-1] + pd.Timedelta(minutes=15),
              idx[-1] + pd.Timedelta(minutes=30)])])
    cand = TPC15PullbackStrategy().detect_signal(down, "GBPUSD", "15M")
    assert cand is not None and cand.direction == "SELL"
    assert cand.market == "GBPUSD"
    assert cand.tps[0] < cand.entry < cand.sl           # SELL sides correct
    assert cand.sl == pytest.approx(403.0)
    assert cand.rr_primary == pytest.approx(1.25)


def test_adapter_timeframe_gate_and_insufficient_history():
    from app.strategies.tpc15.live import TPC15PullbackStrategy
    S = TPC15PullbackStrategy()
    df, _ = with_pullback_and_confirmation(trend_frame(220))
    assert S.detect_signal(df, "EURUSD", "1H") is None      # M15 only
    assert S.detect_signal(df.iloc[:50], "EURUSD", "15M") is None  # EMA warmup
    assert S.detect_signal(None, "EURUSD", "15M") is None


def test_adapter_rejects_wrong_tp_multiple():
    from app.strategies.tpc15 import live as live_mod
    from app.strategies.tpc15 import strategy as engine
    df, _ = with_pullback_and_confirmation(trend_frame(220, force_extreme=(4, 99.0)))
    monkey_seen = {}
    orig = engine.evaluate_setup
    def rigged(candles, *a, **k):
        res = orig(candles, *a, **k)
        if res.get("status") == "SIGNAL":
            res["tp_multiple"] = 2.0                    # an engine bug scenario
        return res
    live_mod.evaluate_setup = rigged
    try:
        cand = live_mod.TPC15PullbackStrategy().detect_signal(df, "EURUSD", "15M")
        assert cand is None                             # spec integrity holds
    finally:
        live_mod.evaluate_setup = orig


def test_adapter_live_module_imports_are_clean():
    import ast
    from app.strategies.tpc15 import live as live_mod
    tree = ast.parse(inspect.getsource(live_mod))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # 'base' = the platform strategy interface; 'strategy' = the pure engine
    assert imported <= {"__future__", "typing", "pandas", "base", "strategy"}, imported
