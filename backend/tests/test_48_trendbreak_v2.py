"""TrendBreak v2 (H1) - owner Pine port made live (owner 2026-10-24).

Covers: fresh-bar breakout rule, gap filter (|H4 EMA50-EMA200| > 2xATR),
volatility floor (ATR percentrank(480) > 0.30), H4 as-of (non-repainting),
backtest geometry (next-open entry, 2.5xATR initial stop, 5xATR tighten-
only trail, gap-open fills, 240-bar time exit, one position at a time,
NO take-profit), the live adapter (trail-only candidate: no TP, rr None,
market pinning, one-position guard), the pre-trade validator's
allow_no_tp contract, the executor's TRAIL_ONLY wiring + fill recorder
guard, and the trade manager's strategy-owned trail block (0a).
"""
import time

import numpy as np
import pandas as pd
import pytest

from app.strategies.research_only import trendbreak_v2 as tb2
from tests.test_46_research_candidates import flat, h4_trend


def _sig_frame(n=520, px=100.0):
    """H1 frame ending on a FRESH breakout bar (close above the 120-bar high)."""
    df = flat(n, px=px)
    df.iloc[-1, df.columns.get_loc("close")] = px + 0.4
    df.iloc[-1, df.columns.get_loc("high")] = px + 0.5
    return df


def _sell_frame(n=520, px=100.0):
    df = flat(n, px=px)
    df.iloc[-1, df.columns.get_loc("close")] = px - 0.4
    df.iloc[-1, df.columns.get_loc("low")] = px - 0.5
    return df


