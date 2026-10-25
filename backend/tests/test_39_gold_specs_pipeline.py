"""Gold specs in the LIVE pipeline (2026-10-10).

The two spec strategies (plus TPC-15, owner-registered 2026-10-24) are the
registered strategies after the owner wipe. Per spec rule 7 their docs are created PAUSED: nothing is scanned,
no signal can be created, nothing can trade - activation is an explicit
owner step. These tests pin that state AND prove the activation path works
(PATCH to ACTIVE -> scan -> signal -> executor stays honest in tests).
"""
from fastapi.testclient import TestClient

NEW_IDS = ("strategy_h1_trend_breakout", "strategy_gold_orb")


# ------------------------------------------------------------- registry
def test_registry_contains_exactly_the_two_specs():
    import app.strategies as reg
    assert set(reg.STRATEGY_CLASSES.keys()) == set(NEW_IDS) | {"strategy_tpc15_pullback", "strategy_trendbreak_h1", "strategy_zonesweep_m15", "strategy_trendbreak_v2", "strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"}
    for sid in NEW_IDS:
        s = reg.STRATEGY_CLASSES[sid]()
        assert s.markets is None   # owner brief 2026-10-24: no instrument pin
        assert s.base_params == {} and s.experiment_variables == {}   # spec-fixed


def test_old_strategies_stay_unregistered():
    import app.strategies as reg
    for sid in ("strategy_1_vp_pivots", "strategy_2_ema_atr",
                "strategy_2_supply_demand_fvg", "strategy_1_zero_lag",
                "strategy_2_mtf_sweep_bos_retest", "strategy_3_h1_breakout",
                "strategy_3_opening_range", "strategy_3_liquidity_sweep",
                "strategy_3_crt_4h_15m"):
        assert sid not in reg.STRATEGY_CLASSES


# ------------------------------------------------------- docs ship PAUSED
def test_boot_creates_spec_docs_paused(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    for sid in NEW_IDS:
        doc = store.list("strategies", filters={"id": sid}, limit=1)
        assert doc and doc[0]["status"] == "PAUSED", sid   # rule 7 state


def test_scan_produces_nothing_while_paused(tmp_path, monkeypatch):
    """Rule 7: live execution disabled - while PAUSED the engine gate skips
    the strategies entirely (no scan, no signal, no execution)."""
    from app.db.store import LocalStore
    from app.engine import signal_engine as se_mod
    from app.engine.signal_engine import SignalEngine
    from app.learning import versions as vc
    import pandas as pd

    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(se_mod, "get_store", lambda: store)
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()                          # docs PAUSED

    idx = pd.date_range(end="2026-10-09T20:00", periods=500, freq="1h", tz="UTC")
    rng = [2400 + (i % 7) for i in range(500)]
    h1 = pd.DataFrame({"open": rng, "high": [x + 4 for x in rng],
                       "low": [x - 4 for x in rng], "close": rng}, index=idx)

    class StubProvider:
        is_demo = True
        def get_candles(self, market, tf, limit=600):
            return h1
        def higher_frames(self, market, base_tf):
            return {"5M": h1, "15M": h1, "1H": h1, "4H": h1}

    engine = SignalEngine(StubProvider())
    for tf in ("15M", "1H", "4H"):
        assert engine.scan("cletusmawa", "XAUUSD", tf, log_activity=False) == []
    assert store.list("signals", limit=100) == []


# ---------------------------------------------------- the activation path
def _fire_frames():
    """Frames that make H1 Trend Breakout fire (rising + breakout last bar)."""
    import pandas as pd
    idx = pd.date_range(end="2026-10-09T20:00", periods=300, freq="1h", tz="UTC")
    o, h, l, c = [], [], [], []
    px = 2400.0
    for i in range(300):
        if i < 200:
            o.append(px); h.append(px + 0.5); l.append(px - 0.5); c.append(px)
        else:
            px += 2.0
            o.append(px - 2.0); h.append(px + 0.6); l.append(px - 2.4); c.append(px)
    h1 = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)
    h1.loc[h1.index[-1], "close"] = px + 3.4
    h1.loc[h1.index[-1], "high"] = px + 3.6
    m15 = pd.DataFrame({"open": [2440.0] * 4, "high": [2441.0] * 4,
                        "low": [2439.0] * 4, "close": [2440.5] * 4},
                       index=pd.date_range(start="2026-10-09T21:00", periods=4,
                                           freq="15min", tz="UTC"))
    qidx = pd.date_range(end="2026-10-09T16:00", periods=210, freq="4h", tz="UTC")
    qo, qh, ql, qc = [], [], [], []
    qpx = 2350.0
    for i in range(210):
        if i >= 170:
            qpx += 3.0
        qo.append(qpx - 1.0); qh.append(qpx + 1.2); ql.append(qpx - 2.2); qc.append(qpx)
    h4 = pd.DataFrame({"open": qo, "high": qh, "low": ql, "close": qc}, index=qidx)
    return h1, m15, h4


