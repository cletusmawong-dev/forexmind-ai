"""The owner's gold pair (implementation guide 2026-11-02): rule tests,
account-level gates, sizing override + halving, and the locked management
plans. The strategy modules run EXACTLY as delivered; everything the guide
places outside them (risk rules, plans) is tested against the guide.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.risk.strategy_rules import (halve_if_in_profit_protect, ids_for_group,
                                     rules_for)
from app.strategies.gold_ny_orb import GoldNYORBStrategy
from app.strategies.gold_ny_pullback import GoldNYPullbackStrategy


# ---------------------------------------------------------------------------
# data: a steady D1 uptrend on XAUUSD (regime needs SMA50/200 + ADX>=20 over
# >=100 D1 bars -> ~11k 15M bars), with the final session shaped per setup
# ---------------------------------------------------------------------------
def _gold_history(setup: str, end: str):
    days = 115
    n = days * 96
    idx = pd.date_range(end=end, periods=n, freq="15min")
    day_k = np.arange(n) // 96
    close = 2000.0 * (1 + 0.003 * day_k) + np.sin(np.arange(n) / 40) * 1.2
    o = np.roll(close, 1)
    o[0] = close[0]
    rng = np.abs(np.random.default_rng(11).normal(2.0, 0.6, n))
    hi = np.maximum(o, close) + rng / 2
    lo = np.minimum(o, close) - rng / 2
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": close,
                       "volume": 100}, index=idx)
    day_mask = df.index >= (df.index[-1].normalize() + pd.Timedelta(hours=12))
    last = df.index[-1]

    if setup == "pullback":
        # final 6 bars: flat 15:30/15:45 (keeps the last COMPLETED H1 bar
        # closing above its EMA20), 3-bar pullback 16:00-16:30 below EMA9
        # (lows through EMA21), then a strong reclaim close at 16:45
        p = float(close[-6])
        closes = [p, p, p - 3, p - 6, p - 9, p + 1]
        opens = [closes[0] - 0.5, closes[1] - 0.5, closes[0] + 0.5,
                 closes[1] + 0.5, closes[2] + 0.5, closes[3] + 0.5]
        lows = [min(opens[k], closes[k]) - 1.0 for k in range(6)]
        highs = [max(opens[k], closes[k]) + 0.8 for k in range(6)]
    else:  # orb
        # 13:30/13:45 build the range; 14:00 breaks out above it
        p = float(close[-3])
        bars = [(p + 1, p + 4, p - 2, p + 3),      # 13:30 o h l c
                (p + 3, p + 4.5, p - 1, p + 1.5),  # 13:45
                (p + 2, p + 11, p + 1.5, p + 10)]  # 14:00 breakout
        opens = [b[0] for b in bars]
        highs = [b[1] for b in bars]
        lows = [b[2] for b in bars]
        closes = [b[3] for b in bars]

    vals = df.iloc[:-len(closes)].copy()
    add = pd.DataFrame({"open": opens, "high": highs, "low": lows,
                        "close": closes, "volume": 100},
                       index=idx[-len(closes):])
    out = pd.concat([vals, add])
    out = out[~out.index.duplicated(keep="last")]
    return out


def _pb_df():
    return _gold_history("pullback", "2026-11-03 16:45")


def _orb_df():
    return _gold_history("orb", "2026-11-03 14:00")


# ---------------------------------------------------------------------------
# rules: fire + exact levels
# ---------------------------------------------------------------------------
def test_pullback_fires_buy_in_uptrend_session():
    cand = GoldNYPullbackStrategy().detect_signal(_pb_df(), "XAUUSD", "15M")
    assert cand is not None and cand.direction == "BUY"
    assert cand.extra["management_plan"] == "GOLD_NY_PULLBACK_LOCKED"
    assert cand.extra["max_hold_bars"] == 32
    assert cand.extra["max_open_positions"] == 1
    st = GoldNYPullbackStrategy().compute(_pb_df(),
                                          GoldNYPullbackStrategy().get_parameters())
    i = len(_pb_df()) - 1
    atr = float(st["atr"].iloc[i])
    swing_low = float(_pb_df()["low"].iloc[-5:].min())
    assert abs(cand.sl - (swing_low - 0.1 * atr)) < 1e-6     # rule 8 exactly
    assert abs(cand.tps[0] - cand.entry) == 3.0 * abs(cand.entry - cand.sl)
    assert cand.strategy_version == "1.0" and cand.timestamp


def test_pullback_respects_the_session_window():
    # same shape but the day ends at 12:45 - no bar in 13:00-17:00
    df = _gold_history("pullback", "2026-11-03 12:45")
    assert GoldNYPullbackStrategy().detect_signal(df, "XAUUSD", "15M") is None


def test_orb_fires_buy_breaking_the_opening_range():
    cand = GoldNYORBStrategy().detect_signal(_orb_df(), "XAUUSD", "15M")
    assert cand is not None and cand.direction == "BUY"
    assert cand.extra["management_plan"] == "GOLD_NY_ORB_LOCKED"
    assert cand.extra["max_hold_bars"] == 24
    st = GoldNYORBStrategy().compute(_orb_df(),
                                     GoldNYORBStrategy().get_parameters())
    i = len(_orb_df()) - 1
    orh, orl = float(st["or_high"].iloc[i]), float(st["or_low"].iloc[i])
    atr = float(st["atr"].iloc[i])
    assert orh > orl
    assert abs(cand.sl - max(orl, cand.entry - 1.2 * atr)) < 1e-6  # rule 5
    assert abs(cand.tps[0] - cand.entry) == 2.0 * abs(cand.entry - cand.sl)


def test_orb_range_sanity_rejects_a_wild_range():
    df = _orb_df()
    # blow the 13:30 bar's range far beyond 2.8 ATR
    big = df.index[-3]
    df.loc[big, "high"] = df.loc[big, "close"] + 30
    df.loc[big, "low"] = df.loc[big, "close"] - 30
    assert GoldNYORBStrategy().detect_signal(df, "XAUUSD", "15M") is None


def test_orb_consumes_the_day_after_one_break():
    s = GoldNYORBStrategy()
    df = _orb_df()
    # add one more breakout bar AFTER the consumed break (14:15 UTC)
    add = df.iloc[[-1]].copy()
    add.index = [df.index[-1] + pd.Timedelta(minutes=15)]
    add["open"] = float(df["close"].iloc[-1])
    add["close"] = float(df["close"].iloc[-1]) + 2
    add["high"] = add["close"] + 1
    add["low"] = add["open"] - 1
    df2 = pd.concat([df, add])
    # the 14:00 break consumed the day: state carries NO new signal at
    # 14:15 (the earlier bar's signal remains - creation-time dedupe and
    # the one-open-position gate stop it from trading twice)
    st = s.compute(df2, s.get_parameters())
    assert int(st["signal"].iloc[-1]) == 0
    assert int(st["signal"].iloc[-2]) == 1
    assert s.detect_signal(df2.iloc[:-1], "XAUUSD", "15M") is not None


def test_gold_pair_is_xauusd_only():
    df = _pb_df()
    assert GoldNYPullbackStrategy().detect_signal(df, "EURUSD", "15M") is None
    assert GoldNYORBStrategy().detect_signal(_orb_df(), "GBPUSD", "15M") is None


# ---------------------------------------------------------------------------
# account-level rules (guide section 5) - data, never inside the modules
# ---------------------------------------------------------------------------
def test_rule_sets_and_shared_counter():
    pb, orb = rules_for("strategy_gold_ny_pullback"), rules_for("strategy_gold_ny_orb")
    for r in (pb, orb):
        assert r["risk_usd"] == 75.0 and r["max_open_positions"] == 1
        assert r["max_spread_usd"] == 0.50 and r["daily_soft_stop_usd"] == 150.0
        assert r["daily_max_losses"] == 2 and r["scan_bars"] >= 10000
    assert pb["soft_stop_group"] == orb["soft_stop_group"] == "gold_ny"
    assert set(ids_for_group("gold_ny")) == {"strategy_gold_ny_pullback",
                                             "strategy_gold_ny_orb"}
    assert rules_for("strategy_mrms_scalper") == {}     # others untouched


def test_halving_math():
    assert halve_if_in_profit_protect(75, 5280, 5000, 8.0, 0.7) == 37.5
    assert halve_if_in_profit_protect(75, 5100, 5000, 8.0, 0.7) == 75.0
    assert halve_if_in_profit_protect(75, 0, 0, 0, 0) == 75.0


def test_entry_gates(monkeypatch, tmp_path):
    import os
    os.environ.setdefault("FXM_DATA_DIR", str(tmp_path))
    from app.db.store import LocalStore
    from app.execution import mt5 as X
    today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")

    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("signals", {"signal_id": "SIG-G1", "userId": "u1",
                             "strategy_id": "strategy_gold_ny_pullback"})
    store.create("signals", {"signal_id": "SIG-F1", "userId": "u1",
                             "strategy_id": "strategy_mrms_scalper",
                             "day": today, "completed": True, "mt5_pl": -80.0})
    sig = {"signal_id": "SIG-G9", "strategy_id": "strategy_gold_ny_pullback"}
    sr = rules_for("strategy_gold_ny_pullback")

    # no positions, tight quote, no completed pair trades today -> pass
    def bridge_ok(path, timeout=8):
        if "/positions" in path:
            return {"positions": [{"comment": "SIG-FXOTHER"}]}
        if "/quote" in path:
            return {"bid": 2000.0, "ask": 2000.2}
        return {}

    monkeypatch.setattr(X, "bridge_get", bridge_ok)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert ok, (code, why)

    # (a) one open position of the same strategy -> refuse
    def bridge_pos(path, timeout=8):
        if "/positions" in path:
            return {"positions": [{"comment": "SIG-G1"}]}
        return {"bid": 2000.0, "ask": 2000.2}

    monkeypatch.setattr(X, "bridge_get", bridge_pos)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "MAX_OPEN_POSITIONS"

    # (b) spread cap
    def bridge_spread(path, timeout=8):
        if "/positions" in path:
            return {"positions": []}
        return {"bid": 2000.0, "ask": 2000.8}

    monkeypatch.setattr(X, "bridge_get", bridge_spread)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "SPREAD_TOO_WIDE"

    # (b2) unusable quote -> fail-closed
    def bridge_dead(path, timeout=8):
        if "/positions" in path:
            return {"positions": []}
        return {"bid": 0, "ask": 0}

    monkeypatch.setattr(X, "bridge_get", bridge_dead)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "QUOTE_UNUSABLE"

    # (c1) 2 pair losses today (2 x -$70 = -$140, P&L wall NOT breached)
    #      -> daily loss cap; FX losses don't count
    d1 = store.create("signals", {"signal_id": "SIG-G2", "userId": "u1",
                                  "strategy_id": "strategy_gold_ny_orb",
                                  "day": today,
                                  "completed": True, "mt5_pl": -70.0})
    d2 = store.create("signals", {"signal_id": "SIG-G3", "userId": "u1",
                                  "strategy_id": "strategy_gold_ny_orb",
                                  "day": today, "completed": True,
                                  "mt5_pl": -70.0})

    def bridge_flat(path, timeout=8):
        if "/positions" in path:
            return {"positions": []}
        return {"bid": 2000.0, "ask": 2000.2}

    monkeypatch.setattr(X, "bridge_get", bridge_flat)
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "DAILY_MAX_LOSSES", (code, why)

    # (c2) one -160 loss -> under the loss count, over the -$150 P&L wall
    store.delete("signals", d1["id"])
    store.delete("signals", d2["id"])
    store.create("signals", {"signal_id": "SIG-G4", "userId": "u1",
                             "strategy_id": "strategy_gold_ny_orb",
                             "day": today, "completed": True,
                             "mt5_pl": -160.0})
    ok, code, why = X._strategy_entry_gates(store, "u1", sig, "XAUUSD", sr)
    assert not ok and code == "DAILY_SOFT_STOP"


# ---------------------------------------------------------------------------
# locked management plans (guide section 4)
# ---------------------------------------------------------------------------
def _mgr_with(monkeypatch, executed_ops):
    from app.aimanager import engine as E
    import app.aimanager.risk_gate as rg

    def fake_plan(user_id, pos, plan, reason):
        executed_ops.append((next(iter(plan)), reason))
        return {"executed": True}

    monkeypatch.setattr(rg, "execute_plan", fake_plan)
    mgr = E.TradeManager.__new__(E.TradeManager)
    mgr._state = {}
    return mgr


def _gold_pos(price=2900.0):
    return {"ticket": 1, "type": "BUY", "price_open": 2800.0,
            "price_current": price, "sl": 2790.0, "profit": 10.0,
            "app_market": "XAUUSD", "comment": "SIG-G9"}


def _gold_sig(**extra):
    return {"strategy_id": "strategy_gold_ny_orb", "tp1": 2910.0,
            "mt5_executed_at": "2026-11-03T14:00:30+00:00", **extra}


def test_locked_plan_never_moves_the_stop_and_backstops_tp(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    out = mgr._gold_locked_manage("u1", _gold_pos(price=2911.0), _gold_sig())
    assert out["closed"] is True
    assert ops == [("close_full", ops[0][1])]          # ONE op: the close
    assert "TP" in ops[0][1] or "tp" in ops[0][1]
    assert not any(op == "modify_sl" for op, _ in ops)  # SL NEVER moved


def test_locked_plan_time_exit_counts_bars(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    idx = pd.date_range(end="2026-11-03 21:00", periods=30, freq="15min",
                        tz="UTC")
    df = pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": 1,
                       "volume": 1}, index=idx)

    class P:
        def get_candles(self, market, tf, limit=200):
            return df

    mgr.provider = P()
    sig = _gold_sig(max_hold_bars=5)     # 5 completed bars -> exit
    out = mgr._gold_locked_manage("u1", _gold_pos(price=2850.0), sig)
    assert out["closed"] is True
    assert any("time exit" in r for _, r in ops)
    assert not any(op == "modify_sl" for op, _ in ops)


def test_locked_plan_holds_below_the_time_exit(monkeypatch):
    ops = []
    mgr = _mgr_with(monkeypatch, ops)
    idx = pd.date_range(end="2026-11-03 15:30", periods=10, freq="15min",
                        tz="UTC")
    df = pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": 1,
                       "volume": 1}, index=idx)

    class P:
        def get_candles(self, market, tf, limit=200):
            return df

    mgr.provider = P()
    out = mgr._gold_locked_manage("u1", _gold_pos(price=2850.0), _gold_sig())
    assert out["closed"] is False and ops == []          # hold: no actions


# ---------------------------------------------------------------------------
# registry + lifecycle contract (guide section 2)
# ---------------------------------------------------------------------------
def test_registry_and_paused_docs():
    from app.strategies import OWNER_STRATEGY_IDS, STRATEGY_CLASSES
    assert {"strategy_gold_ny_pullback", "strategy_gold_ny_orb"} <= \
        set(STRATEGY_CLASSES)
    assert len(OWNER_STRATEGY_IDS) == 5
    from app.learning.versions import START_PAUSED, lifecycle_of
    for sid in ("strategy_gold_ny_pullback", "strategy_gold_ny_orb"):
        assert sid in START_PAUSED            # boots PAUSED (guide section 2)
        assert lifecycle_of(sid) == "LIVE"    # owner activation = trading