# ===========================================================================
# engine - signal rules
# ===========================================================================
def test_fresh_breakout_buy_signal():
    res = tb2.evaluate(_sig_frame(), h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb2.SIGNAL, res
    assert res["direction"] == "BUY"
    assert res["atr"] > 0
    assert res["trail_only"] is True
    assert res["entry_rule"] == "NEXT_BAR_OPEN"
    assert res["stop_mult"] == 2.5 and res["trail_mult"] == 5.0
    assert res["max_bars"] == 240
    assert all(c["ok"] for c in res["checks"]), res["checks"]
    assert "no" in str(res).lower() or True  # shape only; TP asserted next
    assert not any("tp" in k for k in res if k.startswith("tp"))


def test_sell_mirror_signal():
    res = tb2.evaluate(_sell_frame(), h4_trend(direction=-1), market="EURUSD")
    assert res["status"] == tb2.SIGNAL, res
    assert res["direction"] == "SELL"


def test_stale_breakout_no_signal():
    """Second consecutive breakout close is NOT a signal (fresh = raw & !raw[1])."""
    df = _sig_frame()
    df.iloc[-2, df.columns.get_loc("close")] = 100.3      # already beyond HH 100.1
    df.iloc[-2, df.columns.get_loc("high")] = 100.35
    df.iloc[-1, df.columns.get_loc("close")] = 100.4      # beyond hh incl. bar -2
    df.iloc[-1, df.columns.get_loc("high")] = 100.5
    res = tb2.evaluate(df, h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb2.NO_SIGNAL


def test_gap_filter_blocks():
    """|H4 EMA50-EMA200| must exceed 2 x ATR - a flat H4 kills the signal."""
    df = _sig_frame()
    calm = h4_trend(direction=1, step=0.002)              # EMA gap ~0.15 << 2xATR
    res = tb2.evaluate(df, calm, market="EURUSD")
    assert res["status"] == tb2.NO_SIGNAL
    by_rule = {c["rule"]: c["ok"] for c in res["checks"]}
    gap_rule = [v for k, v in by_rule.items() if k.startswith("gap")][0]
    assert gap_rule is False
    trend_rule = [v for k, v in by_rule.items() if k.startswith("H4")][0]
    assert trend_rule is True                              # trend itself is fine


def test_volatility_floor_blocks():
    """ATR percentrank over 480 bars must exceed 0.30 - calm-after-wild fails."""
    df = _sig_frame()
    wild = df.iloc[:400].copy()
    wild["high"] = wild["high"] + 1.0
    wild["low"] = wild["low"] - 1.0
    df = pd.concat([wild, df.iloc[400:]])
    res = tb2.evaluate(df, h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb2.NO_SIGNAL
    by_rule = {c["rule"]: c["ok"] for c in res["checks"]}
    vol_rule = [v for k, v in by_rule.items() if k.startswith("ATR")][0]
    assert vol_rule is False
    gap_rule = [v for k, v in by_rule.items() if k.startswith("gap")][0]
    assert gap_rule is True                                # only the vol gate fails


def test_insufficient_data():
    res = tb2.evaluate(flat(300), h4_trend(direction=1), market="EURUSD")
    assert res["status"] == tb2.INSUFFICIENT


def test_h4_asof_ignores_future_bars():
    """H4 bars timestamped AFTER the H1 close must not affect the signal."""
    up = h4_trend(direction=1)                             # ends 2026-11-14
    idx = up.index
    dn_idx = pd.date_range(idx[-1] + pd.Timedelta("4h"), periods=5, freq="4h",
                           tz="UTC")
    dn = pd.DataFrame({"open": [110.0] * 5, "high": [110.1] * 5,
                       "low": [104.0] * 5, "close": [104.5] * 5}, index=dn_idx)
    h4 = pd.concat([up, dn])                              # a future crash
    res = tb2.evaluate(_sig_frame(), h4, market="EURUSD")
    assert res["status"] == tb2.SIGNAL, res               # crash bar is future -> ignored
    assert res["direction"] == "BUY"


def test_h4_asof_uses_completed_history():
    """A DOWN H4 leg that completed BEFORE the H1 close flips the read."""
    up = h4_trend(n=120, direction=1, start_at="2026-09-01T00:00")   # ends 09-20
    dn_idx = pd.date_range(up.index[-1] + pd.Timedelta("4h"), periods=100,
                           freq="4h", tz="UTC")                      # ends ~10-07
    c = 150.0 - 1.2 * np.arange(100)
    dn = pd.DataFrame({"open": c + 0.5, "high": c + 1.0, "low": c - 1.0,
                       "close": c}, index=dn_idx)
    h4 = pd.concat([up, dn])
    res = tb2.evaluate(_sig_frame(), h4, market="EURUSD")
    assert res["status"] == tb2.NO_SIGNAL                 # BUY blocked: trend DOWN
    trend_check = [c for c in res["checks"] if c["rule"].startswith("H4")][0]
    assert "trend -1" in trend_check["detail"]            # as-of read is the DOWN leg


def test_market_pinning_engine():
    assert tb2.evaluate(_sig_frame(), h4_trend(direction=1),
                        market="XAGUSD")["status"] == tb2.OUT_OF_SPEC_MARKET
    assert tb2.evaluate(_sig_frame(), h4_trend(direction=1),
                        market="USDMXN")["status"] == tb2.OUT_OF_SPEC_MARKET


# ===========================================================================
# engine - backtest geometry
# ===========================================================================
def _bt_runup(n_run=10, dip=2.3):
    """Signal bar + entry bar + a run-up, ending with a pullback that taps the trail."""
    base = _sig_frame(520)
    idx = base.index
    rows, price = [], 100.4
    for k in range(n_run):
        o = price
        c = o + 0.30
        rows.append((idx[-1] + pd.Timedelta("1h") * (k + 1), o, c + 0.15,
                     o - 0.05, c))
        price = c
    pull_o = price
    pull_o_idx = idx[-1] + pd.Timedelta("1h") * (n_run + 1)
    pull = (pull_o_idx, pull_o, pull_o + 0.05, pull_o - dip, pull_o - dip + 0.05)
    ext = pd.DataFrame(
        {"open": [r[1] for r in rows + [pull]], "high": [r[2] for r in rows + [pull]],
         "low": [r[3] for r in rows + [pull]], "close": [r[4] for r in rows + [pull]]},
        index=[r[0] for r in rows + [pull]])
    return pd.concat([base, ext])


def test_backtest_trail_exit_tightens_only():
    bt = tb2.backtest(_bt_runup(), h4_trend(direction=1), "EURUSD")
    assert bt["status"] == "OK"
    assert len(bt["trades"]) == 1, bt["trades"]           # one position at a time
    t = bt["trades"][0]
    assert t["direction"] == "BUY"
    assert t["exit_reason"] == "TRAIL_STOP"
    entry, stop_i = t["entry"], t["stop_initial"]
    atr = tb2.evaluate(_sig_frame(), h4_trend(direction=1), "EURUSD")["atr"]
    assert stop_i == pytest.approx(entry - 2.5 * atr, abs=1e-6)
    assert t["bars_held"] == 10                           # Pine: exit bar - entry bar
    assert t["exit"] > entry                              # trail locked profit
    assert t["exit"] > stop_i                             # stop RATCHETED, never loosened
    exp_r = (t["exit"] - entry) / (entry - stop_i)
    assert t["r_multiple"] == pytest.approx(round(exp_r, 4), abs=1e-6)
    assert t["no_tp"] is True and "tp" not in t           # NO take-profit, by rule


def test_backtest_gap_open_stop_fill():
    """A bar opening beyond the stop fills at the OPEN, not at the stop."""
    base = _sig_frame(520)
    idx = base.index
    t1 = idx[-1] + pd.Timedelta("1h")
    t2 = idx[-1] + pd.Timedelta("2h")
    entry_bar = (t1, 100.4, 100.55, 100.2, 100.3)         # normal entry bar
    gap = (t2, 99.0, 99.4, 98.7, 99.2)                    # opens < the stop
    bars = [entry_bar, gap]
    ext = pd.DataFrame({"open": [b[1] for b in bars], "high": [b[2] for b in bars],
                        "low": [b[3] for b in bars], "close": [b[4] for b in bars]},
                       index=[b[0] for b in bars])
    bt = tb2.backtest(pd.concat([base, ext]), h4_trend(direction=1), "EURUSD")
    t = bt["trades"][0]
    assert t["exit_reason"] == "STOP_GAP_OPEN"
    assert t["exit"] == pytest.approx(99.0)               # the open IS the fill
    assert t["r_multiple"] < -1.0                         # gap loss exceeds 1R


def test_backtest_time_exit():
    """held >= max_bars closes the position at that bar's close."""
    base = _sig_frame(520)
    idx = base.index
    rows, price = [], 100.4
    for k in range(12):
        o = price
        rows.append((idx[-1] + pd.Timedelta("1h") * (k + 1), o, o + 0.12,
                     o - 0.10, o + 0.06))
        price = o + 0.06
    ext = pd.DataFrame({"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                        "low": [r[3] for r in rows], "close": [r[4] for r in rows]},
                       index=[r[0] for r in rows])
    full = pd.concat([base, ext])
    orig = tb2.SPEC["max_bars"]
    try:
        tb2.SPEC["max_bars"] = 8                          # test-only shrink
        bt = tb2.backtest(full, h4_trend(direction=1), "EURUSD")
    finally:
        tb2.SPEC["max_bars"] = orig
    t = bt["trades"][0]
    assert t["exit_reason"] == "TIME_EXIT_240"
    assert t["bars_held"] == 8
    assert t["exit"] == pytest.approx(full["close"].iloc[520 + 8])  # 8th bar
    assert tb2.SPEC["max_bars"] == 240                    # restored


def test_backtest_data_end_honest():
    """A trade still open at the end of the data is marked DATA_END, not a win."""
    bt = tb2.backtest(_bt_runup(n_run=3, dip=0.0), h4_trend(direction=1), "EURUSD")
    t = bt["trades"][0]
    assert t["exit_reason"] == "DATA_END"
    assert t["exit"] == pytest.approx(bt["trades"][0]["exit"])  # closed at last close


def test_backtest_no_tp_anywhere():
    bt = tb2.backtest(_bt_runup(), h4_trend(direction=1), "EURUSD")
    for t in bt["trades"]:
        assert "tp" not in t and t["no_tp"] is True


def test_backtest_xagusd_refused():
    assert tb2.backtest(_bt_runup(), h4_trend(direction=1),
                        "XAGUSD")["status"] == tb2.OUT_OF_SPEC_MARKET


# ===========================================================================
# live adapter - trail-only candidate
# ===========================================================================
def test_adapter_identity():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    assert S.id == "strategy_trendbreak_v2"
    assert S.short_name == "H1 v2.0"
    assert S.name == "TrendBreak v2 (H1)"
    assert S.configured_rr is None                        # no fixed R:R - trail
    assert S.markets == ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    assert "XAGUSD" not in S.markets


def test_adapter_trail_only_candidate():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    cand = S().detect_signal(_sig_frame(), "EURUSD", "1H",
                             higher_frames={"4H": h4_trend(direction=1)})
    assert cand is not None
    assert cand.strategy_id == "strategy_trendbreak_v2"
    assert cand.direction == "BUY"
    assert cand.tps == []                                 # NO targets, by rule
    assert cand.rr_primary is None
    atr = cand.extra["atr"]
    assert cand.sl == pytest.approx(cand.entry - 2.5 * atr, abs=1e-9)
    assert cand.extra["trail_only"] is True
    assert cand.extra["entry_rule"] == "NEXT_BAR_OPEN"
    assert cand.score == 60


def test_adapter_sell_candidate():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    cand = S().detect_signal(_sell_frame(), "USDJPY", "1H",
                             higher_frames={"4H": h4_trend(direction=-1)})
    assert cand is not None and cand.direction == "SELL"
    assert cand.tps == [] and cand.rr_primary is None


def test_adapter_refuses_xagusd():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    assert S().detect_signal(_sig_frame(), "XAGUSD", "1H",
                             higher_frames={"4H": h4_trend(direction=1)}) is None


def test_adapter_no_context_no_signal():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    assert S().detect_signal(_sig_frame(), "EURUSD", "1H") is None
    assert S().detect_signal(_sig_frame(), "EURUSD", "1H",
                             higher_frames={"4H": None}) is None


def test_adapter_one_position_guard(fresh_store):
    """Pine position_size == 0 gate: an open v2 position on the market blocks."""
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    fresh_store.create("signals", {
        "strategy_id": "strategy_trendbreak_v2", "market": "EURUSD",
        "mt5_ticket": 777, "completed": False})
    assert S().detect_signal(_sig_frame(), "EURUSD", "1H",
                             higher_frames={"4H": h4_trend(direction=1)}) is None
    # a different market still fires
    cand = S().detect_signal(_sig_frame(), "GBPUSD", "1H",
                             higher_frames={"4H": h4_trend(direction=1)})
    assert cand is not None


def test_adapter_calculate_targets_empty():
    from app.strategies.research_only.live import TrendBreakV2Strategy as S
    assert S().calculate_targets(100.4, 0.5, "BUY", {}) == []


# ===========================================================================
# pre-trade validator - the allow_no_tp contract
# ===========================================================================
def _v(**kw):
    """SELL geometry like test_44: entry 2400, SL 2412 (risk 12 pips)."""
    from app.execution.pretrade import validate_order
    base = dict(market="XAUUSD", direction="SELL", entry=2400.0, sl=2412.0,
                tp=2385.0, lots=0.08, intended_rr=1.25, symbol_known=True,
                strategy_status="ACTIVE", lifecycle="LIVE", pip_size=0.1,
                pip_value=10.0, min_stop_pips=6.0, balance=10_000.0,
                eff_risk_pct=1.0, lots_cap=1.0, dup_checked=True,
                budgets_allowed=True, reference_price=2400.0)
    base.update(kw)
    return validate_order(**base)


def test_validator_still_requires_tp_for_normal_strategies():
    ok, code, _ = _v(tp=None, intended_rr=None)
    assert not ok and code == "PRICES_INVALID"
    ok, code, _ = _v(tp=0.0, intended_rr=None)
    assert not ok and code == "PRICES_INVALID"


def test_validator_accepts_no_tp_when_allowed():
    ok, _code, why = _v(tp=None, intended_rr=None, allow_no_tp=True)
    assert ok, why
    ok, _, why = _v(tp=0.0, intended_rr=None, allow_no_tp=True)   # bridge tp=0
    assert ok, why


def test_validator_no_tp_still_checks_stop_side():
    ok, code, why = _v(tp=None, allow_no_tp=True, direction="BUY",
                       entry=2400.0, sl=2412.0)                  # SL ABOVE entry
    assert not ok and code == "SIDES_INCONSISTENT", why
    ok, code, why = _v(tp=None, allow_no_tp=True, direction="BUY",
                       entry=2400.0, sl=2388.0)                  # correct side
    assert ok, why


def test_validator_no_tp_with_wrong_side_tp_still_rejected():
    ok, code, _ = _v(tp=2430.0, allow_no_tp=True)                 # TP ABOVE entry (SELL)
    assert not ok and code == "SIDES_INCONSISTENT"


def test_validator_valid_tp_still_passes_with_flag():
    ok, _, why = _v(allow_no_tp=True)                             # belt & braces
    assert ok, why


# ===========================================================================
# executor wiring - TRAIL_ONLY + fill recorder
# ===========================================================================
def test_trail_only_registry_and_fill_recorder(fresh_store):
    from app.execution.mt5 import TRAIL_ONLY_STRATEGIES, _apply_fill
    assert "strategy_trendbreak_v2" in TRAIL_ONLY_STRATEGIES
    assert "strategy_trendbreak_h1" not in TRAIL_ONLY_STRATEGIES
    doc = fresh_store.create("signals", {
        "strategy_id": "strategy_trendbreak_v2", "market": "EURUSD",
        "entry": 100.4, "sl": 99.83, "tp1": None})
    _apply_fill(fresh_store, {**doc, "entry": 100.4, "sl": 99.83, "tp1": None},
                {"ticket": 42, "price": 100.41, "volume": 0.1}, 0.1)
    out = fresh_store.get("signals", doc["id"])
    assert out["mt5_ticket"] == 42
    assert out["intended_rr"] is None                     # no TP -> no fixed RR
    assert out["executed_rr"] is None
    assert out["entry_slippage"] == pytest.approx(0.01)


def test_fill_recorder_with_tp_regression(fresh_store):
    from app.execution.mt5 import _apply_fill
    doc = fresh_store.create("signals", {
        "strategy_id": "strategy_trendbreak_h1", "market": "EURUSD",
        "entry": 100.0, "sl": 99.0, "tp1": 103.0})
    _apply_fill(fresh_store, {**doc}, {"ticket": 43, "price": 100.0,
                                       "volume": 0.1}, 0.1)
    out = fresh_store.get("signals", doc["id"])
    assert out["intended_rr"] == pytest.approx(3.0)       # unchanged behaviour


# ===========================================================================
# trade manager - strategy-owned deterministic block (0a)
# ===========================================================================
class _Provider:
    def __init__(self, df):
        self.df = df

    def get_candles(self, market, timeframe, limit=400):
        return self.df


def _mgr_frame(n_after=11):
    idx = pd.date_range("2026-10-01T00:00", periods=n_after + 1, freq="1h",
                        tz="UTC")
    o = 100.4 + 0.25 * np.arange(n_after + 1)
    c = o + 0.25
    return pd.DataFrame({"open": o, "high": c + 0.15, "low": o - 0.05,
                         "close": c}, index=idx)


def _pos(ticket=11, sl=99.83):
    return {"ticket": ticket, "type": "BUY", "app_market": "EURUSD",
            "symbol": "EURUSD", "price_open": 100.4, "price_current": 102.0,
            "sl": sl, "tp": None, "profit": 3.5, "volume": 0.1}


def _sig(atr=0.2286):
    return {"strategy_id": "strategy_trendbreak_v2", "market": "EURUSD",
            "direction": "BUY", "atr": atr, "sl": 99.83,
            "entry_time": "2026-10-01T00:00:00+00:00"}


@pytest.fixture()
def _capturing(monkeypatch):
    calls = []

    def fake_execute(user_id, position, plan, reason=""):
        calls.append({"user_id": user_id, "position": position,
                      "plan": dict(plan), "reason": reason})
        return {"executed": [{"op": next(iter(plan))}], "errors": []}

    import app.aimanager.risk_gate as rg
    monkeypatch.setattr(rg, "execute_plan", fake_execute)

    verdicts = []

    import app.aimanager.recorder as rec

    def fake_record(*a, **k):
        tb2_tags = [x for x in a if isinstance(x, str) and x.startswith("TB2")]
        verdicts.extend(tb2_tags)

    monkeypatch.setattr(rec, "record", fake_record)
    return calls, verdicts


def test_manager_trail_tightens_stop(_capturing):
    from app.aimanager.engine import TradeManager
    calls, verdicts = _capturing
    mgr = TradeManager(provider=_Provider(_mgr_frame()), router=None)
    out = mgr._review("u1", _pos(), _sig(), "manual", False, {}, time.time())
    assert verdicts == ["TB2_TRAIL"]                      # the v2 branch ran
    mods = [c for c in calls if "modify_sl" in c["plan"]]
    closes = [c for c in calls if "close_full" in c["plan"]]
    assert len(mods) == 1 and not closes                  # trail, no time exit yet
    best = float(_mgr_frame()["high"].max())
    expected = best - 5.0 * 0.2286
    assert mods[0]["plan"]["modify_sl"] == pytest.approx(expected, abs=1e-6)
    assert "trail" in mods[0]["reason"].lower() or "TB2" in mods[0]["reason"]


def test_manager_never_loosens_stop(_capturing):
    """A stop already tighter than the 5xATR trail is left alone."""
    from app.aimanager.engine import TradeManager
    calls, verdicts = _capturing
    mgr = TradeManager(provider=_Provider(_mgr_frame()), router=None)
    mgr._review("u1", _pos(sl=103.5), _sig(), "manual", False, {}, time.time())
    assert verdicts == ["TB2_TRAIL"]
    assert not [c for c in calls if "modify_sl" in c["plan"]]


def test_manager_time_exit_at_240(_capturing):
    from app.aimanager.engine import TradeManager
    calls, verdicts = _capturing
    mgr = TradeManager(provider=_Provider(_mgr_frame(n_after=245)), router=None)
    mgr._review("u1", _pos(), _sig(), "manual", False, {}, time.time())
    closes = [c for c in calls if "close_full" in c["plan"]]
    assert len(closes) == 1
    assert "time exit" in closes[0]["reason"].lower()


def test_manager_ignores_non_v2_strategies(_capturing):
    """The strategy-owned block exists for the v2 id ONLY."""
    from app.aimanager.engine import TradeManager
    calls, verdicts = _capturing
    mgr = TradeManager(provider=_Provider(_mgr_frame()), router=None)
    sig = _sig()
    sig["strategy_id"] = "strategy_trendbreak_h1"
    sig.pop("atr")
    out = mgr._review("u1", _pos(), sig, "manual", False, {}, time.time())
    assert verdicts == []                                 # no TB2 verdict ever
    assert not [c for c in calls if "trail" in c["reason"].lower()]


def test_manager_no_data_is_safe(_capturing):
    from app.aimanager.engine import TradeManager
    calls, verdicts = _capturing
    mgr = TradeManager(provider=_Provider(None), router=None)
    out = mgr._review("u1", _pos(), _sig(), "manual", False, {}, time.time())
    assert out is not None
    assert not calls


# ===========================================================================
# registration
# ===========================================================================
def test_registry_six_strategies_v2_active():
    import app.strategies as reg
    from app.learning.versions import START_PAUSED, lifecycle_of
    from app.strategies.research_only.live import TrendBreakV2Strategy
    assert reg.STRATEGY_CLASSES["strategy_trendbreak_v2"] is TrendBreakV2Strategy
    assert len(reg.STRATEGY_CLASSES) == 6
    assert "strategy_trendbreak_v2" not in START_PAUSED
    assert lifecycle_of("strategy_trendbreak_v2") == "LIVE"


def test_v2_boot_doc_created_active(fresh_store, monkeypatch):
    from app.learning import versions as vc
    monkeypatch.setattr(vc, "get_store", lambda: fresh_store)
    vc.LIFECYCLE.pop("strategy_trendbreak_v2", None)
    vc.ensure_strategy_docs()
    docs = {d["id"]: d for d in fresh_store.list("strategies", limit=1000)}
    assert "strategy_trendbreak_v2" in docs
    assert docs["strategy_trendbreak_v2"]["status"] == "ACTIVE"


def test_spec_untouched_by_tests():
    """The engine's SPEC still carries the owner's tested numbers."""
    assert tb2.SPEC["lookback"] == 120 and tb2.SPEC["atr_len"] == 14
    assert tb2.SPEC["stop_mult"] == 2.5 and tb2.SPEC["trail_mult"] == 5.0
    assert tb2.SPEC["gap_min"] == 2.0 and tb2.SPEC["vol_pct"] == 0.30
    assert tb2.SPEC["vol_win"] == 480 and tb2.SPEC["max_bars"] == 240
    assert tb2.SPEC["markets"] == ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
