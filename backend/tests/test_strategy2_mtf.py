"""Strategy 2 - MTF Sweep -> BOS -> Retest: full spec section 30 suite.

Covers: sweep (bull/bear/none/closed-candle), BOS (bull/bear/none/unconfirmed
pivot), retest (bull/bear/failed), TP math (BUY/SELL/ATR), full state machine
both directions, restart state restore, duplicate protection, multi-symbol
isolation, timeframe mapping, strategy isolation, data integrity, and
end-to-end integration through the real SignalEngine.
"""
import pandas as pd
import pytest

from app.strategies.strategy_2_mtf_sweep_bos_retest import machine
from app.strategies.strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy

S = MtfSweepBosRetestStrategy()
T0 = pd.Timestamp("2026-09-19 10:00")


def df_of(rows, start=T0, freq="15min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def flat(rows_n, o=103.2, h=103.3, l=103.1, c=103.2, start=T0, freq="15min"):
    return df_of([(o, h, l, c)] * rows_n, start=start, freq=freq)


def bull_sweep_frame():
    """v2.0.0: confirmed swing low 98.0 (idx4), final candle WICKS below it
    and CLOSES back above -> sell-side liquidity swept (master spec 6-7)."""
    rows = [(100, 100.5, 99.5, 100.0), (100.0, 100.4, 99.4, 99.8),
            (99.8, 100.2, 99.2, 99.6), (99.6, 99.9, 99.0, 99.3),
            (99.3, 99.5, 98.0, 98.8),                       # idx4 pivot low 98.0
            (98.8, 99.6, 98.5, 99.2), (99.2, 99.8, 98.9, 99.5),
            (99.5, 100.0, 99.3, 99.8),                      # idx7 confirms pivot
            (99.8, 99.9, 97.5, 99.9)]                       # sweep candle (last)
    return df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="4h")


def bear_sweep_frame():
    """v2.0.0: confirmed swing high 102.0 (idx4); last candle wicks above and
    closes back below -> buy-side liquidity swept (master spec 8)."""
    rows = [(100, 100.5, 99.5, 100.2), (100.2, 100.6, 99.8, 100.4),
            (100.4, 100.8, 100.0, 100.6), (100.6, 101.0, 100.2, 100.8),
            (100.8, 102.0, 100.6, 101.6),                   # idx4 pivot high 102.0
            (101.6, 101.8, 100.9, 101.2), (101.2, 101.4, 100.8, 101.0),
            (101.0, 101.2, 100.6, 100.9),                   # idx7 confirms pivot
            (100.9, 102.5, 100.8, 100.7)]                   # sweep candle (last)
    return df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="4h")


def bos_bull_frame():
    rows = [(100, 101, 99.5, 100.5), (100.5, 102, 100.2, 101.5), (101.5, 102.5, 101, 102),
            (102, 102.6, 101.6, 102.2), (102.2, 103, 102, 102.5),      # idx4 pivot high 103
            (102.5, 102.4, 101.8, 102), (102, 102.2, 101.5, 101.7),
            (101.7, 101.9, 101.2, 101.5),                              # idx7 confirms pivot
            (101.5, 102.8, 102.6, 102.7), (102.7, 102.9, 102.6, 102.8),
            (102.8, 102.9, 102.7, 102.9),
            (102.9, 103.6, 102.8, 103.5)]                              # BOS candle (last)
    return df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="1h")


def bos_bear_frame():
    rows = [(100, 101, 100.2, 100.5), (100.5, 101.5, 101.2, 101.3), (101.3, 101.6, 101.3, 101.4),
            (101.4, 101.5, 101.1, 101.2), (101.2, 101.3, 101.0, 101.1),  # idx4 pivot low 101.0
            (101.1, 101.4, 101.2, 101.3), (101.3, 101.5, 101.3, 101.4),
            (101.4, 101.6, 101.4, 101.5),                                # idx7 confirms pivot
            (101.5, 101.8, 101.5, 101.7), (101.7, 101.9, 101.6, 101.8),
            (101.8, 102.0, 101.7, 101.9), (101.9, 102.0, 100.4, 100.6)]  # idx11 BOS down
    return df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="1h")


