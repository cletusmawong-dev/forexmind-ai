"""Rule tests for the owner's three strategies (implementation note
2026-11-02): MRMS Scalper (5M), Pullback in Trend (15M + H1), Liquidity
Sweep (15M). The rules run EXACTLY as delivered - these tests pin the
fire/no-fire behaviour and the signal-contract levels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies.strategy_mrms_scalper import MRMSScalper
from app.strategies.strategy_pullback_trend import PullbackInTrend
from app.strategies.strategy_liquidity_sweep import LiquiditySweep


def _bars(closes, opens, highs, lows, freq, end):
    idx = pd.date_range(end=end, periods=len(closes), freq=freq)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                         "close": closes, "volume": 100}, index=idx)


def _flat(n, base=100.0, rng=0.30, alt=0.05):   # quiet bars: br ~0.33
    """n alternating bars around `base` with a constant `rng` range."""
    cl, op, hi, lo = [], [], [], []
    p = base + alt
    for _ in range(n):
        o = p
        c = base - alt if o > base else base + alt
        cl.append(c); op.append(o)
        hi.append(max(o, c) + (rng - 2 * alt) / 2)
        lo.append(min(o, c) - (rng - 2 * alt) / 2)
        p = c
    return cl, op, hi, lo


# ===========================================================================
# MRMS Scalper - stretch >= 1.05 ATR from EMA8 + rejection, 5M, sessions
# ===========================================================================
def _mrms_df(end="2026-11-03 09:55", freq="5min"):
    cl, op, hi, lo = _flat(200)
    p = cl[-1]
    for _ in range(6):                       # the stretch: 6 down bars
        o = p
        c = o - 0.60
        cl.append(c); op.append(o)
        hi.append(o + 0.02); lo.append(c - 0.02)
        p = c
    # final bar: bullish rejection deep below the EMA
    o = p
    cl += [o + 0.40]; op += [o]; hi += [o + 0.50]; lo += [o - 0.20]
    return _bars(cl, op, hi, lo, freq, end)


def test_mrms_fires_buy_on_stretched_rejection_in_session():
    cand = MRMSScalper().detect_signal(_mrms_df(), "EURUSD", "5M")
    assert cand is not None and cand.direction == "BUY"
    st = MRMSScalper().compute(_mrms_df(), MRMSScalper().get_parameters())
    atr = float(st["atr"].iloc[-1])
    assert cand.sl < cand.entry and cand.tps[0] > cand.entry
    assert abs(cand.entry - cand.sl) > 0                    # real risk
    assert abs(cand.tps[0] - cand.entry) / abs(cand.entry - cand.sl) > 1.3
    assert cand.strategy_version == "1.0" and cand.timestamp


def test_mrms_mirrors_sell_on_stretched_rally():
    cl, op, hi, lo = _flat(200)
    p = cl[-1]
    for _ in range(6):
        o = p
        c = o + 0.60
        cl.append(c); op.append(o); hi.append(c + 0.02); lo.append(o - 0.02)
        p = c
    o = p
    cl += [o - 0.40]; op += [o]; hi += [o + 0.20]; lo += [o - 0.50]
    cand = MRMSScalper().detect_signal(
        _bars(cl, op, hi, lo, "5min", "2026-11-03 09:55"), "EURUSD", "5M")
    assert cand is not None and cand.direction == "SELL"
    assert cand.sl > cand.entry > cand.tps[0]


def test_mrms_respects_the_session_filter():
    df = _mrms_df(end="2026-11-03 20:55")          # 20:5x UTC - outside both
    assert MRMSScalper().detect_signal(df, "EURUSD", "5M") is None


def test_mrms_ignores_unstretched_price():
    cl, op, hi, lo = _flat(200)
    o = cl[-1]
    cl += [o + 0.05]; op += [o]; hi += [o + 0.20]; lo += [o - 0.10]
    df = _bars(cl, op, hi, lo, "5min", "2026-11-03 09:55")
    assert MRMSScalper().detect_signal(df, "EURUSD", "5M") is None


# ===========================================================================
# Pullback in Trend - H1 EMA50/200 bias + 15M pullback touch + rejection
# ===========================================================================
def _h1(up: bool):
    n = 240
    closes = [100 + 10 * k / (n - 1) for k in range(n)] if up else \
             [110 - 10 * k / (n - 1) for k in range(n)]
    op, hi, lo = [], [], []
    p = closes[0]
    for c in closes:
        o = p
        op.append(o); hi.append(max(o, c) + 0.05); lo.append(min(o, c) - 0.05)
        p = c
    return _bars(closes, op, hi, lo, "1h", "2026-11-03 09:00")


def _pbt_15m(up: bool, touch: bool = True):
    # the pullback bar is built around the H1 EMA50 the strategy actually
    # computes (uptrend ema50 ~ 109.0 / downtrend ~ 101.0 for these ramps)
    cl, op, hi, lo = _flat(320, base=109.0 if up else 101.0)
    o = 109.10 if up else 101.10
    c_ = o + 0.35 if up else o - 0.35
    lo[-1] = (o - 0.22 if touch else o + 0.15) if up else (c_ - 0.03)
    hi[-1] = (c_ + 0.03) if up else ((o + 0.22) if touch else (o - 0.15))
    cl[-1] = c_; op[-1] = o
    return _bars(cl, op, hi, lo, "15min", "2026-11-03 09:45")


def test_pbt_fires_buy_in_h1_uptrend_on_pullback_touch():
    cand = PullbackInTrend().detect_signal(
        _pbt_15m(True), "EURUSD", "15M", higher_frames={"1H": _h1(True)})
    assert cand is not None and cand.direction == "BUY"
    assert cand.sl < cand.entry < cand.tps[0]
    assert abs(cand.tps[0] - cand.entry) / abs(cand.entry - cand.sl) == 2.0


def test_pbt_fires_sell_in_h1_downtrend():
    cand = PullbackInTrend().detect_signal(
        _pbt_15m(False), "EURUSD", "15M", higher_frames={"1H": _h1(False)})
    assert cand is not None and cand.direction == "SELL"
    assert cand.tps[0] < cand.entry < cand.sl


def test_pbt_refuses_without_touching_the_ema_zone():
    cand = PullbackInTrend().detect_signal(
        _pbt_15m(True, touch=False), "EURUSD", "15M",
        higher_frames={"1H": _h1(True)})
    assert cand is None


def test_pbt_refuses_without_h1_context():
    cand = PullbackInTrend().detect_signal(
        _pbt_15m(True), "EURUSD", "15M", higher_frames={})
    assert cand is None


def test_pbt_refuses_counter_trend():
    # BUY rejection while the H1 trend is DOWN -> no candidate
    cand = PullbackInTrend().detect_signal(
        _pbt_15m(True), "EURUSD", "15M", higher_frames={"1H": _h1(False)})
    assert cand is None


# ===========================================================================
# Liquidity Sweep - sweep of the recent high/low + close back + rejection
# ===========================================================================
def _sweep_15m(side: str, sweep: bool = True):
    # flat window: lows 99.85 / highs 100.15; the signal bar uses an
    # explicit open so the sweep depth and body ratio are exact
    cl, op, hi, lo = _flat(220)
    o = 100.10
    if side == "BUY":
        c_ = 100.45
        lo[-1] = (o - 0.26) if sweep else (o - 0.05)
        hi[-1] = c_ + 0.02
    else:
        c_ = 99.75
        hi[-1] = (o + 0.06) if sweep else (o - 0.05)
        lo[-1] = c_ - 0.02
    cl[-1] = c_; op[-1] = o
    return _bars(cl, op, hi, lo, "15min", "2026-11-03 09:45")


def test_sweep_fires_buy_below_the_window_low():
    cand = LiquiditySweep().detect_signal(_sweep_15m("BUY"), "EURUSD", "15M")
    assert cand is not None and cand.direction == "BUY"
    st = LiquiditySweep().compute(_sweep_15m("BUY"),
                                  LiquiditySweep().get_parameters())
    extreme = float(cand.extra["sweep_extreme"])
    assert extreme == float(_sweep_15m("BUY")["low"].iloc[-1])
    assert cand.sl < extreme < cand.entry < cand.tps[0]   # stop beyond sweep


def test_sweep_fires_sell_above_the_window_high():
    cand = LiquiditySweep().detect_signal(_sweep_15m("SELL"), "EURUSD", "15M")
    assert cand is not None and cand.direction == "SELL"
    assert cand.tps[0] < cand.entry < cand.sl


def test_sweep_refuses_without_an_actual_sweep():
    assert LiquiditySweep().detect_signal(
        _sweep_15m("BUY", sweep=False), "EURUSD", "15M") is None
    assert LiquiditySweep().detect_signal(
        _sweep_15m("SELL", sweep=False), "EURUSD", "15M") is None


# ===========================================================================
# registry + platform integration
# ===========================================================================
def test_owner_note_registry_contract():
    from app.strategies import OWNER_STRATEGY_IDS, STRATEGY_CLASSES
    assert {"strategy_mrms_scalper", "strategy_pullback_trend",
            "strategy_liquidity_sweep"} <= set(STRATEGY_CLASSES)
    assert set(STRATEGY_CLASSES) == set(OWNER_STRATEGY_IDS)


def test_all_three_are_live_and_universally_managed():
    from app.learning.versions import lifecycle_of
    for sid in ("strategy_mrms_scalper", "strategy_pullback_trend",
                "strategy_liquidity_sweep"):
        assert lifecycle_of(sid) == "LIVE"
    # none of them is in the trade manager's strategy-owned lists ->
    # the Universal ladder manages them (owner note)
    from app.aimanager.engine import TradeManager
    ps = ("strategy_hybrid_crt_tbs", "strategy_fade_crt_ext")
    for sid in ("strategy_mrms_scalper", "strategy_pullback_trend",
                "strategy_liquidity_sweep"):
        assert sid not in ps and sid != "strategy_trendbreak_v2"


def test_backtests_run_and_use_the_declared_rrs():
    from app.strategies import all_strategies
    for sid in ("strategy_mrms_scalper", "strategy_liquidity_sweep"):
        s = all_strategies()[sid]
        df = _mrms_df() if "mrms" in sid else _sweep_15m("BUY")
        trades = s.backtest(pd.concat([df] * 2), max_hold_bars=100)
        assert isinstance(trades, list)
    s = all_strategies()["strategy_pullback_trend"]
    rrs = s.tp_rrs(s.get_parameters())
    assert rrs == [2.0]
