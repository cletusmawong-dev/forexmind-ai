"""Owner directives 2026-10-25 (part 2):

1. EXECUTION_FAILED is loud on Telegram again (owner approval).
2. A PARTIAL exit deal (e.g. the PINSTRIPE TP1 50%) never completes a
   trade early - only the full-volume close confirms + announces.
3. Per-strategy entry-timeframe setting: validated strategy PATCH,
   honest metadata on the list, the scanner fetches + threads the
   configured frame, candidates carry the effective timeframe.
4. Settings validation errors are client errors (422), never 500s.
5. Propfirm daily-drawdown buffer blocks new entries (works end-to-end).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("with_strategies")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    # manual _store swap (NOT monkeypatch): monkeypatch-based teardowns
    # cascade - each later test's undo restores the PREVIOUS test's tmp
    # store, so the module can end with a tmp store as the "global". A
    # manual restore pins the true original.
    orig_store = store_mod._store
    store_mod._store = store
    from app.state import State
    _prev = State.store
    State.store = store
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c, store, monkeypatch
    finally:
        app.dependency_overrides.pop(get_user_id, None)
        # never hand later modules a dead singleton: init_state() runs once
        # per process, so State.store must stay a WORKING, SEEDED store.
        # Restore the true original _store first; if nothing was set before
        # this test, hand over the global default WITH the demo user
        # (api_security-style modules log the demo user in).
        # ABSOLUTE handoff invariant (background replay threads from the
        # TestClients race singleton rebuilds, so "restore what was there"
        # is not enough): both singletons must end up the SAME LIVE,
        # DEMO-SEEDED store - login writes via get_store(), routes read via
        # State.store, and api_security-style modules log the demo user in.
        store_mod._store = None
        State.store = store_mod.get_store()
        try:
            from app.seed import _ensure_demo_user
            _ensure_demo_user()
        except Exception:
            pass


# ===========================================================================
# 1. EXECUTION_FAILED on Telegram
# ===========================================================================
def test_execution_failed_is_telegram_worthy():
    from app.notifications.service import _TELEGRAM_ALLOWED_EXACT, _telegram_worthy
    assert "EXECUTION_FAILED" in _TELEGRAM_ALLOWED_EXACT
    assert _telegram_worthy("EXECUTION_FAILED", None) is True


def test_execution_failed_notification_created(env):
    c, store, monkeypatch = env
    store.create("notifications", {
        "userId": "boss", "type": "EXECUTION_FAILED",
        "title": "ENTRY FAILED - XAUUSD SELL", "body": "boom", "read": False})
    rows = store.list("notifications", filters={"type": "EXECUTION_FAILED"},
                      limit=10)
    assert len(rows) == 1 and rows[0]["userId"] == "boss"


# ===========================================================================
# 2. partial exits never complete a trade early
# ===========================================================================
def _deals_sig(store, sig_id="PS1"):
    store.create("signals", {"userId": "boss", "signal_id": sig_id,
                             "strategy_id": "strategy_hybrid_crt_tbs",
                             "market": "EURUSD", "direction": "SELL",
                             "completed": False, "status": "OPEN_MT5"})
    return sig_id


def _deal(position_id, entry, volume, sig_id, profit=0.0, price=1.1):
    return {"magic": 20260914, "position_id": position_id,
            "entry": 0 if entry else 1, "volume": volume,
            "comment": sig_id if entry else "", "profit": profit,
            "commission": 0.0, "swap": 0.0, "price": price}


def test_partial_exit_deal_does_not_complete(env, monkeypatch, tmp_path):
    c, store, monkeypatch = env
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store2 = LocalStore(path=str(tmp_path / "db2.json"))
    monkeypatch.setattr(store_mod, "_store", store2)
    sig_id = _deals_sig(store2)
    from app.execution import mt5
    prev_conf = set(mt5._CONFIRMED_POSITIONS)
    mt5._CONFIRMED_POSITIONS.clear()
    try:
        n = mt5.apply_deals("boss", [_deal("P1", True, 0.2, sig_id),
                                     _deal("P1", False, 0.1, sig_id, profit=3.0)])
        assert n == 0                       # 50% partial: NOT the close
        doc = [d for d in store2.list("signals", limit=10) if d["signal_id"] == sig_id][0]
        assert not doc.get("mt5_confirmed") and not doc.get("completed")
        assert not store2.list("notifications",
                               filters={"type": "TRADE_COMPLETED"}, limit=5)
    finally:
        mt5._CONFIRMED_POSITIONS.clear()
        mt5._CONFIRMED_POSITIONS.update(prev_conf)


def test_full_volume_exit_completes_and_announces(env, monkeypatch, tmp_path):
    c, store, monkeypatch = env
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store2 = LocalStore(path=str(tmp_path / "db3.json"))
    monkeypatch.setattr(store_mod, "_store", store2)
    sig_id = _deals_sig(store2)
    from app.execution import mt5
    prev_conf = set(mt5._CONFIRMED_POSITIONS)
    mt5._CONFIRMED_POSITIONS.clear()
    try:
        # partial first (ignored), then the remainder -> FULL close
        mt5.apply_deals("boss", [_deal("P2", True, 0.2, sig_id),
                                 _deal("P2", False, 0.1, sig_id, profit=3.0)])
        n = mt5.apply_deals("boss", [_deal("P2", True, 0.2, sig_id),
                                     _deal("P2", False, 0.1, sig_id, profit=3.0),
                                     _deal("P2", False, 0.1, sig_id, profit=5.0)])
        assert n == 1
        doc = [d for d in store2.list("signals", limit=10) if d["signal_id"] == sig_id][0]
        assert doc["completed"] and doc["outcome"] == "WIN"
        assert abs(doc["mt5_pl"] - 8.0) < 1e-9     # entry commission + both exits
        notes = store2.list("notifications",
                            filters={"type": "TRADE_COMPLETED"}, limit=5)
        assert len(notes) == 1 and "WIN" in notes[0]["title"]
    finally:
        mt5._CONFIRMED_POSITIONS.clear()
        mt5._CONFIRMED_POSITIONS.update(prev_conf)


# ===========================================================================
# 3. per-strategy entry timeframe
# ===========================================================================
def test_entry_timeframe_patch_validation_and_metadata(env):
    c, store, monkeypatch = env
    r = c.patch("/api/strategies/strategy_zonesweep_m15",
                json={"entry_timeframe": "1H"})
    assert r.status_code == 200
    assert r.json()["strategy"]["entry_timeframe"] == "1H"
    # 30M is not a platform entry timeframe
    r = c.patch("/api/strategies/strategy_zonesweep_m15",
                json={"entry_timeframe": "30M"})
    assert r.status_code == 422
    # gold_orb declares its own spec timeframes - outside them is refused
    r = c.get("/api/strategies")
    rows = {x["id"]: x for x in r.json()["strategies"]}
    assert rows["strategy_zonesweep_m15"]["default_entry_tf"] == "15M"
    assert "1H" in rows["strategy_zonesweep_m15"]["allowed_entry_tfs"]
    gold = rows["strategy_gold_orb"]
    assert gold["allowed_entry_tfs"] == [t for t in ("15M", "1H", "4H", "1D")
                                         if t in gold["allowed_entry_tfs"]]
    # back to the strategy default
    r = c.patch("/api/strategies/strategy_zonesweep_m15",
                json={"entry_timeframe": None})
    assert r.status_code == 200
    assert r.json()["strategy"]["entry_timeframe"] is None


def test_scan_fetches_and_threads_the_configured_timeframe(env):
    c, store, monkeypatch = env
    import pandas as pd
    store.create("strategies", {"id": "strategy_zonesweep_m15",
                                "status": "ACTIVE",
                                "entry_timeframe": "1H"})
    idx = pd.date_range("2026-10-01", periods=400, freq="15min")
    df = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
                       "volume": 10}, index=idx)

    class FakeProvider:
        def get_candles(self, market, tf, limit=600):
            return df

        def higher_frames(self, market, base_tf, frames=None):
            return {"4H": df, "1H": df}

    from app.engine.signal_engine import SignalEngine
    import app.strategies as reg
    zs = reg.all_strategies()["strategy_zonesweep_m15"]
    captured = []
    orig = zs.detect_signal

    def spy(df, market, tf, params=None, higher_frames=None, score_context=None):
        captured.append({"tf": tf, "entry_tf": (params or {}).get("entry_tf"),
                         "bars": len(df)})
        return None

    zs.detect_signal = spy
    try:
        eng = SignalEngine(FakeProvider())
        eng.scan("boss", "EURUSD", "15M")
    finally:
        zs.detect_signal = orig
    assert captured, "zonesweep was not scanned at all"
    calls = [x for x in captured if x["entry_tf"] == "1H"]
    assert calls and calls[0]["tf"] == "1H" and calls[0]["bars"] == 400


def test_scan_without_override_keeps_the_scan_timeframe(env):
    c, store, monkeypatch = env
    import pandas as pd
    idx = pd.date_range("2026-10-01", periods=400, freq="15min")
    df = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
                       "volume": 10}, index=idx)

    class FakeProvider:
        def get_candles(self, market, tf, limit=600):
            return df

        def higher_frames(self, market, base_tf, frames=None):
            return {}

    from app.engine.signal_engine import SignalEngine
    import app.strategies as reg
    zs = reg.all_strategies()["strategy_zonesweep_m15"]
    tfs = []
    orig = zs.detect_signal
    zs.detect_signal = lambda df, m, tf, **kw: (tfs.append(tf), None)[1]
    try:
        SignalEngine(FakeProvider()).scan("boss", "EURUSD", "15M")
    finally:
        zs.detect_signal = orig
    assert tfs and all(t == "15M" for t in tfs)


def test_candidate_carries_the_effective_timeframe():
    """The pinstripe bridge honours params['entry_tf'] for frame resolution
    AND labels the candidate with the effective timeframe."""
    import pandas as pd
    from app.strategies.research_only.live import HybridCrtTbsStrategy
    s = HybridCrtTbsStrategy()
    assert s._eff_tf(None) == "1H"
    assert s._eff_tf({"entry_tf": "15M"}) == "15M"
    idx = pd.date_range("2026-10-01", periods=60, freq="15min")
    df = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1},
                      index=idx)
    entry, ctx = s._context(df, "15M", {"1H": df, "4H": df},
                            {"entry_tf": "15M"})
    assert entry is df                       # resolved from the scan frame
    assert ctx.get("4H") is df


# ===========================================================================
# 4. settings validation: client errors, never 500
# ===========================================================================
def test_settings_invalid_timeframes_is_422(env):
    c, store, monkeypatch = env
    r = c.patch("/api/settings", json={"signal_timeframes": ["9M"]})
    assert r.status_code == 422, r.text


def test_settings_valid_patch_persists(env):
    c, store, monkeypatch = env
    r = c.patch("/api/settings", json={"signal_timeframes": ["15M", "1H"],
                                       "account_type": "personal"})
    assert r.status_code == 200, r.text
    docs = store.list("settings", filters={"userId": "boss", "kind": "risk"},
                      limit=1)
    assert docs and docs[0].get("signal_timeframes") == ["15M", "1H"]


# ===========================================================================
# 5. propfirm daily-drawdown buffer blocks new entries
# ===========================================================================
def _candidate():
    from app.strategies.base import Candidate
    return Candidate(strategy_id="strategy_gold_orb", market="XAUUSD",
                     timeframe="15M", direction="BUY",
                     candle_time=datetime.now(timezone.utc).isoformat(),
                     entry=2400.0, entry_zone=[2400.0, 2400.0], sl=2390.0,
                     tps=[2430.0], risk=10.0, rr_primary=3.0, score=60,
                     score_components={"test": 60.0}, checks=[],
                     analysis="guard test candidate")


def test_propfirm_daily_buffer_blocks_entries(env):
    c, store, monkeypatch = env
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "account_type": "propfirm",
                              "prop_rules": {"daily_drawdown_pct": 5.0,
                                             "daily_dd_buffer_pct": 50.0},
                              "risk_per_trade_pct": 2.0,
                              "max_signals_per_day": 6,
                              "allowed_markets": ["XAUUSD"],
                              "sessions": ["London", "NewYork"]})
    store.create("agent_goals", {"userId": "boss"})
    store.create("signals", {"userId": "boss", "day": today, "completed": True,
                             "r_multiple": -1.25})       # -1.25 x 2.0% = -2.5%
    from app.engine.signal_engine import SignalEngine
    ok, reason = SignalEngine(None)._guards("boss", _candidate(), "London")
    assert ok is False
    assert reason == "prop daily drawdown buffer reached"


def test_personal_account_uses_its_own_limit(env):
    c, store, monkeypatch = env
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "account_type": "personal",
                              "max_daily_loss_pct": 3.0,
                              "risk_per_trade_pct": 2.0,
                              "max_signals_per_day": 6,
                              "allowed_markets": ["XAUUSD"],
                              "sessions": ["London", "NewYork"]})
    store.create("agent_goals", {"userId": "boss"})
    store.create("signals", {"userId": "boss", "day": today, "completed": True,
                             "r_multiple": -1.25})       # -2.5% > -3.0%: allowed
    from app.engine.signal_engine import SignalEngine
    ok, reason = SignalEngine(None)._guards("boss", _candidate(), "London")
    assert ok is True, reason


# ===========================================================================
# 6. per-strategy RECORD-ONLY (research) execution mode
# ===========================================================================
def test_execution_mode_patch_roundtrip(env):
    c, store, monkeypatch = env
    r = c.patch("/api/strategies/strategy_gold_orb", json={"execution": "record_only"})
    assert r.status_code == 200, r.text
    assert r.json()["strategy"]["execution"] == "record_only"
    r = c.patch("/api/strategies/strategy_gold_orb", json={"execution": None})
    assert r.status_code == 200
    assert r.json()["strategy"]["execution"] is None      # None = live
    r = c.patch("/api/strategies/strategy_gold_orb", json={"execution": "yolo"})
    assert r.status_code == 422


def test_record_only_strategy_signals_are_recorded_not_executed(env):
    """The owner's research pipeline: signals are created + tracked, but the
    executor refuses them server-side (never client-trusted)."""
    c, store, monkeypatch = env
    import json as _json
    from app.config import settings as _s
    monkeypatch.setattr(_s, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(_s, "bridge_url", "http://fake-bridge:8700")
    store.create("strategies", {"id": "strategy_gold_orb", "status": "ACTIVE",
                                "execution": "record_only"})
    from app.execution import mt5 as X

    def boom(path, timeout=8):
        raise AssertionError("bridge MUST NOT be called in record-only mode")
    monkeypatch.setattr(X, "bridge_get", boom)
    monkeypatch.setattr(X, "bridge_post", boom)
    monkeypatch.setattr(X, "execution_enabled", lambda uid: True)

    from app.db import store as store_mod
    real_store = store
    monkeypatch.setattr(X, "get_store", lambda: real_store)
    sig = {"id": "sig_ro1", "userId": "boss", "signal_id": "SIG-RO1",
           "strategy_id": "strategy_gold_orb", "market": "XAUUSD",
           "direction": "BUY", "timeframe": "15M", "entry": 2400.0,
           "sl": 2390.0, "tp1": 2430.0, "risk": 10.0, "status": "ACTIVE",
           "candle_time": "2026-10-25T10:00:00"}
    store.create("signals", dict(sig))
    X.execute_signal(dict(sig), "boss")
    doc = store.get("signals", "sig_ro1")
    assert doc["execution_status"] == "SKIPPED_RECORD_ONLY"
    assert "record-only" in (doc.get("mt5_note") or "")


def test_virtual_signal_outcomes_never_hit_telegram():
    """Standing rule: TP/completion announcements come from REAL tickets.
    Record-only research outcomes stay in-app under VIRTUAL_* types."""
    from app.engine.tracker import _is_real
    assert _is_real({"mt5_ticket": 123}) is True
    assert _is_real({"mt5_command_id": "c1"}) is True
    assert _is_real({"mt5_ticket": None}) is False
    assert _is_real({}) is False

    from app.notifications.service import _telegram_worthy
    assert _telegram_worthy("VIRTUAL_TRADE_COMPLETED", None) is False
    assert _telegram_worthy("TP1_HIT_VIRTUAL", None) is False
    assert _telegram_worthy("TRADE_COMPLETED", None) is True
    assert _telegram_worthy("TP2_HIT", None) is True


def test_tracker_completes_virtual_signal_with_silent_type(env):
    c, store, monkeypatch = env
    store.create("signals", {"id": "sig_v1", "userId": "boss",
                             "signal_id": "SIG-V1", "strategy_name": "Gold ORB",
                             "market": "XAUUSD",
                             "direction": "BUY", "timeframe": "15M",
                             "entry": 2400.0, "sl": 2390.0, "tp1": 2430.0,
                             "risk": 10.0, "status": "TP1_HIT", "tp_hits": 0,
                             "completed": False})
    from app.engine.tracker import SignalTracker
    st = SignalTracker.__new__(SignalTracker)   # no provider needed
    st._exc = {}
    sig = store.get("signals", "sig_v1")
    SignalTracker._complete(st, sig, status="SL_HIT", r=-1.0, exit_price=2390.0,
                            outcome="LOSS")
    doc = store.get("signals", "sig_v1")
    assert doc["completed"] and doc["outcome"] == "LOSS"
    notes = store.list("notifications",
                       filters={"type": "VIRTUAL_TRADE_COMPLETED"}, limit=5)
    assert len(notes) == 1 and notes[0]["userId"] == "boss"
    assert not store.list("notifications",
                          filters={"type": "TRADE_COMPLETED"}, limit=5)
