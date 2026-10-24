"""Owner brief 2026-10-24 - Execution & Risk-to-Reward audit tests.

Covers the brief's required scenarios:
  s.1  selected-pair execution (no hardcoded Gold; each selected pair
       evaluated independently; deselected pairs cannot execute)
  s.2  R:R integrity (order R:R must equal the strategy's configured R:R;
       BUY/SELL sides verified; final submitted levels recorded)
  s.3  universal pre-trade validation (reject, never repair)
  s.4  per-strategy, per-pair, BUY/SELL, sizing and protections
No live trading is touched: every broker call is a captured fake.
"""
from types import ModuleType
import sys

import pandas as pd
import pytest

from app.strategies.gold_specs.strategy import GoldOrbStrategy, H1TrendBreakoutStrategy
from app.execution import pretrade
from app.execution.pretrade import planned_rr, validate_order

# ---------------------------------------------------------------------------
# synthetic frames (same builders as test_37, plus price rescaling so the
# SAME rules are exercised at EURUSD / XAUUSD / NAS100 price levels)
# ---------------------------------------------------------------------------
def mk(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def rescale(df, level):
    """Rescale a frame to a different instrument's price level (rules are
    ratio-based, so shape is preserved; only the price magnitude changes)."""
    f = level / float(df["close"].iloc[-1])
    return mk([str(t).replace(" ", "T") for t in df.index],
              [v * f for v in df["open"]], [v * f for v in df["high"]],
              [v * f for v in df["low"]], [v * f for v in df["close"]])


def h1_trend_frame(bars=80, flat=40, step=2.0, end="2026-10-09T20:00"):
    idx = pd.date_range(end=end, periods=bars, freq="1h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = 2400.0
    for i in range(bars):
        if i < flat:
            o.append(px); h.append(px + 0.5); l.append(px - 0.5); c.append(px)
        else:
            px += step
            o.append(px - step); h.append(px + 0.6); l.append(px - step - 0.4); c.append(px)
    return mk(ts, o, h, l, c)


def h4_trend_frame(bars=210, up_last=40, step=3.0):
    idx = pd.date_range(end="2026-10-09T16:00", periods=bars, freq="4h", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    o, h, l, c = [], [], [], []
    px = 2350.0
    for i in range(bars):
        if i >= bars - up_last:
            px += step
        o.append(px - 1.0); h.append(px + 1.2); l.append(px - 2.2); c.append(px)
    return mk(ts, o, h, l, c)


def m15_frame(end="2026-10-09T21:00"):
    idx = pd.date_range(end=end, periods=3, freq="15min", tz="UTC")
    ts = [str(t).replace(" ", "T") for t in idx]
    return mk(ts, [2440.0] * 3, [2441.0] * 3, [2439.0] * 3, [2440.5] * 3)


def orb_day(trigger_at="14:05"):
    ts, o, h, l, c = [], [], [], [], []
    slots = [(13, m) for m in (30, 35, 40, 45, 50, 55)]
    slots += [(hh, m) for hh in (14,) for m in (0, 5)]
    for (hh, mm) in slots:
        t = f"2026-10-08T{hh:02d}:{mm:02d}"
        if (hh, mm) <= (13, 55):
            ts.append(t); o.append(2400.0); h.append(2401.0)
            l.append(2399.5); c.append(2400.2)
        elif (hh, mm) == (14, 5):
            ts.append(t); o.append(2400.8); h.append(2403.0)
            l.append(2400.6); c.append(2402.5)
        else:
            ts.append(t); o.append(2400.3); h.append(2400.8)
            l.append(2399.9); c.append(2400.5)
    return mk(ts, o, h, l, c)


PAIR_LEVELS = {"EURUSD": 1.10, "GBPUSD": 1.27, "USDJPY": 150.0,
               "XAUUSD": 2400.0, "XAGUSD": 30.0, "NAS100": 21000.0}


def h1_higher_for(level):
    return {"15M": rescale(m15_frame(), level),
            "4H": rescale(h4_trend_frame(), level)}


# ===========================================================================
# s.1 - SELECTED-PAIR EXECUTION: no hardcoded Gold
# ===========================================================================
@pytest.mark.parametrize("market", ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "NAS100"])
def test_h1_breakout_evaluates_every_market(market):
    """Each selected pair is evaluated with the SAME deterministic rules."""
    level = PAIR_LEVELS[market]
    cand = H1TrendBreakoutStrategy().detect_signal(
        rescale(h1_trend_frame(), level), market, "1H",
        higher_frames={k: rescale(v, level) for k, v in h1_higher_for(2400.0).items()})
    assert cand is not None, f"H1 strategy refused {market}"
    assert cand.market == market
    assert cand.direction == "BUY"
    # spec geometry preserved on every instrument: stop 1.2xATR, target 1.25R
    assert cand.rr_primary == pytest.approx(1.25, abs=0.01)
    assert cand.sl < cand.entry < cand.tps[0]


@pytest.mark.parametrize("market", ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "NAS100"])
def test_orb_evaluates_every_market(market):
    level = PAIR_LEVELS[market]
    cand = GoldOrbStrategy().detect_signal(
        None, market, "15M", higher_frames={"5M": rescale(orb_day(), level)})
    assert cand is not None, f"ORB refused {market}"
    assert cand.market == market
    assert cand.direction == "BUY"
    assert cand.rr_primary == pytest.approx(1.5, abs=0.01)
    # ORB stop sits at the range low - on the correct side for a BUY
    assert cand.sl < cand.entry < cand.tps[0]


def test_orb_buy_and_sell_directions_are_exclusive():
    """ORB is long-only by spec: a bearish trigger candle must never produce
    a SELL candidate (direction correctness, brief s.3 check 3)."""
    bear = orb_day(trigger_at="14:05")
    # make the trigger candle close below the range high
    bear.iloc[-1, bear.columns.get_loc("close")] = 2399.0
    assert GoldOrbStrategy().detect_signal(None, "EURUSD", "15M",
                                           higher_frames={"5M": rescale(bear, 1.1)}) is None


def test_h1_short_candidate_sides_are_correct():
    base = h1_trend_frame()
    f = 4800.0
    short = mk([str(t).replace(" ", "T") for t in base.index],
               [f - v for v in base["open"]], [f - v for v in base["low"]],
               [f - v for v in base["high"]], [f - v for v in base["close"]])
    h4 = h4_trend_frame()
    h4_short = mk([str(t).replace(" ", "T") for t in h4.index],
                  [f - v for v in h4["open"]], [f - v for v in h4["low"]],
                  [f - v for v in h4["high"]], [f - v for v in h4["close"]])
    m15 = m15_frame()
    m15_short = mk([str(t).replace(" ", "T") for t in m15.index],
                   [f - v for v in m15["open"]], [f - v for v in m15["low"]],
                   [f - v for v in m15["high"]], [f - v for v in m15["close"]])
    cand = H1TrendBreakoutStrategy().detect_signal(
        short, "XAUUSD", "1H", higher_frames={"15M": m15_short, "4H": h4_short})
    assert cand is not None and cand.direction == "SELL"
    # SELL geometry: TP < entry < SL
    assert cand.tps[0] < cand.entry < cand.sl
    assert cand.rr_primary == pytest.approx(1.25, abs=0.01)


def test_timeframe_gate_still_applies():
    """markets is now universal, but the timeframe gate is unchanged."""
    assert H1TrendBreakoutStrategy().detect_signal(
        h1_trend_frame(), "EURUSD", "30M",
        higher_frames=h1_higher_for(1.1)) is None


def test_spec_rr_is_declared_for_the_validator():
    assert H1TrendBreakoutStrategy.configured_rr == pytest.approx(1.25)
    assert GoldOrbStrategy.configured_rr == pytest.approx(1.5)
    assert H1TrendBreakoutStrategy.markets is None   # no instrument pin
    assert GoldOrbStrategy.markets is None


# ===========================================================================
# s.1 - deselected pairs cannot execute (per-user allowed_markets gate)
# ===========================================================================
def test_deselected_pair_is_refused(fresh_store, monkeypatch):
    from app.engine.signal_engine import SignalEngine
    from app.strategies.base import Candidate
    uid = "u1"
    fresh_store.create("settings", {"userId": uid, "kind": "risk",
                                    "allowed_markets": ["XAUUSD"]})
    cand = Candidate(strategy_id="strategy_h1_trend_breakout", market="EURUSD",
                     timeframe="1H", direction="BUY", candle_time="2026-10-09T20:00:00+00:00",
                     entry=1.105, entry_zone=[1.105, 1.105], sl=1.1040, tps=[1.1075],
                     risk=0.001, rr_primary=1.25, score=60, score_components={},
                     checks=[], analysis="", params={}, params_version="1.0")
    ok, reason = SignalEngine(None)._guards(uid, cand, "London")
    assert not ok and "allowed" in reason


def test_selected_pair_passes_the_user_gate(fresh_store):
    from app.engine.signal_engine import SignalEngine
    from app.strategies.base import Candidate
    uid = "u1"
    fresh_store.create("settings", {"userId": uid, "kind": "risk",
                                    "allowed_markets": ["XAUUSD", "EURUSD"]})
    cand = Candidate(strategy_id="strategy_h1_trend_breakout", market="EURUSD",
                     timeframe="1H", direction="BUY", candle_time="2026-10-09T20:00:00+00:00",
                     entry=1.105, entry_zone=[1.105, 1.105], sl=1.1040, tps=[1.1075],
                     risk=0.001, rr_primary=1.25, score=60, score_components={},
                     checks=[], analysis="", params={}, params_version="1.0")
    ok, reason = SignalEngine(None)._guards(uid, cand, "London")
    assert ok, reason


# ===========================================================================
# s.2/s.3 - the pre-trade validator itself
# ===========================================================================
B, E, SL, TP = 10_000.0, 2400.0, 2412.0, 2385.0   # SELL: risk 12, reward 15 -> 1.25


def _v(**kw):
    base = dict(market="XAUUSD", direction="SELL", entry=E, sl=SL, tp=TP,
                lots=0.08, intended_rr=1.25, symbol_known=True,
                strategy_status="ACTIVE", lifecycle="LIVE", pip_size=0.1,
                pip_value=10.0, min_stop_pips=6.0, balance=B, eff_risk_pct=1.0,
                lots_cap=1.0, dup_checked=True, budgets_allowed=True,
                reference_price=E)
    base.update(kw)
    return validate_order(**base)


def test_validator_accepts_valid_sell_and_buy():
    ok, code, why = _v()
    assert ok, why
    ok, code, why = _v(direction="BUY", entry=2400.0, sl=2388.0, tp=2415.0,
                       intended_rr=1.25)          # BUY: SL < entry < TP
    assert ok, why


def test_validator_rejects_wrong_side_stop():
    ok, code, _ = _v(direction="BUY", entry=2400.0, sl=2412.0, tp=2385.0,
                     intended_rr=1.25)            # BUY with SL above entry
    assert not ok and code == "SIDES_INCONSISTENT"


def test_validator_rejects_rr_mismatch():
    ok, code, why = _v(tp=E - 12.0)               # 1R target on a 1.25R strategy
    assert not ok and code == "RR_MISMATCH"
    assert "1.000" in why and "1.250" in why


def test_validator_rejects_missing_tp():
    ok, code, _ = _v(tp=None)
    assert not ok and code == "PRICES_INVALID"


def test_validator_rejects_unsupported_symbol():
    ok, code, _ = _v(market="USDCHKD", symbol_known=False)
    assert not ok and code == "SYMBOL_UNSUPPORTED"


def test_validator_rejects_inactive_or_nonlive_strategy():
    ok, code, _ = _v(strategy_status="PAUSED")
    assert not ok and code == "STRATEGY_NOT_ACTIVE"
    ok, code, _ = _v(lifecycle="SLEEPING")
    assert not ok and code == "STRATEGY_NOT_LIVE"


def test_validator_rejects_oversized_risk():
    ok, code, why = _v(lots=0.9)                  # 0.9 x 120 pips x $10 = $1080 > 1% of $10k
    assert not ok and code == "RISK_OVER_BUDGET"


def test_validator_rejects_stale_entry():
    ok, code, why = _v(reference_price=E - 3.0)   # SELL: market $3 under entry = 0.25R drift... boundary
    # exactly 0.25R must pass; a hair more must refuse
    ok2, code2, _ = _v(reference_price=E - 3.01)
    assert ok, why
    assert not ok2 and code2 == "ENTRY_STALE"


def test_validator_fails_closed_on_garbage():
    ok, code, _ = _v(entry="abc", sl=None, tp="")
    assert not ok


def test_validator_requires_dup_check():
    ok, code, _ = _v(dup_checked=False)
    assert not ok and code == "DUP_CHECK_MISSING"


def test_planned_rr_helper():
    assert planned_rr(100, 90, 115) == pytest.approx(1.5)
    assert planned_rr(100, 100, 110) is None
    assert planned_rr(100, 90, None) is None


# ===========================================================================
# s.2/s.3 - execute_signal integration: the order the broker would receive
# ===========================================================================
@pytest.fixture()
def exec_env(fresh_store, monkeypatch):
    """Everything execute_signal needs, with the broker call CAPTURED."""
    from app.execution import mt5 as m5
    uid = "u1"
    monkeypatch.setattr(m5, "user_mode", lambda u: "vps")
    monkeypatch.setattr(m5, "execution_enabled", lambda u: True)
    monkeypatch.setattr(m5, "_goals_doc", lambda u: {"risk_per_trade_pct": 1.0})
    monkeypatch.setattr(m5, "_executed_today", lambda u: 0)
    monkeypatch.setattr(m5, "_lot_mode", lambda u: "medium")
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "owner_user_id", uid, raising=False)
    import app.api.routes_accounts as ra
    monkeypatch.setattr(ra, "active_account", lambda u: None)
    import app.learning.versions as lv
    monkeypatch.setattr(lv, "lifecycle_of", lambda sid: "LIVE")
    import app.engine.daily as daily
    monkeypatch.setattr(daily, "is_entry_blocked", lambda u: (False, None, {}))
    monkeypatch.setattr(daily, "daily_state", lambda u: {"total_usd": 0.0,
                                                         "consecutive_losses": 0})
    import app.risk_checks as rc
    monkeypatch.setattr(rc, "check_entry", lambda *a, **k: (True, None, None))

    sent = {}

    def fake_bridge_get(path, timeout=8):
        if path == "/account":
            return {"balance": 10_000.0, "currency": "USD"}
        if path.startswith("/rates"):
            return {"bars": [{"c": REF["price"]}]}
        return None

    def fake_bridge_post(path, payload, timeout=15):
        sent["path"] = path
        sent["payload"] = dict(payload)
        return {"ok": True, "ticket": 900001, "position_id": 900001,
                "volume": payload["lots"], "price": REF["price"],
                "symbol": "BROKERSYMBOL.m"}
    REF = {"price": None}
    monkeypatch.setattr(m5, "bridge_get", fake_bridge_get)
    monkeypatch.setattr(m5, "bridge_post", fake_bridge_post)
    sent["ref"] = REF
    return m5, sent, uid


