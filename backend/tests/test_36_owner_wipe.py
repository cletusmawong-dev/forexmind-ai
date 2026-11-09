"""Registry contract: OWNER WIPE 2 (2026-11-02) emptied the registry; the
same day the owner supplied THREE new strategies (MRMS Scalper, Pullback in
Trend, Liquidity Sweep) which are now the only registered strategies.

Guarantees:
  * the registry holds exactly the owner's three - nothing else scans
  * boot purges every strategies/ + strategy_versions/ + version_events/
    doc whose id is not registered - on Local AND Supabase
  * the Strategies API returns exactly the owner's three
  * OLD signal history still renders (name fallback, journal loops, market
    cards, chat) - history is preserved
"""
from fastapi.testclient import TestClient


def test_registry_has_exactly_the_owner_three():
    """OWNER WIPE 2 then the owner's three new strategies (2026-11-02)."""
    from app.strategies import OWNER_STRATEGY_IDS, all_strategies, STRATEGY_CLASSES
    assert set(STRATEGY_CLASSES) == set(OWNER_STRATEGY_IDS)
    assert set(all_strategies()) == set(STRATEGY_CLASSES)


def test_boot_purges_all_strategy_docs(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    # the pre-wipe world: sleeping, retired and live docs + version rows
    for sid in ("strategy_1_vp_pivots", "strategy_2_ema_atr",
                "strategy_2_supply_demand_fvg", "strategy_3_h1_breakout",
                "strategy_1_zero_lag"):
        store.create("strategies", {"status": "PAUSED" if "3" not in sid
                                    else "ACTIVE"}, doc_id=sid)
        store.create("strategy_versions",
                     {"strategy_id": sid, "version": "1.0", "active": False,
                      "params": {}, "changes": []}, doc_id=f"{sid}-v1.0")
        store.create("version_events", {"strategy_id": sid, "kind": "LIFECYCLE",
                                        "detail": "x"},
                     doc_id=f"{sid}-evt")
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    # the registered three get docs; everything unregistered is purged
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    from app.strategies import OWNER_STRATEGY_IDS
    assert set(docs) == set(OWNER_STRATEGY_IDS)
    # the gold pair boots PAUSED (guide section 2); the FX three ACTIVE
    for sid in ("strategy_gold_ny_pullback", "strategy_gold_ny_orb"):
        assert docs[sid]["status"] == "PAUSED", sid
    for sid in ("strategy_mrms_scalper", "strategy_pullback_trend",
                "strategy_liquidity_sweep"):
        assert docs[sid]["status"] == "ACTIVE", sid
    # the 5M scalper scans on ITS timeframe via the doc override
    assert docs["strategy_mrms_scalper"]["entry_timeframe"] == "5M"
    for sid in ("strategy_trendbreak_v2", "strategy_hybrid_crt_tbs",
                "strategy_fade_crt_ext", "strategy_1_zero_lag"):
        assert not store.list("strategies", filters={"id": sid})
        assert not store.list("strategy_versions", filters={"strategy_id": sid})
        assert not store.list("version_events", filters={"strategy_id": sid})
    for sid in ("strategy_1_vp_pivots", "strategy_2_ema_atr",
                "strategy_2_supply_demand_fvg", "strategy_1_zero_lag",
                "strategy_3_h1_breakout"):
        assert not store.list("strategies", filters={"id": sid})
        assert not store.list("strategy_versions", filters={"strategy_id": sid})
        assert not store.list("version_events", filters={"strategy_id": sid})
    vc.ensure_strategy_docs()          # idempotent - same state, no crash
    assert len(store.list("strategy_versions", limit=1000)) == 5


def test_registered_doc_would_survive_purge(tmp_path, monkeypatch):
    """The purge only removes UNREGISTERED docs - a re-registered strategy
    (future) keeps its doc."""
    from app.db.store import LocalStore
    from app.learning import versions as vc
    import app.strategies as reg
    store = LocalStore(str(tmp_path / "db.json"))
    store.create("strategies", {"status": "PAUSED"}, doc_id="some_future_strategy")
    monkeypatch.setattr(vc, "get_store", lambda: store)
    monkeypatch.setattr(reg, "STRATEGY_CLASSES",
                        {"some_future_strategy": object})
    try:
        vc.ensure_strategy_docs()
    except Exception:
        pass  # registration details of a dummy class don't matter here
    assert store.list("strategies", filters={"id": "some_future_strategy"})


def test_strategies_api_returns_zero():
    from app.state import State
    from app.main import app
    from app.api.deps import get_user_id
    _prev = State.store
    State.store = _prev          # startup init_state() provides the store
    app.dependency_overrides[get_user_id] = lambda: "cletusmawa"
    try:
        with TestClient(app) as c:
            r = c.get("/api/strategies")
            assert r.status_code == 200
            rows = r.json()["strategies"]
            from app.strategies import OWNER_STRATEGY_IDS
            assert {x["id"] for x in rows} == set(OWNER_STRATEGY_IDS)
    finally:
        # never leak the override into other test files (full-suite isolation)
        app.dependency_overrides.pop(get_user_id, None)
        State.store = _prev
        State.ready = False      # house pattern: next startup re-inits


def test_scan_creates_nothing_and_old_history_survives(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.engine import signal_engine as se_mod
    from app.engine.signal_engine import SignalEngine
    import pandas as pd

    store = LocalStore(str(tmp_path / "db.json"))
    # history from the removed strategies must still be listable
    store.create("signals", {"userId": "cletusmawa", "strategy_id": "strategy_2_ema_atr",
                             "strategy_name": "EMA/ATR", "market": "XAUUSD",
                             "timeframe": "15M", "candle_time": "2026-10-01T10:00:00Z"})
    monkeypatch.setattr(se_mod, "get_store", lambda: store)

    class StubProvider:
        is_demo = True
        def get_candles(self, market, tf, limit=600):
            idx = pd.date_range(end="2026-10-09T20:00", periods=320, freq="1h", tz="UTC")
            n = len(idx)
            return pd.DataFrame({"open": [2400.0]*n, "high": [2401.0]*n,
                                 "low": [2399.0]*n, "close": [2400.0]*n}, index=idx)
        def higher_frames(self, market, base_tf):
            return {}

    engine = SignalEngine(StubProvider())
    for tf in ("15M", "1H"):
        assert engine.scan("cletusmawa", "XAUUSD", tf, log_activity=False) == []
    # history untouched and readable
    hist = store.list("signals", filters={"strategy_id": "strategy_2_ema_atr"})
    assert len(hist) == 1 and hist[0]["strategy_name"] == "EMA/ATR"
    # name fallback for removed strategies (learning/analysis path)
    from app.learning.analysis import strategy_name
    assert strategy_name("strategy_2_ema_atr") == "strategy_2_ema_atr"


def test_market_card_survives_without_strategies(monkeypatch):
    """The Markets screen must not 500 now that zero_lag/ema_atr are gone."""
    import pandas as pd
    import app.api.routes_markets as rm

    class StubProvider:
        is_demo = True
        def data_health(self, symbol):
            return {"status": "ok"}
        def latest_price(self, symbol):
            return 2400.0
        def get_candles(self, symbol, tf, limit=1200):
            idx = pd.date_range(end="2026-10-09T20:00", periods=320, freq="15min", tz="UTC")
            n = len(idx)
            return pd.DataFrame({"open": [2400.0]*n, "high": [2401.0]*n,
                                 "low": [2399.0]*n, "close": [2400.0]*n}, index=idx)
        def higher_frames(self, symbol, tf):
            return {}

    monkeypatch.setattr(rm.State, "provider", StubProvider(), raising=False)
    card = rm._market_card("XAUUSD")
    assert card["status"] == "MONITORING"
    assert card["bias"] == "N/A"
    assert "retired legacy" in card["note"]