def entry_retest_frame(bars=300):
    d = flat(bars - 1)
    retest = pd.DataFrame([(103.0, 103.15, 102.95, 103.1)],
                          columns=["open", "high", "low", "close"],
                          index=[d.index[-1] + pd.Timedelta("15min")])
    return pd.concat([d, retest])


def _frames():
    return entry_retest_frame(), bull_sweep_frame(), bos_bull_frame()


@pytest.fixture()
def world(monkeypatch, tmp_path):
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    return store


def state_load(doc_id):
    from app.strategies.strategy_2_mtf_sweep_bos_retest import state as ss
    return ss.load(*doc_id.split("|"))


# ============================== sweep (spec 8) =============================
def test_sweep_bullish():
    res = machine.detect_sweep(bull_sweep_frame(), 3)
    assert res and res[0] == "bull"
    assert res[1] == pytest.approx(98.0)      # the swept swing-low level
    assert res[2] == pytest.approx(97.5)      # the wick that swept it


def test_sweep_bearish():
    res = machine.detect_sweep(bear_sweep_frame(), 3)
    assert res and res[0] == "bear"
    assert res[1] == pytest.approx(102.0)
    assert res[2] == pytest.approx(102.5)


def test_false_sweep_close_below_level_is_no_signal():
    """Master spec 7: wick below the level but CLOSE below it too = breakdown,
    NOT a sweep -> None."""
    f = bull_sweep_frame()
    f.iloc[-1, f.columns.get_loc("close")] = 97.8    # closed below the level
    assert machine.detect_sweep(f, 3) is None


def test_sweep_level_must_be_confirmed_before_sweep_candle():
    """No lookahead: with NO confirmed swing level (strictly descending lows),
    a wick-down-and-close-above is NOT a sweep - there is no liquidity level
    to sweep. Levels must be confirmed pivots from EARLIER candles."""
    rows = [(100.8, 101.0, 100.5 - 0.3 * i, 100.4 - 0.3 * i) for i in range(8)]
    rows.append((rows[-1][3], rows[-1][3] + 0.4, 97.0, rows[-1][3] + 0.3))
    f = df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="4h")
    assert machine.detect_sweep(f, 3) is None


def test_sweep_unconfirmed_recent_pivot_is_not_liquidity():
    """A swing low centered on one of the last swing_len candles is not yet
    confirmed and must never be used as liquidity (no repaint)."""
    rows = [(100, 100.5, 99.5, 100.0)] * 6 + [
        (100.0, 100.2, 97.9, 99.8),   # idx6: deep low, pivot center too recent
        (99.8, 100.0, 99.4, 99.9),
        (99.9, 100.1, 97.5, 100.05)]  # sweep-shaped candle
    f = df_of(rows, start=pd.Timestamp("2026-09-19 00:00"), freq="4h")
    res = machine.detect_sweep(f, 3)
    assert res is None or res[1] != 97.9   # 97.9 can never be the swept level


def test_sweep_none_when_both_bullish():
    f = df_of([(100, 101, 99, 100.5), (100.5, 102, 100.2, 101.5)],
              start=pd.Timestamp("2026-09-19 00:00"), freq="4h")
    assert machine.detect_sweep(f) is None


def test_sweep_uses_latest_closed_pair_only():
    # the sweep was between [-3] and [-2]; the LATEST closed pair is not a
    # sweep -> no event (a sweep only counts when its candle just closed)
    f = df_of([(100, 101, 98, 99), (99, 102, 97.5, 101.5), (101.5, 103, 101, 102.5)],
              start=pd.Timestamp("2026-09-19 00:00"), freq="4h")
    assert machine.detect_sweep(f) is None


# ============================== BOS (spec 10-11) ===========================
def bull_bos_state():
    return {**machine.default_state(), "bullish_setup": True, "bearish_setup": False}


def test_bos_bullish_fires():
    fired, st = machine.detect_bos(bos_bull_frame(), bull_bos_state(), 3, "bull")
    assert fired is True
    assert st["broken_high"] == pytest.approx(103.0)
    assert st["bos_candle_low"] == pytest.approx(102.8)
    assert st["waiting_bull_retest"] is True and st["waiting_bear_retest"] is False


def test_bos_bearish_fires():
    st = {**machine.default_state(), "bearish_setup": True}
    fired, st = machine.detect_bos(bos_bear_frame(), st, 3, "bear")
    assert fired is True
    assert st["broken_low"] == pytest.approx(101.0)
    assert st["bos_candle_high"] == pytest.approx(102.0)
    assert st["waiting_bear_retest"] is True and st["waiting_bull_retest"] is False