def _signal_doc(fresh_store, **kw):
    doc = {"signal_id": "SIG-20261024-001", "userId": "u1", "day": "2026-10-24",
           "strategy_id": "strategy_gold_orb", "strategy_name": "Gold ORB",
           "market": "XAUUSD", "timeframe": "5M", "direction": "SELL",
           "entry": 2400.0, "entry_zone": [2400.0, 2400.0], "sl": 2410.0,
           "tp1": 2385.0, "tp2": None, "tp3": None, "risk": 10.0,
           "rr_primary": 1.5, "score": 60, "candle_time": "2026-10-24T14:05:00+00:00",
           "status": "ACTIVE", "completed": False}
    doc.update(kw)
    return fresh_store.create("signals", doc)


def test_single_target_signal_sends_the_strategy_tp(exec_env, fresh_store, monkeypatch):
    """THE September bug, regression-tested: EXECUTION_TP_LEVEL=2 must NOT
    replace a single-target (spec) signal's own TP."""
    m5, sent, uid = exec_env
    monkeypatch.setattr(m5.settings, "execution_tp_level", "2", raising=False)
    sig = _signal_doc(fresh_store)
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    assert sent.get("path") == "/execute", f"no order sent: {sig.get('execution_status')}"
    p = sent["payload"]
    assert p["tp"] == 2385.0                       # the strategy's own 1.5R target
    assert p["sl"] == 2410.0 and p["symbol"] == "XAUUSD" and p["direction"] == "SELL"
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc["execution_status"] == "SUBMITTED"
    assert doc["intended_rr"] == pytest.approx(1.5, abs=1e-6)
    assert doc["executed_rr"] is not None          # measured from the fill


