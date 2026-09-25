"""MT5 execution policy: caps, kill switch, lot math, deal sync (bridge mocked)."""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    store.create("signals", dict(SIG))
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 2)
    monkeypatch.setattr(settings, "owner_user_id", "u1")
    return store


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260914-001", "market": "EURUSD",
       "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}   # 20 pip SL


def test_calc_lot_matches_app_calculator():
    # 20 pip SL on EURUSD at 1% of $1000 = $10 risk -> $10 / (20 pips * $10/pip/lot) = 0.05
    assert X.calc_lot("EURUSD", 1.1000, 1.1020, 1000, 1.0) == 0.05
    # floor to 0.01 step, never round up
    assert X.calc_lot("EURUSD", 1.1000, 1.1020, 1100, 1.0) == 0.05
    assert X.calc_lot("EURUSD", 1.1000, 1.1020, 1200, 1.0) == 0.06
    # 50 pip SL, $10 risk -> 0.02
    assert X.calc_lot("EURUSD", 1.1000, 1.1050, 1000, 1.0) == 0.02


def test_execute_submits_and_records(monkeypatch):
    calls = {}
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    def fake_post(path, payload, timeout=15):
        calls.update(payload)
        return {"ok": True, "ticket": 777, "position_id": 888, "volume": 0.05, "price": 1.0999}
    monkeypatch.setattr(X, "bridge_post", fake_post)
    X.execute_signal(SIG, "u1")
    store = X.get_store()
    doc = store.get("signals", "sig1")
    assert calls["lots"] == 0.05 and calls["tp"] == 1.0900      # tp2 default
    assert doc["execution_status"] == "SUBMITTED" and doc["mt5_ticket"] == 777


def test_kill_switch_blocks(monkeypatch):
    X.set_execution_enabled("u1", False)
    sent = []
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15: sent.append(payload))
    X.execute_signal(SIG, "u1")
    assert sent == []
    # C-2 (approved 2026-09-21): the branch now stamps a terminal status
    assert X.get_store().get("signals", "sig1").get("execution_status") == "SKIPPED_KILL_SWITCH"


def test_bridge_offline_is_honest(monkeypatch):
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: None)
    X.execute_signal(SIG, "u1")
    doc = X.get_store().get("signals", "sig1")
    assert doc["execution_status"] == "SKIPPED_BRIDGE_OFFLINE"


def test_daily_cap(monkeypatch):
    from datetime import datetime, timezone
    store = X.get_store()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for i in range(2):   # cap = 2
        store.create("signals", {"userId": "u1", "signal_id": f"S-{i}", "day": today,
                                 "mt5_ticket": 100 + i})
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    X.execute_signal(SIG, "u1")
    # C-2 (approved 2026-09-21): the branch now stamps a terminal status
    assert store.get("signals", "sig1").get("execution_status") == "SKIPPED_EXEC_DAILY_CAP"


def test_sync_confirms_closed_trade(monkeypatch):
    store = X.get_store()   # uses the fixture signal doc (sig1, userId u1)
    deals = {"deals": [
        {"position_id": 5, "entry": 0, "magic": X.MAGIC, "comment": "SIG-20260914-001",
         "volume": 0.05, "price": 1.0999, "profit": 0, "commission": -0.2, "swap": 0},
        {"position_id": 5, "entry": 1, "magic": X.MAGIC, "comment": "",
         "volume": 0.05, "price": 1.0901, "profit": 9.8, "commission": -0.2, "swap": 0},
    ]}
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=20: deals)
    n = X.sync_deals("u1")
    assert n == 1
    doc = store.get("signals", "sig1")
    assert doc["mt5_confirmed"] and doc["outcome"] == "WIN"
    assert doc["mt5_pl"] == 9.4                                  # 9.8 - 0.2 - 0.2
    notes = store.list("notifications", filters={"userId": "u1"})
    assert any("MT5" in (x.get("title") or "") for x in notes)
    # idempotent: second sync does nothing
    assert X.sync_deals("u1") == 0


