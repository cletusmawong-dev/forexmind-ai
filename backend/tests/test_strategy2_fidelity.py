"""Strategy 2 script-fidelity tests.

Pins the implementation to the user-supplied Pine Script
('KN - Smart TP SL Signals', pasted 2026-09-15) so any future drift in the
strategy logic fails the suite. Per the user's explicit decision the EMA
lengths stay 9/21 (script defaults are 5/13); every other rule is verbatim:

    entry = close
    risk  = ATR(14) * 1.5
    SL    = close -/+ risk
    TP1/2/3 = close +/- risk * 1.0 / 2.0 / 3.0
    BUY:  signal when fast EMA crosses ABOVE slow EMA
    SELL: signal when fast EMA crosses BELOW slow EMA
    TP hit:  BUY high >= TP   /  SELL low <= TP   (each TP independent)
    SL hit:  BUY low <= SL    /  SELL high >= SL
"""
import numpy as np
import pandas as pd
import pytest


def _synth_df():
    """200 falling bars then 200 rising bars -> exactly one bullish cross."""
    n = 400
    close = np.concatenate([4000 - np.arange(200) * 0.5,
                            3900 + np.arange(200) * 0.5])
    high = close + 0.4
    low = close - 0.4
    open_ = close.copy()
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": 1.0}, index=idx)


# ---------- inputs pinned to the script (9/21 per user decision) ----------

def test_script_inputs_pinned():
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    p = EmaAtrStrategy.base_params
    assert p["fast_len"] == 9      # script default 5; user chose to keep 9
    assert p["slow_len"] == 21     # script default 13; user chose to keep 21
    assert p["atr_len"] == 14
    assert p["sl_mult"] == 1.5
    assert p["tp1_rr"] == 1.0
    assert p["tp2_rr"] == 2.0
    assert p["tp3_rr"] == 3.0


# ---------- signal logic: crossover semantics identical to Pine ----------

def test_crossover_semantics_match_pine():
    from app.core.indicators import crossunder, crossover, ema
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    df = _synth_df()
    p = EmaAtrStrategy.base_params
    st = EmaAtrStrategy().compute(df, p)
    f, s = st["ema_f"], st["ema_s"]
    # every buy bar satisfies Pine's ta.crossover definition exactly
    for i in np.where(st["buy"])[0]:
        assert i >= 1
        assert f.iloc[i] > s.iloc[i] and f.iloc[i - 1] <= s.iloc[i - 1]
    for i in np.where(st["sell"])[0]:
        assert f.iloc[i] < s.iloc[i] and f.iloc[i - 1] >= s.iloc[i - 1]
    assert not bool((st["buy"] & st["sell"]).any())
    # the synthetic data contains at least one of each (test validity)
    assert int(st["buy"].sum()) >= 1


# ---------- level math: entry close, ATR x 1.5, TP at 1R/2R/3R ----------

def test_levels_math_matches_pine():
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    df = _synth_df()
    atr_series = pd.Series([2.0] * len(df), index=df.index)
    state = {"atr": atr_series}
    strat = EmaAtrStrategy()
    p = dict(strat.base_params)
    i = len(df) - 1
    close = float(df["close"].iloc[i])

    rk = strat.calculate_risk(df, state, i, "BUY", p)
    assert rk["entry"] == close
    assert rk["risk"] == pytest.approx(2.0 * 1.5)
    assert rk["sl"] == pytest.approx(close - 3.0)
    tps = strat.calculate_targets(rk["entry"], rk["risk"], "BUY", p)
    assert [pytest.approx(t) for t in tps] == [
        pytest.approx(close + 3.0 * r) for r in (1.0, 2.0, 3.0)]

    rk = strat.calculate_risk(df, state, i, "SELL", p)
    assert rk["sl"] == pytest.approx(close + 3.0)
    tps = strat.calculate_targets(rk["entry"], rk["risk"], "SELL", p)
    assert [pytest.approx(t) for t in tps] == [
        pytest.approx(close - 3.0 * r) for r in (1.0, 2.0, 3.0)]


# ---------- hit conditions: BUY high/low vs SELL mirrored, as in Pine ----------