def test_bos_no_break_no_event():
    f = bos_bull_frame()
    f.iloc[-1, f.columns.get_loc("close")] = 102.0     # below structure 103
    fired, st = machine.detect_bos(f, bull_bos_state(), 3, "bull")
    assert fired is False
    assert st["waiting_bull_retest"] is False


def test_bos_requires_setup():
    fired, _ = machine.detect_bos(bos_bull_frame(), machine.default_state(), 3, "bull")
    assert fired is False


def test_bos_ignores_unconfirmed_pivots():
    # huge highs on the last two bars: a pivot centered there needs right-side
    # bars that do not exist yet -> structures stay at the confirmed 103 pivot
    st = bull_bos_state()
    f = bos_bull_frame()
    f.iloc[-2, f.columns.get_loc("high")] = 150.0
    f.iloc[-1, f.columns.get_loc("high")] = 200.0
    f.iloc[-1, f.columns.get_loc("close")] = 120.0
    fired, st2 = machine.detect_bos(f, st, 3, "bull")
    assert st2["broken_high"] == pytest.approx(103.0)   # NOT 150/200 (unconfirmed)
    assert st2["structure_high"] == pytest.approx(103.0)


# ============================== retest (spec 12-13) ========================
def test_retest_bullish():
    st = {**machine.default_state(), "waiting_bull_retest": True,
          "broken_high": 103.0, "bos_candle_low": 103.2}
    e = flat(3)
    e.iloc[-1, e.columns.get_loc("low")] = 102.95
    e.iloc[-1, e.columns.get_loc("close")] = 103.1
    hit = machine.check_retest(e, st)
    assert hit and hit["direction"] == "BUY"
    assert hit["entry"] == pytest.approx(103.1) and hit["sl"] == pytest.approx(103.2)


def test_retest_bearish():
    st = {**machine.default_state(), "waiting_bear_retest": True,
          "broken_low": 101.0, "bos_candle_high": 101.5}
    e = flat(3, o=102.8, h=102.9, l=102.7, c=102.8)
    e.iloc[-1, e.columns.get_loc("high")] = 101.2
    e.iloc[-1, e.columns.get_loc("close")] = 100.8
    hit = machine.check_retest(e, st)
    assert hit and hit["direction"] == "SELL" and hit["sl"] == pytest.approx(101.5)


def test_retest_failed_keeps_waiting():
    st = {**machine.default_state(), "waiting_bull_retest": True,
          "broken_high": 103.0, "bos_candle_low": 103.2}
    e = flat(3)
    e.iloc[-1, e.columns.get_loc("low")] = 102.95
    e.iloc[-1, e.columns.get_loc("close")] = 102.8    # closes back below -> failed
    assert machine.check_retest(e, st) is None
    assert st["waiting_bull_retest"] is True          # state persists (spec 9)


# ============================== TP / ATR (spec 12-14) ======================
def test_atr_is_wilder_rma_like_pine_ta_atr():
    """Independent reimplementation of RMA(true_range, n) - Pine ta.atr."""
    from app.core.indicators import atr as core_atr, true_range
    rows, c_prev = [], 100.0
    for i, (o, h, l, c) in enumerate([(100, 101, 99.5, 100.5), (100.5, 102, 100.2, 101.5),
                                      (101.5, 102.5, 101, 102), (102, 102.6, 101.6, 102.2),
                                      (101, 101.9, 100.8, 101.1), (101.1, 102.8, 101, 102.6),
                                      (102.6, 103.4, 102.2, 102.9), (102.9, 103, 101.4, 101.6),
                                      (101.6, 102.2, 101.1, 101.9), (101.9, 103.1, 101.7, 102.8),
                                      (102.8, 103.3, 102.5, 103), (103, 103.8, 102.9, 103.5)]):
        rows.append((o, h, l, c))
    d = df_of(rows)
    trs = true_range(d).reset_index(drop=True).to_numpy()
    manual = pd.Series(trs).ewm(alpha=1.0 / 3, adjust=False).mean().to_numpy()
    a = core_atr(d, 3).reset_index(drop=True).to_numpy()
    assert list(a) == [pytest.approx(v, abs=1e-9) for v in manual]