def test_multi_target_signal_still_uses_the_level_setting(exec_env, fresh_store, monkeypatch):
    """Legacy multi-TP signals keep the TP-level manager (rules preserved)."""
    m5, sent, uid = exec_env
    monkeypatch.setattr(m5.settings, "execution_tp_level", "3", raising=False)
    sig = _signal_doc(fresh_store, strategy_id="strategy_h1_trend_breakout",
                      rr_primary=1.0, tp1=2390.0, tp2=2380.0, tp3=2370.0)
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    assert sent.get("path") == "/execute"
    assert sent["payload"]["tp"] == 2370.0         # level 3 of a genuine 3-TP signal


def test_rr_mismatch_blocks_the_order(exec_env, fresh_store):
    """A signal whose TP no longer matches its configured R:R is REFUSED."""
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store, rr_primary=1.5, tp1=2390.0)   # 1R target on 1.5R config
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    assert "path" not in sent, "an R:R-mismatched order reached the broker"
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc["execution_status"] == "REJECTED_PRETRADE"
    assert "RR_MISMATCH" in (doc.get("mt5_note") or "")


def test_stale_entry_blocks_the_order(exec_env, fresh_store):
    """The broker price has moved >0.25R from the intended entry: refuse,
    never re-anchor (the 0.31-0.71 fillRR incidents, regression-tested)."""
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store)
    REF = sent["ref"]; REF["price"] = 2393.0       # $7 through a $10 stop = 0.7R
    m5.execute_signal(sig, uid)
    assert "path" not in sent
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc["execution_status"] == "REJECTED_PRETRADE"
    assert "ENTRY_STALE" in (doc.get("mt5_note") or "")


