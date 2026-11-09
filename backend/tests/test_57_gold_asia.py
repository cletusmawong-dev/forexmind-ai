"""GOLDASIA - the owner's Gold Asian-Range Breakout (guide
IMPLEMENTATION_GUIDE_GOLDASIA.md): rule tests, the account-level gates
(1.0% risk, shared gold position lock), and the GOLD_ASIA_BO_LOCKED plan
(ONE breakeven move at +0.75R, 20:45 UTC session flatten, no fixed TP).
The strategy module runs EXACTLY as delivered; everything the guide places
outside it is tested against the guide.
"""
from __future__ import annotations

from datetime import datetime as _dt, timezone as _tz

import numpy as np
import pandas as pd

from app.risk.strategy_rules import (halve_if_in_profit_protect, ids_for_group,
                                     ids_for_lock_group, rules_for)
from app.strategies.gold_asia_range_bo import GoldAsiaRangeBOStrategy

SID = "strategy_gold_asia_range_bo"


# ---------------------------------------------------------------------------
# data: a steady D1 uptrend on XAUUSD (D1 SMA50/200 with NO ADX, >=100 D1 bars
# -> ~11k 15M bars), Asia window quiet, final morning shaped per test
# ---------------------------------------------------------------------------
def _asia_history(end: str, drift: float = 0.003, flatten_pre_break: bool = True,
                  break_close: float = None, flat_asia: bool = False):
    days = 115
    n = days * 96
    idx = pd.date_range(end=end, periods=n, freq="15min")
    day_k = np.arange(n) // 96
    close = 2000.0 * (1 + drift * day_k) + np.sin(np.arange(n) / 40) * 1.2
    rng = np.abs(np.random.default_rng(11).normal(2.0, 0.6, n))

    if drift == 0.0:
        # no-trend fixture: pin the last 8 D1 closes to K between the D1
        # SMA50 and SMA200 (converged on the MODULE's own 22:00-UTC resample),
        # so neither trend alignment holds -> ddir 0 -> no trade, no ADX.
        from app.strategies.gold_asia_range_bo import _resample, _to_utc_frame
        for _ in range(12):
            df_i = pd.DataFrame({"open": close, "high": close, "low": close,
                                 "close": close}, index=idx)
            d1 = _resample(_to_utc_frame(df_i, 0.0), "1D", offset="22h")
            sma50 = d1["close"].rolling(50, min_periods=50).mean().iloc[-2]
            sma200 = d1["close"].rolling(200, min_periods=100).mean().iloc[-2]
            k = (float(sma50) + float(sma200)) / 2.0
            close[-8 * 96:] = k

    o = np.roll(close, 1)
    o[0] = close[0]
    hi = np.maximum(o, close) + rng / 2
    lo = np.minimum(o, close) - rng / 2
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": close,
                       "volume": 100}, index=idx)

    last_day = str(idx[-1].date())
    asia_m = (df.index >= f"{last_day} 00:00") & (df.index < f"{last_day} 06:00")
    ah, al = float(df["high"][asia_m].max()), float(df["low"][asia_m].min())

    if flat_asia:
        # razor-thin Asian range (for the min-risk rule)
        base = (ah + al) / 2.0
        for col, v in (("open", base - 0.005), ("close", base - 0.005),
                       ("high", base + 0.004), ("low", base - 0.004)):
            df.loc[asia_m, col] = v
        ah = al = base + 0.004

    if flatten_pre_break:
        # keep the in-window bars before the break strictly inside the range
        pre = (df.index >= f"{last_day} 06:00") & (df.index < idx[-1])
        df.loc[pre, "open"] = al + 0.3
        df.loc[pre, "close"] = al + 0.4
        df.loc[pre, "high"] = al + 0.6
        df.loc[pre, "low"] = al + 0.2
        ah = float(df["high"][asia_m].max())
        al = float(df["low"][asia_m].min())

    if break_close is not None:
        j = idx[-1]
        c = float(break_close)
        df.loc[j] = {"open": c - 1.0, "high": c + 0.5,
                     "low": min(al, c - 2.0), "close": c, "volume": 100}
    return df, ah, al


def _buy_df():
    # last bar 08:00 UTC closes above the Asian high
    df, ah, al = _asia_history("2026-11-03 08:00")
    df.loc[df.index[-1], "close"] = ah + 3.0
    df.loc[df.index[-1], "high"] = ah + 3.5
    df.loc[df.index[-1], "open"] = ah + 2.0
    return df, ah, al