def test_tp_ladder_is_r_based(world):
    """Master spec 13: TP1/TP2/TP3 = 1R/2R/3R from the entry-to-SL risk."""
    e, sw, bo = _frames()
    c = S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c is not None
    R = abs(c.entry - c.sl)
    assert c.tps == [pytest.approx(c.entry + R * m) for m in (1.0, 2.0, 3.0)]


def test_sl_anchors_beyond_swept_level(world):
    """Master spec 12: SL sits BELOW the swept swing low (bull) with the
    configured ATR buffer - not at the BOS candle."""
    from app.core.indicators import atr as core_atr
    e, sw, bo = _frames()
    a = float(core_atr(e, 14).iloc[-1])
    c = S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c is not None
    assert c.sl == pytest.approx(98.0 - 0.25 * a)
    assert c.extra["liquidity_level"] == pytest.approx(98.0)
    assert c.extra["liquidity_type"] == "sell_side"
    assert c.extra["signal_state"] == "RETEST_CONFIRMED"


# ============================== timeframe mapping (spec 6) =================
def test_timeframe_mapping_exact():
    assert machine.TIMEFRAME_MAP == {"5M": ("1H", "15M"), "15M": ("4H", "1H"),
                                     "30M": ("4H", "1H"), "1H": ("1D", "4H"),
                                     "4H": ("1D", "4H")}


# ==================== full machine via detect_signal =======================
def test_full_machine_bull_end_to_end(world):
    e, sw, bo = _frames()
    c = S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c is not None and c.direction == "BUY"
    assert c.strategy_id == "strategy_2_mtf_sweep_bos_retest"
    assert c.entry == pytest.approx(103.1)
    assert c.sl < 98.0        # beyond the swept swing low
    assert c.extra["setup_state"] == "SWEEP_BOS_RETEST"
    assert c.extra["sweep_timeframe"] == "4H" and c.extra["bos_timeframe"] == "1H"
    assert c.extra["entry_timeframe"] == "15M"


def test_full_machine_bear_direction(world):
    e2 = flat(299)
    e2 = pd.concat([e2, pd.DataFrame([(101.2, 101.3, 100.7, 100.8)],
                                     columns=["open", "high", "low", "close"],
                                     index=[e2.index[-1] + pd.Timedelta("15min")])])
    c = S.detect_signal(e2, "XAUUSD", "15M",
                        higher_frames={"4H": bear_sweep_frame(), "1H": bos_bear_frame()})
    assert c is not None and c.direction == "SELL"
    assert c.sl > 102.0       # beyond the swept swing high
    assert c.tps[0] < c.entry < c.sl


def test_duplicate_signal_blocked_same_retest(world):
    e, sw, bo = _frames()
    assert S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo}) is not None
    assert S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo}) is None


def test_state_survives_restart(world):
    e, sw, bo = _frames()
    pre = e.iloc[:-1]                       # sweep + BOS happen, no retest yet
    assert S.detect_signal(pre, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo}) is None
    st = state_load("XAUUSD|15M")
    assert st["waiting_bull_retest"] is True and st["broken_high"] == pytest.approx(103.0)
    # process restart: a FRESH strategy instance resumes from persisted state
    S2 = MtfSweepBosRetestStrategy()
    c = S2.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert c is not None and c.direction == "BUY"


def test_multi_symbol_state_isolation(world):
    e, sw, bo = _frames()
    assert S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo}) is not None
    # EURUSD runs its OWN machine: with no sweep candle in ITS history it stays
    # idle even though XAUUSD is mid-setup (no cross-symbol state leak)
    no_sweep = df_of([(100, 101, 99, 100.5), (100.5, 102, 100.2, 101.5)],
                     start=pd.Timestamp("2026-09-19 00:00"), freq="4h")
    assert S.detect_signal(e, "EURUSD", "15M",
                           higher_frames={"4H": no_sweep, "1H": bo}) is None
    # EURUSD's own persisted state never contains XAUUSD's setup
    st_e = state_load("EURUSD|15M")
    assert st_e["waiting_bull_retest"] is False and st_e["broken_high"] is None