def test_activation_patch_enables_scan_and_logs_checks(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.engine import signal_engine as se_mod
    from app.engine.signal_engine import SignalEngine
    from app.learning import versions as vc
    from app.execution import mt5 as mt5_mod

    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(se_mod, "get_store", lambda: store)
    monkeypatch.setattr(vc, "get_store", lambda: store)
    monkeypatch.setattr(mt5_mod, "get_store", lambda: store)
    vc.ensure_strategy_docs()                          # PAUSED docs

    # owner activation = the existing audited status PATCH (doc-level)
    store.update("strategies", "strategy_h1_trend_breakout", {"status": "ACTIVE"})

    h1, m15, h4 = _fire_frames()

    class StubProvider:
        is_demo = True
        def get_candles(self, market, tf, limit=600):
            return {"1H": h1, "15M": m15, "4H": h4, "5M": m15}.get(tf, h1)
        def higher_frames(self, market, base_tf):
            return {"5M": m15, "15M": m15, "1H": h1, "4H": h4}

    engine = SignalEngine(StubProvider())
    created = engine.scan("cletusmawa", "XAUUSD", "1H", log_activity=False)
    mine = [c for c in created if c["strategy_id"] == "strategy_h1_trend_breakout"]
    assert mine, "activated strategy must create a signal"
    doc = store.list("signals", filters={"strategy_id": "strategy_h1_trend_breakout"},
                     limit=1)[0]
    assert doc["timeframe"] == "1H" and doc["market"] == "XAUUSD"
    assert doc.get("execution_status") == "SKIPPED_ADVISORY_MODE"  # honest, no order
    checks = doc.get("checks") or doc.get("why", {}).get("checks") if \
        isinstance(doc.get("why"), dict) else doc.get("checks")
    assert checks, "condition log must travel with the signal (rule 6)"
    assert any("ADX" in c["label"] for c in checks)

    # ORB stays PAUSED -> never fires even though frames flow
    created2 = engine.scan("cletusmawa", "XAUUSD", "15M", log_activity=False)
    assert all(c["strategy_id"] != "strategy_gold_orb" for c in created2)


# ---------------------------------------------------- strategies API view
def test_strategies_api_lists_both_paused():
    from app.state import State
    from app.main import app
    from app.api.deps import get_user_id
    _prev = State.store
    app.dependency_overrides[get_user_id] = lambda: "cletusmawa"
    try:
        with TestClient(app) as c:
            r = c.get("/api/strategies")
            assert r.status_code == 200
            rows = r.json()["strategies"]
            by_id = {s["id"]: s for s in rows}
            live_ids = {"strategy_tpc15_pullback", "strategy_trendbreak_h1",
                        "strategy_zonesweep_m15", "strategy_trendbreak_v2",
                        "strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"}
            assert set(by_id) == set(NEW_IDS) | live_ids
            for sid in NEW_IDS:
                assert by_id[sid]["status"] == "PAUSED"    # spec rule 7 holds
            for sid in live_ids:
                assert by_id[sid]["status"] == "ACTIVE"    # owner directives
    finally:
        app.dependency_overrides.pop(get_user_id, None)
        State.store = _prev
        State.ready = False
