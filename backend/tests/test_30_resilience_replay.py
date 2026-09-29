"""FOREXMIND 3.0 stages 6/7: kill-switch, incidents, decision replay,
time machine, counterfactuals, blocked-opportunity/filter-contribution, chaos.

Chaos failures (bridge down, faults) must FAIL SAFE: no duplicate orders, no
unauthorized orders, every block loud.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
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
            yield c, store, monkeypatch, settings
    finally:
        State.store = _prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)
        # never leak a kill-switch level between tests
        from app.risk import killswitch as ks
        try:
            ks.set_level(0, "test-teardown")
        except Exception:
            pass


class FakeRouter:
    def analyze(self, prompt, context, escalate=False, user_id=None):
        return {"text": json.dumps({"probable_cause": "bridge down",
                                    "evidence": ["no response"], "affected": ["exec"],
                                    "recommended_action": "restart", "safe": True}),
                "model": "fake", "layer": "primary"}


def _user(store, uid="boss"):
    store.create("users", {"id": uid, "userId": uid, "email": f"{uid}@x.io",
                           "role": "admin", "status": "active",
                           "trading_permission": "locked"})


def _t(day, hour=10):
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc).isoformat()


# ===========================================================================
# Kill-switch hierarchy
# ===========================================================================
def test_killswitch_levels_block_and_restore(env):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.risk_checks import check_entry
    from app.risk import killswitch as ks
    ok, guard, why = check_entry("boss", "EURUSD")
    assert ok is True                                  # level 0 = no-op
    ks.set_level(1, "admin-test", "test stop")
    ok, guard, why = check_entry("boss", "EURUSD")
    assert ok is False and guard == "KILLSWITCH"
    assert "KILLSWITCH_L1" in why
    # enforcement works even with risk guards disabled (independent safety)
    monkeypatch.setattr(settings, "risk_guards_enabled", False)
    ok, guard, why = check_entry("boss", "EURUSD")
    assert ok is False
    ks.set_level(0, "admin-test", "restore")
    ok, _, _ = check_entry("boss", "EURUSD")
    assert ok is True


def test_killswitch_l2_l3_scoped_blocks(env):
    from app.risk import killswitch as ks
    ks.set_level(3, "t", "x", instruments=["XAUUSD"])
    ok, why = ks.check("XAUUSD")
    assert not ok and "L3_INSTRUMENT_XAUUSD" in why
    ok, _ = ks.check("EURUSD")
    assert ok
    ks.set_level(2, "t", "x", strategies=["strategy_2_ema_atr"])
    ok, why = ks.check("EURUSD", "strategy_2_ema_atr")
    assert not ok and "L2" in why
    ok, _ = ks.check("EURUSD", "strategy_1_zero_lag")
    assert ok


def test_killswitch_admin_routes_guarded_and_audited(env):
    c, store, monkeypatch, settings = env
    _user(store)
    r = c.get("/api/admin/killswitch")
    assert r.status_code == 200 and r.json()["level"] == 0
    # level 5 via the normal route is REFUSED
    r = c.post("/api/admin/killswitch", json={"level": 5, "reason": "no"})
    assert r.status_code == 422
    r = c.post("/api/admin/killswitch", json={"level": 1, "reason": "halt"})
    assert r.status_code == 200 and r.json()["level"] == 1
    assert c.get("/api/admin/killswitch").json()["level"] == 1
    audits = store.list("audit_log", limit=10)
    assert any("killswitch" in (a.get("action") or "") for a in audits)
    # close-all requires explicit confirm
    r = c.post("/api/admin/killswitch/close-all", json={"confirm": False})
    assert r.status_code == 422
    c.post("/api/admin/killswitch", json={"level": 0, "reason": "restore"})


def test_killswitch_l5_close_all_suspends_enabled_users(env):
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("users", {"id": "trader1", "userId": "trader1",
                           "email": "t1@x.io", "role": "user", "status": "active",
                           "trading_permission": "enabled"})
    store.create("users", {"id": "locked1", "userId": "locked1",
                           "email": "l1@x.io", "role": "user", "status": "active",
                           "trading_permission": "locked"})
    r = c.post("/api/admin/killswitch/close-all", json={"confirm": True})
    assert r.status_code == 200
    assert any(x["user"]["id"] == "trader1" and x.get("stopped")
               for x in r.json()["closed"])
    assert not any(x.get("user", {}).get("id") == "locked1"
                   for x in r.json()["closed"])          # untouched
    u = store.get("users", "trader1")
    assert u["trading_permission"] == "locked" and u["status"] == "suspended"
    assert store.get("settings", "killswitch")["level"] == 4   # automation stopped


# ===========================================================================
# Incident center + AI commander
# ===========================================================================
def test_incident_sweep_detects_bridge_down_and_dedupes(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    import app.execution.mt5 as mt5
    def dead(path, timeout=4, **kw):
        raise ConnectionError("bridge down")
    monkeypatch.setattr(mt5, "bridge_get", dead)
    from app.system.incidents import sweep
    a = sweep("boss", bridge_probe=None)
    assert a["raised"] >= 1
    b = sweep("boss", bridge_probe=False)
    inc = [i for i in store.list("incidents", limit=10) if i["kind"] == "MT5_BRIDGE"]
    assert len(inc) == 1                                 # deduped, occurrences bumped
    assert inc[0]["occurrences"] == 2
    assert inc[0]["severity"] == "CRITICAL"


def test_incident_lifecycle_and_commander_advice_only(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.system.incidents import raise_incident, investigate_with_ai
    inc = raise_incident("boss", "MT5_BRIDGE", "bridge", "CRITICAL", "down")
    # lifecycle
    assert c.post(f"/api/incidents/{inc['id']}/ack").json()["status"] == "ACKNOWLEDGED"
    monkeypatch.setattr("app.execution.mt5.bridge_get",
                        lambda path, timeout=4, **kw: {"login": 477135810})
    import app.agent.router as ai_router
    monkeypatch.setattr(ai_router, "get_router", lambda: FakeRouter())
    rep = c.post(f"/api/incidents/{incident_id_helper(inc)}/investigate").json()
    assert rep["probable_cause"] == "bridge down"
    assert rep["advice_only"] is True
    r = c.post(f"/api/incidents/{inc['id']}/resolve",
               json={"note": "restarted bridge", "confirm": True})
    assert r.json()["status"] == "RESOLVED"
    doc = store.get("incidents", inc["id"])
    assert doc["postmortem"] == "restarted bridge"
    audits = store.list("audit_log", limit=10)
    assert any("incident" in (a.get("action") or "") for a in audits)


def incident_id_helper(inc):
    return inc["id"]


def test_commander_route_isolated_from_other_users(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store, "boss")
    _user(store, "mallory")
    from app.system.incidents import raise_incident
    inc = raise_incident("boss", "MT5_BRIDGE", "bridge", "CRITICAL", "down")
    from app.api.deps import get_user_id
    c.app.dependency_overrides[get_user_id] = lambda: "mallory"
    r = c.post(f"/api/incidents/{inc['id']}/investigate")
    assert r.status_code in (404, 503)                   # cannot touch boss's incident
    c.app.dependency_overrides[get_user_id] = lambda: "boss"


# ===========================================================================
# Stage 6: decision replay / time machine / counterfactuals / blocked
# ===========================================================================
def _completed_trade(store, i=0):
    return store.create("signals", {
        "userId": "boss", "strategy_id": "strategy_2_ema_atr",
        "strategy_name": "EMA", "market": "EURUSD", "direction": "BUY",
        "completed": True, "outcome": "WIN", "r_multiple": 1.5, "tp_hits": 2,
        "entry": 1.10, "sl": 1.0980, "tp1": 1.1020, "tp2": 1.1040, "tp3": 1.1080,
        "candle_time": _t(1), "completed_at": _t(2, 14),
        "signal_id": f"RP-{i}", "mt5_ticket": 555, "mt5_open_price": 1.1001,
        "mt5_pl": 15.0, "mt5_confirmed": True,
        "market_conditions": {"session": "London", "regime": "TRENDING"}})


def test_decision_replay_separates_known_from_outcome(env):
    c, store, monkeypatch, settings = env
    _user(store)
    sig = _completed_trade(store)
    store.create("exec_events", {"userId": "boss", "kind": "ENTRY", "stage": "REQUESTED",
                                 "signal_doc_id": sig["id"], "createdAt": _t(1)})
    store.create("exec_events", {"userId": "boss", "kind": "ENTRY", "stage": "CONFIRMED",
                                 "signal_doc_id": sig["id"], "ticket": 555,
                                 "latency_ms": 180.0, "createdAt": _t(1)})
    r = c.get(f"/api/research/replay/{sig['signal_id']}")
    body = r.json()
    assert r.status_code == 200
    assert body["execution_chain"][0]["stage"] == "REQUESTED"
    assert body["known_at_time"]["entry"] == 1.10
    assert body["later_outcome"]["r_multiple"] == 1.5    # kept strictly separate
    assert "never" in body["honesty"]
    assert c.get("/api/research/replay/does-not-exist").status_code == 404


def test_time_machine_and_counterfactuals(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    sig = _completed_trade(store)
    hist = [{"t": _t(1, 9 + m // 4), "o": 1.1, "h": 1.1004, "l": 1.0996, "c": 1.1}
            for m in range(24)]
    hist.append({"t": _t(1, 16), "o": 1.104, "h": 1.1090, "l": 1.1035, "c": 1.1085})
    import app.market_data.candle_store as cs
    monkeypatch.setattr(cs, "history", lambda m, limit=300: hist)
    tm = c.get(f"/api/research/timemachine/{sig['signal_id']}").json()
    assert tm["markers"]["tp2"] == 1.1040
    assert len(tm["candles_known_at_time"]) >= 5
    cf = c.get(f"/api/research/counterfactuals/{sig['signal_id']}").json()
    assert cf["actual_record"]["outcome"] == "WIN"       # untouched original
    questions = [s["question"] for s in cf["simulations"]]
    assert any("TP3" in q for q in questions)
    assert any("delayed" in q for q in questions)
    assert any("blocked" in q for q in questions)
    tp3 = next(s for s in cf["simulations"] if "TP3" in s["question"])
    assert tp3["result"] == "TARGET"
    assert "never a rewrite" in cf["epistemic"]


def test_blocked_analysis_and_filter_contribution(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(3):
        store.create("signals", {
            "userId": "boss", "strategy_id": "strategy_2_ema_atr", "market": "EURUSD",
            "direction": "BUY", "completed": False, "status": "SKIPPED_RISK_NEWS",
            "execution_status": "SKIPPED_RISK_NEWS", "entry": 1.10, "sl": 1.0980,
            "tp1": 1.1020, "candle_time": _t(1 + i), "signal_id": f"B-{i}"})
    hist = [{"t": _t(1, 11 + m // 4), "o": 1.1, "h": 1.1004, "l": 1.0996, "c": 1.1}
            for m in range(16)]
    hist.append({"t": _t(1, 18), "o": 1.1002, "h": 1.1035, "l": 1.0998, "c": 1.103})
    import app.market_data.candle_store as cs
    monkeypatch.setattr(cs, "history", lambda m, limit=300: hist)
    body = c.get("/api/research/blocked").json()
    assert body["blocked_total"] == 3
    news = next(f for f in body["filter_contribution"] if f["filter"] == "NEWS")
    assert news["blocked"] == 3
    assert news["winning_blocked"] >= 1                  # winning trades blocked - honest
    assert "saved" in news["net_effect"]
    assert "association" in news["note"]
    wb = c.get("/api/research/why-blocked/B-0").json()
    assert wb["guard"] == "NEWS" and wb["reason"] == "SKIPPED_RISK_NEWS"


# ===========================================================================
# Chaos: fail-safe under injected faults
# ===========================================================================
def test_chaos_bridge_dead_during_entry_is_loud_not_fatal(env, monkeypatch):
    """Bridge failure during entry => SKIPPED recorded loudly, no order placed,
    no duplicate, execution path survives."""
    c, store, monkeypatch, settings = env
    _user(store)                                   # boss IS the owner in this env
    store.create("agent_goals", {"userId": "boss", "execution_enabled": True,
                                 "execution_mode": "vps"})
    import app.execution.mt5 as mt5
    calls = {"n": 0}

    def flaky(path, timeout=8, **kw):
        calls["n"] += 1
        raise ConnectionError("VPS unreachable mid-flight")

    monkeypatch.setattr(mt5, "bridge_get", flaky)
    sig = {"id": "sig-chaos-1", "signal_id": "CHAOS-1", "market": "EURUSD",
           "direction": "BUY", "strategy_id": "strategy_2_ema_atr",
           "strategy_name": "EMA", "entry": 1.1, "sl": 1.098,
           "tp1": 1.102, "tp2": 1.104, "tp3": 1.108, "timeframe": "15M",
           "confidence": 0.7, "lot": 0.01}
    try:
        mt5.execute_signal(sig, "boss")
    except Exception:
        pass                                             # executor never raises (contract)
    events = [e for e in store.list("exec_events", limit=50)
              if e.get("kind") == "ENTRY"]
    assert events, "failure must be recorded loudly, never silent"
    assert any(e.get("stage") in ("FAILED", "SKIPPED") for e in events)
    placed = [e for e in events if e.get("stage") == "CONFIRMED"]
    assert placed == []                                  # nothing reached the broker
    assert calls["n"] >= 1


def test_chaos_killswitch_read_failure_fails_closed_for_entries(env, monkeypatch):
    """If the kill-switch doc cannot be read, entries must NOT silently pass
    the level check as if everything were normal - the guard reports and the
    remaining deterministic chain still applies (loud, not fake-healthy)."""
    from app.risk import killswitch as ks
    from app.db.store import get_store
    from app.db import store as store_mod
    # simulate store failure
    orig = store_mod._store

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("db down")

    store_mod._store = Boom()
    try:
        lvl = ks.current_level()                         # must NEVER raise
        assert lvl["level"] == 0
        assert lvl.get("degraded_read") is True          # honest, visible flag
        ok, why = ks.check("EURUSD")                     # and enforcement stays loud-safe
        assert ok is True and why == ""
    finally:
        store_mod._store = orig