def test_idempotent_when_no_new_candles(world):
    e, sw, bo = _frames()
    S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    st1 = state_load("XAUUSD|15M")
    for _ in range(3):
        S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    assert state_load("XAUUSD|15M") == st1


# ============================== data integrity (spec 25) ===================
def test_integrity_missing_sweep_frame(world):
    e, _, bo = _frames()
    assert S.detect_signal(e, "XAUUSD", "15M", higher_frames={"1H": bo}) is None


def test_integrity_non_monotonic_entry_timestamps(world):
    e, sw, bo = _frames()
    bad = pd.concat([e.iloc[:-6], e.iloc[-9:]])   # tail overlaps -> time goes backwards
    assert S.detect_signal(bad, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo}) is None


def test_integrity_too_few_entry_bars(world):
    e, sw, bo = _frames()
    assert S.detect_signal(e.iloc[-50:], "XAUUSD", "15M",
                           higher_frames={"4H": sw, "1H": bo}) is None


# ==================== alignment: events keyed per frame ====================
def test_alignment_events_keyed_by_own_frame_candles(world):
    e, sw, bo = _frames()
    S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    st = state_load("XAUUSD|15M")
    assert st["last_sweep_ts"] == int(sw.index[-1].timestamp())
    assert st["last_bos_ts"] == int(bo.index[-1].timestamp())
    assert st["last_retest_ts"] == int(e.index[-1].timestamp())


# ==================== strategy isolation (spec 30) =========================
def test_strategy1_isolation(world):
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    rows, price = [], 100.0
    for i in range(320):
        price += 0.2 if 150 <= i < 160 else (-0.15 if i >= 160 else 0.0)
        rows.append((price, price + 0.1, price - 0.1, price))
    d = df_of(rows)
    ema = EmaAtrStrategy()
    before = ema.detect_signal(d, "XAUUSD", "15M")
    e, sw, bo = _frames()
    S.detect_signal(e, "XAUUSD", "15M", higher_frames={"4H": sw, "1H": bo})
    after = ema.detect_signal(d, "XAUUSD", "15M")
    assert (before is None) == (after is None)
    if before is not None:
        assert before.entry == after.entry and before.direction == after.direction


# ============ end-to-end through the real SignalEngine =====================
def test_engine_integration_creates_s2_signal(world, monkeypatch):
    monkeypatch.setenv("REPLAY_ENABLED", "0")
    from app.engine.signal_engine import SignalEngine
    import app.engine.signal_engine as se_mod
    captured = []
    monkeypatch.setattr(se_mod, "notify",
                        lambda uid, ntype, title, body, **kw: captured.append(body))

    class StubProvider:
        is_demo = False
        def get_candles(self, market, tf, limit=600):
            e, sw, bo = _frames()
            return {"15M": e, "4H": sw, "1H": bo}.get(tf)
        def higher_frames(self, market, base_tf, frames=("5M", "15M", "1H", "4H", "1D")):
            out = {}
            for tf in frames:
                d = self.get_candles(market, tf)
                if d is not None and tf != base_tf:
                    out[tf] = d
            return out

    world.create("strategies", {"id": "strategy_2_mtf_sweep_bos_retest",
                                "status": "ACTIVE", "version": "v1.0"}, doc_id="strategy_2_mtf_sweep_bos_retest")
    eng = SignalEngine(StubProvider())
    created = eng.scan("u1", "XAUUSD", "15M", log_activity=False)
    s2 = [x for x in created if x["strategy_id"] == "strategy_2_mtf_sweep_bos_retest"]
    assert len(s2) == 1
    doc = s2[0]
    assert doc["sweep_timeframe"] == "4H" and doc["bos_timeframe"] == "1H"
    assert doc["setup_state"] == "SWEEP_BOS_RETEST"
    R = doc["entry"] - doc["sl"]
    assert doc["tp1"] == pytest.approx(doc["entry"] + R)       # 1R
    assert doc["tp2"] == pytest.approx(doc["entry"] + 2 * R)   # 2R
    assert doc["tp3"] == pytest.approx(doc["entry"] + 3 * R)   # 3R
    from app.core.indicators import atr as core_atr
    a = float(core_atr(entry_retest_frame(), 14).iloc[-1])
    assert doc["sl"] == pytest.approx(98.0 - 0.25 * a)  # beyond swept level + buffer
    assert any("Sweep TF" in b for b in captured)
