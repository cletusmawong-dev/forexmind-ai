"""Stage 3: execution lifecycle ledger + TP-leak audit + health endpoint
(Master Upgrade Phase 4).

Rules under test:
 - every entry attempt leaves REQUESTED -> CONFIRMED (ticket) / FAILED
 - every primitive (modify_sl / partial / close) leaves the same trail
 - skips are recorded with a reason (kill switch etc.) - never silent
 - manual mode: QUEUED on command create, EXPIRED on TTL, FAILED on bad ack
 - TP-leak audit: TP2 lock violation = leak; orphan position + untracked
   close = warn; clean account = no findings; throttled + deduped, leaks notify
 - /api/health reports honest components (store kind, execution mode,
   brain flag, scheduler heartbeat) and keeps its legacy keys
"""
import os
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.execution.mt5 import MAGIC


@pytest.fixture()
def world(monkeypatch, tmp_path):
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
            yield c, store, monkeypatch
    finally:
        State.store = _prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)


def _vps_goals(store, enabled=True, mode="vps"):
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": enabled,
                                 "execution_mode": mode})
    store.create("users", {"id": "boss", "userId": "boss", "email": "b@x.io",
                           "role": "admin", "status": "active"})


SIG = {"id": "sg1", "userId": "boss", "signal_id": "SIG-L1", "market": "EURUSD",
       "direction": "BUY", "entry": 1.10, "sl": 1.095, "tp1": 1.105,
       "tp2": 1.11, "tp3": 1.115}


def _events(store, kind=None, stage=None):
    out = store.list("exec_events", filters={"userId": "boss"}, limit=0)
    if kind:
        out = [e for e in out if e["kind"] == kind]
    if stage:
        out = [e for e in out if e["stage"] == stage]
    return out


# ===========================================================================
# LIFECYCLE LEDGER
# ===========================================================================
def test_entry_lifecycle_confirmed(world):
    c, store, _ = world
    _vps_goals(store)
    store.create("signals", dict(SIG))
    from app.execution import mt5 as X
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: {"balance": 1000} if p == "/account" else {}
    X.bridge_post = lambda p, payload, timeout=15: \
        {"ok": True, "ticket": 777, "volume": 0.1, "price": 1.1}
    try:
        X.execute_signal(store.get("signals", "sg1"), "boss")
        req = [e for e in _events(store, "ENTRY") if e["stage"] == "REQUESTED"]
        con = [e for e in _events(store, "ENTRY") if e["stage"] == "CONFIRMED"]
        assert len(req) == 1 and req[0]["signal_id"] == "SIG-L1"
        assert len(con) == 1 and con[0]["ticket"] == 777
        assert store.get("signals", "sg1")["execution_status"] == "SUBMITTED"
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post


def test_entry_lifecycle_failed(world):
    c, store, _ = world
    _vps_goals(store)
    store.create("signals", dict(SIG))
    from app.execution import mt5 as X
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: {"balance": 1000} if p == "/account" else {}
    X.bridge_post = lambda p, payload, timeout=15: {"ok": False, "error": "requote"}
    try:
        X.execute_signal(store.get("signals", "sg1"), "boss")
        failed = [e for e in _events(store, "ENTRY") if e["stage"] == "FAILED"]
        assert len(failed) == 1 and "requote" in failed[0]["detail"]
        assert failed[0]["latency_ms"] is not None
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post


def test_skip_event_kill_switch(world):
    c, store, _ = world
    _vps_goals(store, enabled=False)
    store.create("signals", dict(SIG))
    from app.execution import mt5 as X
    X.execute_signal(store.get("signals", "sg1"), "boss")
    sk = [e for e in _events(store, "ENTRY") if e["stage"] == "SKIPPED"]
    assert len(sk) == 1 and "kill switch" in sk[0]["detail"]


