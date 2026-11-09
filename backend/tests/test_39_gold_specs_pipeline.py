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
def test_registry_has_the_owner_three_after_wipe2():
    """OWNER WIPE 2 (2026-11-02) + the owner's three new strategies: the
    gold specs are UNREGISTERED (modules kept for history, docs purged)."""
    import app.strategies as reg
    assert set(reg.STRATEGY_CLASSES.keys()) == {
        "strategy_mrms_scalper", "strategy_pullback_trend",
        "strategy_liquidity_sweep"}
    for sid in NEW_IDS:
        assert sid not in reg.STRATEGY_CLASSES

def test_old_strategies_stay_unregistered():
    import app.strategies as reg
    for sid in ("strategy_1_vp_pivots", "strategy_2_ema_atr",
                "strategy_2_supply_demand_fvg", "strategy_1_zero_lag",
                "strategy_2_mtf_sweep_bos_retest", "strategy_3_h1_breakout",
                "strategy_3_opening_range", "strategy_3_liquidity_sweep",
                "strategy_3_crt_4h_15m"):
        assert sid not in reg.STRATEGY_CLASSES


# ------------------------------------------------------- docs ship PAUSED
def test_boot_creates_owner_three_docs_active(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert set(docs) == {"strategy_mrms_scalper", "strategy_pullback_trend",
                         "strategy_liquidity_sweep"}
    assert all(d["status"] == "ACTIVE" for d in docs.values())
    for sid in NEW_IDS:
        assert not store.list("strategies", filters={"id": sid})

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


def test_activation_gate_runs_the_registered_strategies(tmp_path, monkeypatch):
    """The doc-status gate machinery survives the wipe: a PAUSED registered
    strategy never scans; an ACTIVE one does (owner's three)."""
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    store.update("strategies", "strategy_mrms_scalper", {"status": "PAUSED"})
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert docs["strategy_mrms_scalper"]["status"] == "PAUSED"
    assert docs["strategy_pullback_trend"]["status"] == "ACTIVE"
    store.update("strategies", "strategy_mrms_scalper", {"status": "ACTIVE"})
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert docs["strategy_mrms_scalper"]["status"] == "ACTIVE"
def test_strategies_api_lists_the_owner_three():
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
            assert {s["id"] for s in rows} == {
                "strategy_mrms_scalper", "strategy_pullback_trend",
                "strategy_liquidity_sweep"}
    finally:
        app.dependency_overrides.pop(get_user_id, None)
        State.store = _prev
        State.ready = False
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
def test_registry_has_the_owner_three_after_wipe2():
    """OWNER WIPE 2 (2026-11-02) + the owner's three new strategies: the
    gold specs are UNREGISTERED (modules kept for history, docs purged)."""
    import app.strategies as reg
    assert set(reg.STRATEGY_CLASSES.keys()) == {
        "strategy_mrms_scalper", "strategy_pullback_trend",
        "strategy_liquidity_sweep"}
    for sid in NEW_IDS:
        assert sid not in reg.STRATEGY_CLASSES

def test_old_strategies_stay_unregistered():
    import app.strategies as reg
    for sid in ("strategy_1_vp_pivots", "strategy_2_ema_atr",
                "strategy_2_supply_demand_fvg", "strategy_1_zero_lag",
                "strategy_2_mtf_sweep_bos_retest", "strategy_3_h1_breakout",
                "strategy_3_opening_range", "strategy_3_liquidity_sweep",
                "strategy_3_crt_4h_15m"):
        assert sid not in reg.STRATEGY_CLASSES


# ------------------------------------------------------- docs ship PAUSED
def test_boot_creates_owner_three_docs_active(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert set(docs) == {"strategy_mrms_scalper", "strategy_pullback_trend",
                         "strategy_liquidity_sweep"}
    assert all(d["status"] == "ACTIVE" for d in docs.values())
    for sid in NEW_IDS:
        assert not store.list("strategies", filters={"id": sid})

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


def test_activation_gate_runs_the_registered_strategies(tmp_path, monkeypatch):
    """The doc-status gate machinery survives the wipe: a PAUSED registered
    strategy never scans; an ACTIVE one does (owner's three)."""
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    store.update("strategies", "strategy_mrms_scalper", {"status": "PAUSED"})
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert docs["strategy_mrms_scalper"]["status"] == "PAUSED"
    assert docs["strategy_pullback_trend"]["status"] == "ACTIVE"
    store.update("strategies", "strategy_mrms_scalper", {"status": "ACTIVE"})
    docs = {d["id"]: d for d in store.list("strategies", limit=1000)}
    assert docs["strategy_mrms_scalper"]["status"] == "ACTIVE"