# ---------------------------------------------------------------------------
# rules: fire + exact levels (guide section 3)
# ---------------------------------------------------------------------------
def test_asia_fires_buy_with_exact_levels():
    df, ah, al = _buy_df()
    cand = GoldAsiaRangeBOStrategy().detect_signal(df, "XAUUSD", "15M")
    assert cand is not None and cand.direction == "BUY"
    assert abs(cand.sl - al) < 1e-9              # SL = Asian low (stop_mode=opp)
    assert abs(cand.tps[0] - cand.entry) == 20.0 * abs(cand.entry - cand.sl)  # 20R placeholder
    e = cand.extra
    assert e["management_plan"] == "GOLD_ASIA_BO_LOCKED"
    assert e["be_trigger_r"] == 0.75 and e["be_lock_r"] == 0.1
    assert e["session_close_utc"] == "20:45" and e["no_fixed_tp"] is True
    assert e["friday_flatten_utc"] == "20:45"
    assert cand.strategy_version == "1.0" and cand.score == 50


def test_asia_fires_sell_in_downtrend_with_sl_at_asian_high():
    df, ah, al = _asia_history("2026-11-03 08:00", drift=-0.003)
    df.loc[df.index[-1], "close"] = al - 3.0
    df.loc[df.index[-1], "low"] = al - 3.5
    df.loc[df.index[-1], "open"] = al - 2.0
    df.loc[df.index[-1], "high"] = ah
    cand = GoldAsiaRangeBOStrategy().detect_signal(df, "XAUUSD", "15M")
    assert cand is not None and cand.direction == "SELL"
    assert abs(cand.sl - ah) < 1e-9              # SL = Asian high


def test_asia_respects_the_entry_window():
    # a break close at 16:15 UTC is OUTSIDE 06:00-16:00 -> no signal
    df, ah, al = _asia_history("2026-11-03 16:15")
    df.loc[df.index[-1], "close"] = ah + 3.0
    df.loc[df.index[-1], "high"] = ah + 3.5
    assert GoldAsiaRangeBOStrategy().detect_signal(df, "XAUUSD", "15M") is None
    # and nothing fires when the day never reaches 06:00
    df2, ah2, _ = _asia_history("2026-11-03 04:45")
    df2.loc[df2.index[-1], "close"] = ah2 + 3.0
    assert GoldAsiaRangeBOStrategy().detect_signal(df2, "XAUUSD", "15M") is None


def test_asia_needs_the_d1_trend_no_adx():
    # sideways D1 (no drift): neither close>SMA50>SMA200 nor the short
    # alignment holds -> ddir 0 -> NO trade even on a clean breakout
    df, ah, al = _asia_history("2026-11-03 08:00", drift=0.0)
    df.loc[df.index[-1], "close"] = ah + 3.0
    df.loc[df.index[-1], "high"] = ah + 3.5
    s = GoldAsiaRangeBOStrategy()
    st = s.compute(df, s.get_parameters())
    assert float(st["ddir"].iloc[-1]) == 0.0
    assert s.detect_signal(df, "XAUUSD", "15M") is None
    # sanity: the SAME breakout in a real uptrend DOES fire
    df2, _, _ = _buy_df()
    assert GoldAsiaRangeBOStrategy().detect_signal(df2, "XAUUSD", "15M") is not None


def test_asia_consumes_the_day_after_one_break():
    s = GoldAsiaRangeBOStrategy()
    df, ah, al = _buy_df()
    add = df.iloc[[-1]].copy()
    add.index = [df.index[-1] + pd.Timedelta(minutes=15)]   # 08:15 UTC
    add["open"] = float(df["close"].iloc[-1])
    add["close"] = float(df["close"].iloc[-1]) + 1.0
    add["high"] = float(add["close"].iloc[0]) + 0.5
    add["low"] = float(add["open"].iloc[0]) - 0.5
    df2 = pd.concat([df, add])
    st = s.compute(df2, s.get_parameters())
    day = df2.index[-1].date()
    day_mask = df2.index.date == day
    assert int(st["signal"][day_mask].sum()) == 1   # first break consumed the day
    assert int(st["signal"].iloc[-2]) == 1


def test_asia_min_risk_rejects_a_razor_thin_range():
    # range depth ~0.008 -> break ~0.046 above it: |entry-sl| < 0.08 -> skip
    df, ah, al = _asia_history("2026-11-03 08:00", flat_asia=True,
                               flatten_pre_break=False)
    df.loc[df.index[-1]] = {"open": ah - 0.01, "high": ah + 0.05,
                            "low": al, "close": ah + 0.046, "volume": 100}
    assert abs((ah + 0.046) - al) < 0.08
    assert GoldAsiaRangeBOStrategy().detect_signal(df, "XAUUSD", "15M") is None