def test_sync_window_overlaps_lookback(monkeypatch):
    """Regression (production incident 2026-09-24): a 5-min incremental deal
    window contains the EXIT deal but not the ENTRY deal (opened hours
    earlier), so apply_deals could not pair them and broker-closed trades
    stayed 'ACTIVE' in the app forever. The window must overlap by the
    lookback (default 48h)."""
    store = X.get_store()
    X._goals_update("u1", {"mt5_deal_sync_ts": int(X.time.time()) - 300,
                           "mt5_backfill_done": True})
    seen = {}
    deals = {"deals": [
        {"position_id": 7, "entry": 0, "magic": X.MAGIC, "comment": "SIG-20260914-001",
         "volume": 0.05, "price": 1.0999, "profit": 0, "commission": 0, "swap": 0},
        {"position_id": 7, "entry": 1, "magic": X.MAGIC, "comment": "",
         "volume": 0.05, "price": 1.0901, "profit": 5.0, "commission": 0, "swap": 0},
    ]}
    def fake_bridge(path, timeout=20):
        seen["path"] = path
        return deals
    monkeypatch.setattr(X, "bridge_get", fake_bridge)
    n = X.sync_deals("u1")
    assert n == 1
    since = int(seen["path"].split("since=")[1])
    now = int(X.time.time())
    assert since <= now - 47 * 3600          # cursor 5 min ago -> window ~48h
    assert store.get("signals", "sig1")["mt5_confirmed"]


# ---------------- manual (MT5 PC connector) mode ----------------
def _manual_mode(store):
    import time as _t
    doc = store.list("agent_goals", filters={"userId": "u1"}, limit=1)[0]
    store.update("agent_goals", doc["id"], {"execution_mode": "manual",
                                            "mt5_account": {"balance": 1000},
                                            "mt5_connector_seen": _t.time()})


def test_manual_mode_queues_command(monkeypatch):
    store = X.get_store()
    _manual_mode(store)
    sent = []
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 999})
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15: sent.append(payload))
    X.execute_signal(SIG, "u1")
    assert sent == []                                   # never touches the bridge
    cmds = store.list("exec_commands", filters={"userId": "u1", "status": "PENDING"})
    assert len(cmds) == 1 and cmds[0]["payload"]["symbol"] == "EURUSD"
    assert cmds[0]["payload"]["lots"] == 0.05           # from mt5_account balance 1000
    sig = store.get("signals", "sig1")
    assert sig["execution_status"] == "QUEUED_PC" and sig["mt5_command_id"] == cmds[0]["id"]


def test_manual_never_connected_skips(monkeypatch):
    store = X.get_store()
    _manual_mode(store)
    doc = store.list("agent_goals", filters={"userId": "u1"}, limit=1)[0]
    store.update("agent_goals", doc["id"], {"mt5_account": None})
    monkeypatch.setattr(X, "connector_online", lambda uid: False)
    monkeypatch.setattr(X, "_connector_seen", lambda uid: None)
    X.execute_signal(SIG, "u1")
    assert store.get("signals", "sig1")["execution_status"] == "SKIPPED_PC_NEVER_CONNECTED"


def test_pairing_binds_connector(monkeypatch):
    store = X.get_store()
    code = X.ensure_pairing_code("u1")
    assert code.startswith("FXM-")
    assert X.user_for_pairing(code) == "u1"
    assert X.user_for_pairing("FXM-WRONG-CODE") is None


def test_pull_and_ack_confirm_fill():
    store = X.get_store()
    _manual_mode(store)
    X.execute_signal(SIG, "u1")
    cmds = X.pull_commands("u1")
    assert len(cmds) == 1 and cmds[0]["symbol"] == "EURUSD"
    # pull marks SENT -> second pull returns nothing
    assert X.pull_commands("u1") == []
    X.ack_command("u1", cmds[0]["command_id"], True,
                  {"ok": True, "ticket": 555, "position_id": 556, "volume": 0.05, "price": 1.0998})
    sig = store.get("signals", "sig1")
    assert sig["execution_status"] == "SUBMITTED" and sig["mt5_ticket"] == 555
    acts = store.list("agent_activity", limit=10)
    assert any("MT5 PC EXECUTED" in (a.get("message") or "") for a in acts)