def test_buy_direction_validated_end_to_end(exec_env, fresh_store):
    """BUY order: side-correct, sized, submitted with the spec geometry."""
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store, direction="BUY", entry=2400.0, sl=2390.0,
                      tp1=2415.0, rr_primary=1.5, risk=10.0)
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    p = sent.get("payload")
    assert p, "valid BUY was refused"
    assert p["direction"] == "BUY" and p["sl"] == 2390.0 < p["tp"] == 2415.0
    assert p["lots"] > 0


def test_unsupported_symbol_is_refused_loudly(exec_env, fresh_store):
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store, market="USDCHKD")
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    assert "path" not in sent
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc["execution_status"] == "REJECTED_PRETRADE"
    assert "SYMBOL_UNSUPPORTED" in (doc.get("mt5_note") or "")


def test_no_price_reference_fails_closed(exec_env, fresh_store, monkeypatch):
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store)
    REF = sent["ref"]

    def no_rates(path, timeout=8):
        return {"balance": 10_000.0} if path == "/account" else None
    monkeypatch.setattr(m5, "bridge_get", no_rates)   # account up, rates down
    m5.execute_signal(sig, uid)
    assert "path" not in sent
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc["execution_status"] == "REJECTED_PRETRADE"


def test_order_rr_recorded_even_on_refusal(exec_env, fresh_store):
    m5, sent, uid = exec_env
    sig = _signal_doc(fresh_store, rr_primary=1.5, tp1=2390.0)
    REF = sent["ref"]; REF["price"] = 2400.0
    m5.execute_signal(sig, uid)
    doc = fresh_store.list("signals", filters={"id": sig["id"]}, limit=1)[0]
    assert doc.get("order_rr") == pytest.approx(1.0, abs=1e-6)


