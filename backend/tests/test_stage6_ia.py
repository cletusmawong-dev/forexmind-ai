"""Stage 6 / Phase 18: UI information-architecture backend surfaces +
full-upgrade integration weave.

Covers:
 - GET /api/positions/live: broker-truth positions joined with signals and
   the latest AI decision per ticket; honest empty in non-vps mode
 - /aimanager/status recent_decisions now carry brain metadata
 - integration weave: entry -> ledger CONFIRMED -> audit log; suspended user
   refused by executor with ledger event; command center builds over a store
   with real activity
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.execution.mt5 import MAGIC


@pytest.fixture()
def env(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
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
    monkeypatch.setattr(settings, "execution_tp_level", "2")
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


def _boss(store, **kw):
    doc = {"id": "boss", "userId": "boss", "email": "boss@x.io", "role": "admin",
           "status": "active", "trading_permission": "enabled"}
    doc.update(kw)
    return store.create("users", doc)


SIG = {"id": "sg1", "userId": "boss", "signal_id": "SIG-P1", "market": "EURUSD",
       "direction": "BUY", "entry": 1.10, "sl": 1.095, "tp1": 1.105,
       "tp2": 1.11, "tp3": 1.115, "strategy_name": "S1"}


def test_positions_live_joins_broker_signal_and_brain(env):
    c, store, _, _ = env
    _boss(store)
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "execution_enabled": True, "execution_mode": "vps"})
    store.create("signals", dict(SIG))
    store.create("ai_decisions", {
        "userId": "boss", "position_ticket": 321, "symbol": "EURUSD",
        "trigger": "tp1_approach", "model": "fake", "layer": "primary",
        "decision": {"action": "PROTECT", "confidence": 0.8},
        "gate_verdict": "OK_PROTECT",
        "brain": {"answer": "ACTION", "confidence_pct": 74,
                  "evidence_quality": 0.8, "freshness": "OK",
                  "challenge": {"ran": True, "survives": True}},
        "createdAt": "2026-09-27T10:00:00+00:00"})
    from app.execution import mt5 as X
    orig_get = X.bridge_get
    X.bridge_get = lambda p, timeout=8: (
        {"positions": [{"ticket": 321, "magic": MAGIC, "type": "BUY",
                        "comment": "SIG-P1", "symbol": "EURUSD",
                        "price_open": 1.10, "price_current": 1.103,
                        "volume": 0.1, "sl": 1.095, "profit": 30.0,
                        "time": time.time() - 7200}]} if p == "/positions" else {})
    try:
        r = c.get("/api/positions/live")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1 and body["mode"] == "vps"
        p = body["positions"][0]
        assert p["signal_id"] == "SIG-P1" and p["strategy"] == "S1"
        assert p["r_multiple_now"] == 0.6           # 30 pips / 50 pip risk
        assert p["last_ai"]["answer"] == "ACTION"
        assert p["last_ai"]["confidence_pct"] == 74
        assert p["last_ai"]["gate"] == "OK_PROTECT"
    finally:
        X.bridge_get = orig_get


def test_positions_live_honest_when_not_vps(env):
    c, store, _, _ = env
    _boss(store)
    store.create("agent_goals", {"userId": "boss", "execution_mode": "manual"})
    r = c.get("/api/positions/live")
    assert r.status_code == 200
    body = r.json()
    assert body["positions"] == [] and body["mode"] == "manual"


def test_manager_status_exposes_brain_metadata(env):
    c, store, _, _ = env
    _boss(store)
    store.create("ai_decisions", {
        "userId": "boss", "position_ticket": 5, "symbol": "XAUUSD",
        "trigger": "scheduled_review", "model": "m", "layer": "primary",
        "decision": {"action": "HOLD"}, "gate_verdict": "OK_HOLD",
        "brain": {"answer": "NO_ACTION", "confidence_pct": 40,
                  "evidence_quality": 0.5, "freshness": "OK",
                  "challenge": {"ran": False}},
        "createdAt": "2026-09-27T09:00:00+00:00"})
    r = c.get("/api/aimanager/status")
    assert r.status_code == 200
    dec = r.json()["recent_decisions"][0]
    assert dec["brain"]["answer"] == "NO_ACTION"
    assert dec["brain"]["confidence_pct"] == 40
    assert dec["brain"]["challenge"]["ran"] is False


# ===========================================================================
# PHASE-18 INTEGRATION WEAVE (cross-stage, one store, fakes for broker)
# ===========================================================================
def test_weave_entry_ledger_audit_and_suspension(env):
    c, store, monkeypatch, settings = env
    _boss(store)
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True, "execution_mode": "vps"})
    store.create("signals", dict(SIG))
    from app.execution import mt5 as X
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: {"balance": 1000} if p == "/account" else {}
    X.bridge_post = lambda p, payload, timeout=15: \
        {"ok": True, "ticket": 900, "volume": 0.1, "price": 1.1}
    try:
        # 1) authorized entry -> broker -> CONFIRMED in the ledger
        X.execute_signal(store.get("signals", "sg1"), "boss")
        evs = store.list("exec_events", filters={"userId": "boss"}, limit=10)
        stages = [e["stage"] for e in sorted(evs, key=lambda x: x["createdAt"])]
        assert stages == ["REQUESTED", "CONFIRMED"]
        confirmed = [e for e in evs if e["stage"] == "CONFIRMED"][0]
        assert confirmed["ticket"] == 900
        assert store.get("signals", "sg1")["execution_status"] == "SUBMITTED"

        # 2) suspended user -> executor refuses, ledger records, never silent
        store.create("users", {"id": "u9", "userId": "u9", "email": "u9@x.io",
                               "role": "user", "status": "suspended",
                               "trading_permission": "locked"})
        store.create("signals", dict(SIG, id="sg2", userId="u9",
                                     signal_id="SIG-P2"))
        sent = []
        import app.notifications.service as NS
        monkeypatch.setattr(NS, "notify",
                            lambda uid, t, ti, b, **kw: sent.append(t))
        monkeypatch.setattr("app.execution.mt5.notify",
                            lambda uid, t, ti, b, **kw: sent.append(t))
        X.execute_signal(store.get("signals", "sg2"), "u9")
        doc = store.get("signals", "sg2")
        assert doc["execution_status"] == "SKIPPED_NOT_AUTHORIZED"
        assert "EXECUTION_SKIPPED" in sent
        ev2 = [e for e in store.list("exec_events", filters={"userId": "u9"},
                                     limit=5)]
        assert ev2 and ev2[0]["stage"] == "SKIPPED"

        # 3) command center builds over this real activity
        r = c.get("/api/admin/command-center")
        assert r.status_code == 200
        cc = r.json()
        assert cc["users"]["total"] == 2
        assert cc["users"]["suspended"] == 1
        assert len(cc["executions"]["recent"]) == 2   # owner's own trail
        assert all(e["userId"] == "boss"
                   for e in cc["executions"]["recent"])
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post


def test_goals_overlay_shows_bridge_truth_for_vps(env, monkeypatch):
    """User directive 2026-09-27: the admin account IS the VPS account.
    get_goals must overlay live broker balance/equity in vps mode - a stale
    stored number may never be shown as the account balance."""
    c, store, monkeypatch, settings = env
    from app.execution.mt5 import MAGIC
    store.create("users", {"id": "boss", "userId": "boss", "email": "b@x.io",
                           "role": "admin", "status": "active"})
    store.create("agent_goals", {"userId": "boss", "account_balance": 5000.0,
                                 "execution_mode": "vps", "execution_enabled": True})
    from app.execution import mt5 as X
    orig_get = X.bridge_get
    X.bridge_get = lambda p, timeout=4: (
        {"balance": 4864.57, "equity": 4902.10, "login": 477135810,
         "server": "Exness-MT5Trial9"} if p == "/account" else {})
    try:
        from app.agent.core import get_goals
        g = get_goals("boss")
        assert g["account_balance"] == 4864.57        # broker truth, not 5000
        assert g["balance_source"] == "mt5_bridge"
        assert g["account_equity_live"] == 4902.10
        assert g["mt5_account"]["login"] == 477135810
        assert g["mt5_account"]["broker_truth"] is True
        # non-vps account keeps its stored balance, honestly labeled
        store.create("agent_goals", {"userId": "u2", "account_balance": 123.0,
                                     "execution_mode": "off"})
        g2 = get_goals("u2")
        assert g2["account_balance"] == 123.0 and g2["balance_source"] == "stored"
    finally:
        X.bridge_get = orig_get


def test_execution_status_returns_mode_and_tp_level(env, monkeypatch):
    """Regression: the Settings execution card showed 'off' with dead taps
    because /execution/status 500'd (undefined tp_level). It must return
    the USER's real mode (vps for the master account) + configured TP level."""
    c, store, monkeypatch, settings = env
    from app.execution import mt5 as X
    store.create("users", {"id": "boss", "userId": "boss", "email": "b@x.io",
                           "role": "admin", "status": "active"})
    store.create("agent_goals", {"userId": "boss", "account_balance": 4864.57,
                                 "execution_mode": "vps", "execution_enabled": True})
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 4864.57, "equity": 4864.57})
    monkeypatch.setattr(settings, "execution_tp_level", "AUTO")
    r = c.get("/api/execution/status")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "vps" and d["enabled"] is True
    assert d["tp_level"] == "AUTO"
    assert d["bridge"]["online"] is True
    # the mode switch endpoint accepts vps for this account (what the tap calls)
    r2 = c.post("/api/execution/mode", json={"mode": "vps"})
    assert r2.status_code == 200 and r2.json()["mode"] == "vps"