def test_stale_commands_expire_offline():
    store = X.get_store()
    _manual_mode(store)
    X.execute_signal(SIG, "u1")
    from datetime import datetime, timedelta, timezone
    cmd = store.list("exec_commands", filters={"userId": "u1", "status": "PENDING"})[0]
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.update("exec_commands", cmd["id"], {"createdAt": old})
    assert X.expire_stale_commands() == 1
    assert store.get("signals", "sig1")["execution_status"] == "EXPIRED_PC_OFFLINE"


def test_connector_deals_push_confirms():
    store = X.get_store()
    deals = {"deals": [
        {"position_id": 9, "entry": 0, "magic": X.MAGIC, "comment": "SIG-20260914-001",
         "volume": 0.05, "price": 1.0999, "profit": 0, "commission": -0.2, "swap": 0},
        {"position_id": 9, "entry": 1, "magic": X.MAGIC, "comment": "",
         "volume": 0.05, "price": 1.0901, "profit": 9.8, "commission": -0.2, "swap": 0},
    ]}
    assert X.connector_push_deals("u1", deals["deals"]) == 1
    assert store.get("signals", "sig1")["mt5_confirmed"]


def test_non_owner_signals_stay_advisory(monkeypatch):
    """Standing directive: the bridge drives ONE account (the owner's). A
    signal scanned under any other user must NEVER race the owner for a
    broker position (production incident: SIG-20260924-001 executed under
    the demo user). Non-owner -> advisory, bridge never called."""
    sent = []
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15: sent.append(payload))
    env_store = X.get_store()
    X.execute_signal(SIG, "someone_else")
    doc = env_store.get("signals", "sig1")
    assert doc["execution_status"] == "SKIPPED_NOT_ENGINE_OWNER"
    assert sent == []          # nothing reached the broker


def test_skip_paths_notify_telegram(monkeypatch):
    """The creation TG message announces a trade - any skip must answer with
    an explicit NOT EXECUTED notice (user report: TG showed the signal, the
    app showed nothing, no trade existed)."""
    store = X.get_store()
    notices = []
    monkeypatch.setattr(X, "notify",
                        lambda uid, t, title, body, **kw: notices.append((t, title)))
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    monkeypatch.setattr(X, "_executed_today", lambda uid: X.settings.execution_max_trades_per_day)
    X.execute_signal(SIG, "u1")
    assert store.get("signals", "sig1")["execution_status"] == "SKIPPED_EXEC_DAILY_CAP"
    assert any(t == "EXECUTION_SKIPPED" for t, _ in notices)

    # stop-too-tight path
    notices.clear()
    tight = dict(SIG, id="sig_tight", sl=1.0999)   # 1 pip SL < broker minimum
    store.create("signals", dict(tight))
    monkeypatch.setattr(X, "_executed_today", lambda uid: 0)
    X.execute_signal(tight, "u1")
    assert store.get("signals", "sig_tight")["execution_status"] == "SKIPPED_STOP_TOO_TIGHT"
    assert any(t == "EXECUTION_SKIPPED" for t, _ in notices)


def test_apply_deals_never_rereads_confirmed_positions(monkeypatch):
    """Quota fix: the 48h deal window re-offers old positions every 5 min;
    once confirmed they must be skipped with ZERO store reads."""
    X._CONFIRMED_POSITIONS.clear()
    calls = {"list": 0}
    real_list = X.get_store().list
    def counting_list(coll, *a, **kw):
        if coll == "signals":
            calls["list"] += 1
        return real_list(coll, *a, **kw)
    monkeypatch.setattr(X.get_store(), "list", counting_list)
    deals = {"deals": [
        {"position_id": 99, "entry": 0, "magic": X.MAGIC, "comment": "SIG-20260914-001",
         "volume": 0.05, "price": 1.0999, "profit": 0, "commission": 0, "swap": 0},
        {"position_id": 99, "entry": 1, "magic": X.MAGIC, "comment": "",
         "volume": 0.05, "price": 1.0901, "profit": 3.0, "commission": 0, "swap": 0},
    ]}
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=20: deals)
    assert X.sync_deals("u1") == 1
    first = calls["list"]
    assert X.sync_deals("u1") == 0
    assert calls["list"] == first        # second sync: no new store reads
    X._CONFIRMED_POSITIONS.clear()