def test_asia_is_xauusd_only():
    df, _, _ = _buy_df()
    assert GoldAsiaRangeBOStrategy().markets == ["XAUUSD"]
    assert GoldAsiaRangeBOStrategy().detect_signal(df, "EURUSD", "15M") is None


def test_asia_asian_range_is_the_manual_mark():
    df, ah, al = _buy_df()
    s = GoldAsiaRangeBOStrategy()
    st = s.compute(df, s.get_parameters())
    assert abs(float(st["asia_high"].iloc[-1]) - ah) < 1e-9
    assert abs(float(st["asia_low"].iloc[-1]) - al) < 1e-9


# ---------------------------------------------------------------------------
# account-level rules (guide section 5) + the shared gold lock (section 6)
# ---------------------------------------------------------------------------
def test_asia_rule_set_and_shared_gold_lock():
    r = rules_for(SID)
    assert r["risk_usd"] == 50.0                 # 1.0% - NOT the pair's 75
    assert r["risk_halving_at_target_frac"] == 0.7
    assert r["max_open_positions"] == 1 and r["max_spread_usd"] == 0.50
    assert r["soft_stop_group"] == "gold_asia"   # own daily counter
    assert r["daily_soft_stop_usd"] == 150.0 and r["daily_max_losses"] == 2
    assert r["scan_bars"] >= 20000               # guide section 8: ~22k better
    assert r["position_lock_group"] == "gold_xau"
    # section 6: ONE gold position at a time across ALL gold strategies
    assert set(ids_for_lock_group("gold_xau")) == {
        "strategy_gold_ny_pullback", "strategy_gold_ny_orb", SID}
    for gsid in ("strategy_gold_ny_pullback", "strategy_gold_ny_orb"):
        assert rules_for(gsid)["position_lock_group"] == "gold_xau"
    assert ids_for_group("gold_asia") == [SID]
    assert rules_for("strategy_mrms_scalper") == {}   # FX untouched


def test_asia_halving_math():
    assert halve_if_in_profit_protect(50, 5280, 5000, 8.0, 0.7) == 25.0
    assert halve_if_in_profit_protect(50, 5100, 5000, 8.0, 0.7) == 50.0


def test_gold_lock_gate_blocks_cross_strategy_entries(monkeypatch, tmp_path):
    import os
    os.environ.setdefault("FXM_DATA_DIR", str(tmp_path))
    from app.db.store import LocalStore
    from app.execution import mt5 as X

    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("signals", {"signal_id": "SIG-NY1",
                             "strategy_id": "strategy_gold_ny_pullback"})
    store.create("signals", {"signal_id": "SIG-FX1",
                             "strategy_id": "strategy_mrms_scalper"})
    sig = {"signal_id": "SIG-AS1", "strategy_id": SID}
    sr = rules_for(SID)

    # an open NEW-YORK gold trade blocks GOLDASIA (section 6 shared lock)
    def bridge_gold(path, timeout=8):
        if "/positions" in path:
            return {"positions": [{"comment": "SIG-NY1"}]}
        return {"bid": 2000.0, "ask": 2000.2}

    monkeypatch.setattr(X, "bridge_get", bridge_gold)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "MAX_OPEN_POSITIONS" and "gold" in why

    # an open FX trade does NOT block GOLDASIA
    def bridge_fx(path, timeout=8):
        if "/positions" in path:
            return {"positions": [{"comment": "SIG-FX1"}]}
        return {"bid": 2000.0, "ask": 2000.2}

    monkeypatch.setattr(X, "bridge_get", bridge_fx)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert ok, (code, why)


# ---------------------------------------------------------------------------
# GOLD_ASIA_BO_LOCKED plan (guide section 4) - through the shared dispatch
# ---------------------------------------------------------------------------
def _mgr_with(monkeypatch, ops):
    from app.aimanager import engine as E
    import app.aimanager.risk_gate as rg

    def fake_plan(user_id, pos, plan, reason):
        ops.append((next(iter(plan)), reason,
                    float(plan.get("modify_sl") or 0.0)))
        return {"executed": True}

    monkeypatch.setattr(rg, "execute_plan", fake_plan)
    mgr = E.TradeManager.__new__(E.TradeManager)
    mgr._state = {}
    return mgr


def _asia_pos(price=2807.5, sl=2790.0):
    # BUY opened 2800, Asian-low stop 2790 -> risk 10.0
    return {"ticket": 7, "type": "BUY", "price_open": 2800.0,
            "price_current": price, "sl": sl, "profit": 10.0,
            "app_market": "XAUUSD", "comment": "SIG-AS1"}


def _asia_sig(entry_day="2026-11-03", **extra):
    return {"strategy_id": SID, "management_plan": "GOLD_ASIA_BO_LOCKED",
            "be_trigger_r": 0.75, "be_lock_r": 0.1, "session_close_utc": "20:45",
            "mt5_executed_at": f"{entry_day}T08:00:30+00:00", **extra}