def _tracker(store, candle_df):
    from app.engine import tracker as T

    class P:
        def get_candles(self, market, tf, limit=3):
            return candle_df

    t = T.SignalTracker(P())
    return t


def test_hit_conditions_match_pine(monkeypatch):
    import os, tempfile
    from app.db.store import LocalStore
    from app.engine import tracker as T

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(T, "get_store", lambda: store)
    monkeypatch.setattr(T, "notify", lambda *a, **k: None)

    base = {"userId": "u1", "market": "EURUSD", "timeframe": "15M",
            "risk": 0.0050, "strategy_name": "9/21 EMA Smart TP/SL",
            "strategy_id": "strategy_2_ema_atr", "status": "ACTIVE",
            "completed": False, "user_action": None,
            "candle_time": "2026-09-14 12:00", "tp_hits": 0,
            "params": {"expire_bars": 200}}
    # BUY: entry 1.1000, sl 1.0950, tp1 1.1050, tp2 1.1100
    store.create("signals", dict(base, id="sBuy", signal_id="SIG-B",
                                 direction="BUY", entry=1.1000, sl=1.0950,
                                 tp1=1.1050, tp2=1.1100, tp3=None))
    # SELL: entry 1.1000, sl 1.1050, tp1 1.0950
    store.create("signals", dict(base, id="sSell", signal_id="SIG-S",
                                 direction="SELL", entry=1.1000, sl=1.1050,
                                 tp1=1.0950, tp2=None, tp3=None))

    # SELL bar whose HIGH only reaches 1.1040: touches nothing (gap below TP1
    # conditions) - Pine: high >= sl(1.1050)? no; low <= tp1(1.0950)? no.
    ts = pd.Timestamp("2026-09-14 12:15")
    quiet = pd.DataFrame({"open": [1.1000], "high": [1.1040],
                          "low": [1.0960], "close": [1.1010]}, index=[ts])
    _tracker(store, quiet).update_market("EURUSD")
    assert store.get("signals", "sSell")["status"] == "ACTIVE"

    # SELL bar with high >= sl AND low <= tp1 on the same candle: the app
    # books SL first (conservative accounting - documented deviation:
    # Pine could draw both marks on one bar).
    spike = pd.DataFrame({"open": [1.1010], "high": [1.1060],
                          "low": [1.0940], "close": [1.0990]},
                         index=[pd.Timestamp("2026-09-14 12:30")])
    _tracker(store, spike).update_market("EURUSD")
    doc = store.get("signals", "sSell")
    assert doc["status"] == "SL_HIT" and doc["outcome"] == "LOSS"
    assert doc["r_multiple"] == -1.0

    # BUY bar that touches TP1 only via its HIGH (low stays above SL):
    # Pine condition high >= tp1 -> TP1_HIT.
    store.create("signals", dict(base, id="sBuy2", signal_id="SIG-B2",
                                 direction="BUY", entry=1.1000, sl=1.0950,
                                 tp1=1.1050, tp2=1.1100, tp3=None))
    up = pd.DataFrame({"open": [1.1010], "high": [1.1060],
                       "low": [1.0995], "close": [1.1055]},
                      index=[pd.Timestamp("2026-09-14 12:45")])
    _tracker(store, up).update_market("EURUSD")
    assert store.get("signals", "sBuy2")["status"] == "TP1_HIT"

    # BUY bar whose LOW pierces SL while TP never touched -> LOSS -1R.
    store.create("signals", dict(base, id="sBuy3", signal_id="SIG-B3",
                                 direction="BUY", entry=1.1000, sl=1.0950,
                                 tp1=1.1050, tp2=1.1100, tp3=None))
    down = pd.DataFrame({"open": [1.0990], "high": [1.0995],
                         "low": [1.0930], "close": [1.0940]},
                        index=[pd.Timestamp("2026-09-14 13:00")])
    _tracker(store, down).update_market("EURUSD")
    doc = store.get("signals", "sBuy3")
    assert doc["status"] == "SL_HIT" and doc["outcome"] == "LOSS"
    assert doc["r_multiple"] == -1.0
