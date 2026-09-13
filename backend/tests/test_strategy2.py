"""Strategy 2 tests (SPEC §10, §11, §58): 9/21 EMA cross, ATR SL, 1/2/3R TPs."""
import numpy as np
import pandas as pd

from app.strategies import get_strategy


def test_exact_base_parameters():
    s = get_strategy("strategy_2_ema_atr")
    p = s.get_parameters()
    assert p["fast_len"] == 9      # NOT 5 - the one requested modification
    assert p["slow_len"] == 21     # NOT 13
    assert p["atr_len"] == 14
    assert p["sl_mult"] == 1.5
    assert (p["tp1_rr"], p["tp2_rr"], p["tp3_rr"]) == (1.0, 2.0, 3.0)


def test_buy_sell_signals_match_ema_cross(xau_15m):
    s = get_strategy("strategy_2_ema_atr")
    state = s.compute(xau_15m, s.base_params)
    ema9, ema21 = state["ema_f"], state["ema_s"]
    buy_bars = set(state["buy"].index[state["buy"]])
    sell_bars = set(state["sell"].index[state["sell"]])
    closes = xau_15m["close"]
    for i in range(2, len(xau_15m)):
        if xau_15m.index[i] in buy_bars:
            assert ema9.iloc[i] > ema21.iloc[i] and ema9.iloc[i - 1] <= ema21.iloc[i - 1]
        if xau_15m.index[i] in sell_bars:
            assert ema9.iloc[i] < ema21.iloc[i] and ema9.iloc[i - 1] >= ema21.iloc[i - 1]
    # no bar can be both
    assert not (buy_bars & sell_bars)
    # sanity: crosses exist in the dataset
    assert buy_bars and sell_bars


def test_sl_is_1_5_atr_and_tps_1_2_3r(xau_15m):
    from app.core.indicators import atr
    s = get_strategy("strategy_2_ema_atr")
    df = xau_15m.tail(1200)
    state = s.compute(df, s.base_params)
    idx = [i for i in range(100, len(df)) if s.detect_on_bar(state, i)]
    assert idx, "expected crossovers in demo data"
    i = idx[-1]
    ev = s.detect_on_bar(state, i)
    rk = s.calculate_risk(df, state, i, ev["direction"], s.base_params)
    expected_risk = float(atr(df, 14).iloc[i]) * 1.5
    assert abs(rk["risk"] - expected_risk) < 1e-9
    tps = s.calculate_targets(rk["entry"], rk["risk"], ev["direction"], s.base_params)
    for k, tp in enumerate(tps, start=1):
        if ev["direction"] == "BUY":
            assert abs((tp - rk["entry"]) / rk["risk"] - k) < 1e-9
            assert rk["sl"] < rk["entry"]
        else:
            assert abs((rk["entry"] - tp) / rk["risk"] - k) < 1e-9
            assert rk["sl"] > rk["entry"]


def test_backtest_tracks_tp_ladder_and_sl(xau_15m):
    s = get_strategy("strategy_2_ema_atr")
    trades = s.backtest(xau_15m.tail(1500))
    assert trades, "expected trades from demo dataset"
    statuses = {t["status"] for t in trades}
    assert statuses <= {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "EXPIRED"}
    for t in trades:
        # conservative accounting: never lose more than 1R, never bank beyond 3R
        assert -1.0 <= t["r_multiple"] <= 3.0
        if t["status"] == "SL_HIT" and t["tp_hits"] == 0:
            assert t["r_multiple"] == -1.0
        if t["tp_hits"] >= 1:
            assert t["r_multiple"] > 0