def _at(y, m, d, hh, mm):
    return _dt(y, m, d, hh, mm, tzinfo=_tz.utc)


def test_asia_be_move_once_at_075r(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 12, 0)     # midday, window open
    out = mgr._gold_locked_manage("u1", _asia_pos(price=2807.5), _asia_sig())
    assert out["closed"] is False
    assert ops and ops[0][0] == "modify_sl" and "BE" in ops[0][1]
    assert abs(ops[0][2] - 2801.0) < 1e-9               # 2800 + 0.1 x 10
    # broker reflects the move: second tick does NOT re-send (once, no trail)
    ops.clear()
    out = mgr._gold_locked_manage("u1", _asia_pos(price=2809.0, sl=2801.0),
                                  _asia_sig())
    assert ops == [] and out["closed"] is False


def test_asia_be_target_is_entry_plus_lock_r(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 12, 0)
    mgr._gold_locked_manage("u1", _asia_pos(price=2807.5), _asia_sig())
    assert ops and ops[0][0] == "modify_sl"
    assert abs(ops[0][2] - 2801.0) < 1e-9               # 2800 + 0.1*10


def test_asia_holds_below_the_be_trigger(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 12, 0)
    out = mgr._gold_locked_manage("u1", _asia_pos(price=2804.0),  # +0.4R
                                  _asia_sig())
    assert out["closed"] is False and ops == []


def test_asia_ignores_the_20r_placeholder_tp(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 12, 0)
    # price deep past the fake 20R: no TP backstop close may fire
    out = mgr._gold_locked_manage("u1", _asia_pos(price=3100.0), _asia_sig())
    assert out["closed"] is False
    assert not any(op == "close_full" for op, _, _v in ops)   # placeholder ignored


def test_asia_session_flatten_at_2045_utc_and_friday(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 20, 50)     # Tuesday evening
    out = mgr._gold_locked_manage("u1", _asia_pos(price=2802.0), _asia_sig())
    assert out["closed"] is True and ops[0][0] == "close_full"
    assert "session close" in ops[0][1]

    ops.clear()
    mgr2 = _mgr_with(monkeypatch, ops)
    mgr2._now_utc = lambda: _at(2026, 11, 6, 20, 46)    # Friday evening
    out = mgr2._gold_locked_manage("u1", _asia_pos(price=2802.0), _asia_sig())
    assert out["closed"] is True and ops[0][0] == "close_full"

    # 20:44 - still holding
    ops.clear()
    mgr3 = _mgr_with(monkeypatch, ops)
    mgr3._now_utc = lambda: _at(2026, 11, 3, 20, 44)
    out = mgr3._gold_locked_manage("u1", _asia_pos(price=2802.0), _asia_sig())
    assert out["closed"] is False and ops == []


def test_asia_overnight_backstop(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 4, 10, 0)      # next morning
    out = mgr._gold_locked_manage("u1", _asia_pos(price=2802.0),
                                  _asia_sig(entry_day="2026-11-03"))
    assert out["closed"] is True and "overnight" in ops[0][1]


def test_asia_be_moves_down_for_shorts(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    mgr._now_utc = lambda: _at(2026, 11, 3, 12, 0)
    pos = {"ticket": 8, "type": "SELL", "price_open": 2800.0,
           "price_current": 2792.5, "sl": 2810.0, "profit": 10.0,
           "app_market": "XAUUSD", "comment": "SIG-AS1"}
    out = mgr._gold_locked_manage("u1", pos, _asia_sig())
    assert out["closed"] is False and ops[0][0] == "modify_sl"
    assert abs(ops[0][2] - 2799.0) < 1e-9               # 2800 - 0.1*10


# ---------------------------------------------------------------------------
# registry + lifecycle contract (guide section 2)
# ---------------------------------------------------------------------------
def test_asia_registry_and_paused_docs():
    from app.strategies import OWNER_STRATEGY_IDS, STRATEGY_CLASSES
    assert SID in STRATEGY_CLASSES and SID in set(OWNER_STRATEGY_IDS)
    assert len(OWNER_STRATEGY_IDS) == 6
    from app.learning.versions import START_PAUSED, lifecycle_of
    assert SID in START_PAUSED                # boots PAUSED (guide section 2)
    assert lifecycle_of(SID) == "LIVE"        # owner activation = trading
    s = GoldAsiaRangeBOStrategy()
    assert s.id == SID and s.short_name == "GOLDASIA" and s.version == "1.0"
    assert s.timeframes == ["15M"] and s.markets == ["XAUUSD"]