def test_modify_and_close_lifecycle(world):
    c, store, _ = world
    _vps_goals(store)
    from app.execution import mt5 as X
    orig_post = X.bridge_post
    X.bridge_get = lambda p, timeout=8: {}
    X.bridge_post = lambda p, payload, timeout=15: \
        ({"ok": False, "error": "off quotes"} if p == "/close"
         else {"ok": True})
    try:
        r1 = X.modify_sl("boss", 55, 1.1005, reason="TP2 lock")
        assert r1.get("ok")
        r2 = X.close_position("boss", 55, reason="AI EXIT")
        assert not r2.get("ok")
        mr = sorted(_events(store, "MODIFY_SL"), key=lambda e: e["createdAt"])
        assert [e["stage"] for e in mr] == ["REQUESTED", "CONFIRMED"]
        cr = sorted(_events(store, "CLOSE_FULL"), key=lambda e: e["createdAt"])
        assert [e["stage"] for e in cr] == ["REQUESTED", "FAILED"]
        assert "off quotes" in cr[-1]["detail"]
    finally:
        X.bridge_post = orig_post


def test_manual_mode_queued_and_expired(world):
    c, store, monkeypatch = world
    _vps_goals(store, mode="manual")
    from app.execution import mt5 as X
    X.modify_sl("boss", 66, 1.0999, reason="manual")
    assert [e["stage"] for e in _events(store, "MODIFY_SL")] == ["QUEUED"]
    # a stale PENDING command expires with a ledger event
    store.create("exec_commands", {
        "userId": "boss", "type": "close_full", "status": "PENDING",
        "signal_id": "SIG-L9",
        "createdAt": datetime.now(timezone.utc).isoformat()})
    doc = [x for x in store.list("exec_commands", limit=10)
           if x["type"] == "close_full"][0]
    store.update("exec_commands", doc["id"], {
        "createdAt": datetime.now(timezone.utc).isoformat()})
    # age it beyond the TTL by direct local edit
    from datetime import timedelta
    aged = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    store._data["exec_commands"][doc["id"]]["createdAt"] = aged
    n = X.expire_stale_commands()
    assert n >= 1
    assert "EXPIRED" in [e["stage"] for e in _events(store, "ENTRY")]


# ===========================================================================
# TP-LEAK AUDIT
# ===========================================================================
def _audit_goals(store):
    store.create("agent_goals", {"userId": "boss", "execution_enabled": True,
                                 "execution_mode": "vps"})
    store.create("users", {"id": "boss", "userId": "boss", "email": "b@x.io",
                           "role": "admin", "status": "active"})


def test_tp2_lock_missing_is_a_leak(world):
    c, store, _ = world
    _audit_goals(store)
    store.create("signals", dict(SIG, signal_id="SIG-L1", mt5_ticket=5,
                                 completed=False))
    pos = {"ticket": 5, "magic": MAGIC, "type": "BUY", "comment": "SIG-L1",
           "price_current": 1.1105, "sl": 1.095, "tp": 1.11,
           "app_market": "EURUSD", "symbol": "EURUSD", "profit": 90.0}
    from app.execution.tp_audit import scan_user
    f = scan_user("boss", positions=[pos])
    leak = [x for x in f if x["type"] == "TP2_LOCK_MISSING"]
    assert len(leak) == 1 and leak[0]["severity"] == "leak"
    assert leak[0]["ticket"] == 5


def test_lock_held_and_matching_tp_is_clean(world):
    c, store, _ = world
    _audit_goals(store)
    store.create("signals", dict(SIG, signal_id="SIG-L1", mt5_ticket=5,
                                 completed=False,
                                 tp_selection={"level": 2}))
    pos = {"ticket": 5, "magic": MAGIC, "type": "BUY", "comment": "SIG-L1",
           "price_current": 1.1110, "sl": 1.105, "tp": 1.11,
           "app_market": "EURUSD", "symbol": "EURUSD", "profit": 95.0}
    from app.execution.tp_audit import scan_user
    assert scan_user("boss", positions=[pos]) == []