# ===========================================================================
# s.4 - sizing across instruments (tick-size aware pip tables)
# ===========================================================================
def test_calc_lot_unknown_symbol_is_zero_not_fallback():
    from app.execution.mt5 import calc_lot
    assert calc_lot("USDCHKD", 100.0, 101.0, 10_000.0, 1.0) == 0.0


def test_calc_lot_silver_and_nas100_are_sane():
    from app.execution.mt5 import calc_lot
    # XAUUSD reference: 1% of $10k = $100 risk; stop 120 pips x $10 = $1200/lot -> 0.08
    assert calc_lot("XAUUSD", 2400.0, 2412.0, 10_000.0, 1.0) == pytest.approx(0.08, abs=0.011)
    # XAGUSD: stop $0.30 = 30 pips x $50 = $1500/lot -> 0.06
    assert calc_lot("XAGUSD", 30.0, 30.30, 10_000.0, 1.0) == pytest.approx(0.06, abs=0.011)
    # NAS100: stop 20 points x $1 = $20/lot -> 5.0 lots (cap applies upstream)
    assert calc_lot("NAS100", 21000.0, 21020.0, 10_000.0, 1.0) == pytest.approx(5.0, abs=0.011)


# ===========================================================================
# s.1 - bridge symbol resolution (aliases; never a silent different symbol)
# ===========================================================================
def test_bridge_symbol_aliases_parse():
    sys.modules.setdefault("MetaTrader5", ModuleType("MetaTrader5"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bridge", __import__("os").path.join(
            __import__("os").path.dirname(__file__), "..", "..", "vps-bridge", "bridge.py"))
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    aliases = br.parse_symbol_aliases("XAUUSD=XAUUSD.m,XAUUSDm; NAS100=USTEC")
    assert aliases == {"XAUUSD": ["XAUUSD.m", "XAUUSDm"], "NAS100": ["USTEC"]}
    assert br.ALIASES["XAUUSD"][0] == "XAUUSD"      # exact name tried first
    junk = br.parse_symbol_aliases(";;broken; = x")
    assert junk == {}


# ===========================================================================
# s.4 - duplicate / one-per-setup protection still active (unchanged path)
# ===========================================================================
def test_setup_dup_guard_still_blocks(fresh_store, exec_env):
    from app.execution.mt5 import _setup_already_sent
    m5, sent, uid = exec_env
    a = _signal_doc(fresh_store)
    fresh_store.update("signals", a["id"], {"execution_status": "SUBMITTED",
                                            "mt5_ticket": 1})
    b = _signal_doc(fresh_store, signal_id="SIG-20261024-002")
    assert _setup_already_sent(fresh_store, b) == "SIG-20261024-001"