def test_orphan_position_and_closed_untracked(world):
    c, store, _ = world
    _audit_goals(store)
    # orphan: engine-magic position with unknown comment
    orphan = {"ticket": 8, "magic": MAGIC, "type": "SELL",
              "comment": "unknown-sig", "price_current": 1.09, "sl": 1.095,
              "symbol": "EURUSD", "profit": 5.0}
    # closed untracked: journal entry never finalized, position long gone
    store.create("signals", dict(SIG, id="sg9", signal_id="SIG-L9",
                                 mt5_ticket=9, completed=False,
                                 mt5_executed_at="2026-09-20T08:00:00+00:00"))
    from app.execution.tp_audit import scan_user
    f = scan_user("boss", positions=[orphan])
    types = {x["type"] for x in f}
    assert "ORPHAN_POSITION" in types and "CLOSED_UNTRACKED" in types


def test_recently_executed_signal_is_not_flagged(world):
    """A filled signal whose position just closed is tracker's job - the
    audit only warns after the grace window."""
    c, store, _ = world
    _audit_goals(store)
    store.create("signals", dict(SIG, signal_id="SIG-L1", mt5_ticket=5,
                                 completed=False,
                                 mt5_executed_at=datetime.now(
                                     timezone.utc).isoformat()))
    from app.execution.tp_audit import scan_user
    assert scan_user("boss", positions=[]) == []


def test_run_throttled_dedupes_and_notifies_on_leak(world, monkeypatch):
    c, store, monkeypatch = world
    _audit_goals(store)
    store.create("signals", dict(SIG, signal_id="SIG-L1", mt5_ticket=5,
                                 completed=False))
    pos = {"ticket": 5, "magic": MAGIC, "type": "BUY", "comment": "SIG-L1",
           "price_current": 1.1105, "sl": 1.095, "symbol": "EURUSD",
           "profit": 90.0}
    from app.execution import mt5 as X, tp_audit as A
    orig_get = X.bridge_get
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {}
    sent = []
    import app.notifications.service as NS
    monkeypatch.setattr(NS, "notify",
                        lambda uid, t, title, body, **kw: sent.append(t))
    try:
        fresh1 = A.run_throttled("boss", min_interval_s=0)
        assert [f["type"] for f in fresh1] == ["TP2_LOCK_MISSING"]
        assert "TP_AUDIT" in [e["kind"] for e in _events(store)]
        assert "TP_AUDIT_LEAK" in sent                     # owner notified
        A._last_scan["boss"] = 0.0                         # unlock throttle
        fresh2 = A.run_throttled("boss", min_interval_s=0)
        assert fresh2 == []                                # deduped
        assert sent.count("TP_AUDIT_LEAK") == 1
    finally:
        X.bridge_get = orig_get
        A._last_signatures.clear()


# ===========================================================================
# API SURFACES + HEALTH
# ===========================================================================
def test_executions_api(world):
    c, store, _ = world
    _vps_goals(store)
    from app.execution import ledger
    ledger.emit("boss", "ENTRY", "CONFIRMED", market="EURUSD",
                signal_id="SIG-L1", ticket=42, detail="x")
    r = c.get("/api/aimanager/executions")
    assert r.status_code == 200
    ev = r.json()["events"]
    assert len(ev) == 1 and ev[0]["kind"] == "ENTRY" and ev[0]["ticket"] == 42
    r2 = c.get("/api/aimanager/executions/tp-audit")
    assert r2.status_code == 200 and "findings" in r2.json()


def test_health_reports_components(world):
    c, store, monkeypatch = world
    _vps_goals(store)
    from app.config import settings
    monkeypatch.setattr(settings, "bridge_url", "")   # skip network probe
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["provider"]    # legacy keys intact
    comps = body["components"]
    assert "LocalStore" in comps["store"]["kind"]
    assert comps["execution"]["bridge_configured"] is False
    assert comps["brain_v2"] is True
    assert "last_tick_age_s" in comps["scheduler"]
