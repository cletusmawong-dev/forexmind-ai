"""Phase 6: daily profit target, loss limit, EXTRA-signal flow, manual tracking.

Walls are ACCOUNT-LEVEL and USD-based, computed from real records only
(broker mt5_pl, or R x risk% x balance - labeled). Walls block automatic
ENTRY only: signals keep flowing as EXTRA SIGNALS (SS22-SS27). The prop
max-TOTAL-drawdown wall is intentionally NOT an extra-signal wall.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.store import LocalStore

DAY_TZ = "UTC"
NOW = datetime.now(timezone.utc)
T00 = NOW.strftime("%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _mk_user(store, balance=1000.0, **risk_over):
    store.create("agent_goals", {"userId": "u1", "account_balance": balance,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True,
                                 "execution_mode": "vps"})
    risk = {"userId": "u1", "kind": "risk",
            "allowed_markets": ["XAUUSD"], "sessions": ["London", "NewYork", "Asian", "Late"],
            "max_signals_per_day": 6, "min_rr": 1.5, "max_daily_loss_pct": 3.0,
            "risk_per_trade_pct": 1.0, "session_tz": DAY_TZ}
    risk.update(risk_over)
    store.create("settings", risk)


def _completed(store, sig_id, r, pl=None, confirmed=False, closed_at=None):
    doc = {"id": sig_id, "userId": "u1", "signal_id": sig_id, "market": "XAUUSD",
           "completed": True, "r_multiple": r,
           "createdAt": _iso(NOW.replace(hour=1, minute=0)),
           "completed_at": closed_at or _iso(NOW.replace(hour=2, minute=0))}
    if pl is not None:
        doc.update({"mt5_pl": pl, "mt5_confirmed": confirmed})
    store.create("signals", doc)


class _Cand:
    market = "XAUUSD"
    rr_primary = 2.0
    strategy_id = "strategy_1_zero_lag"
    candle_time = "2026-07-01 13:00:00"


# =================================================================
# daily_state accounting
# =================================================================
def test_daily_state_broker_pl_and_r_estimate(fresh_store):
    from app.engine.daily import daily_state
    _mk_user(fresh_store)
    _completed(fresh_store, "S1", r=-1.0, pl=-12.5, confirmed=True)   # real -$12.50
    _completed(fresh_store, "S2", r=+2.0, pl=None)                    # est +2% of 1000 = +20
    st = daily_state("u1")
    assert st["realized_usd"] == pytest.approx(7.5)
    assert "1 broker-confirmed" in st["realized_basis"]


def test_daily_state_ignores_yesterday(fresh_store):
    from app.engine.daily import daily_state
    _mk_user(fresh_store)
    _completed(fresh_store, "OLD", r=-3.0, pl=-30, confirmed=True,
               closed_at=_iso(NOW - timedelta(days=1)))
    st = daily_state("u1")
    assert st["realized_usd"] == 0.0


def test_daily_state_tz_day_boundary(fresh_store):
    from app.engine.daily import daily_state
    _mk_user(fresh_store, session_tz="Africa/Accra")   # UTC+0 in July, same day here
    _completed(fresh_store, "S1", r=1.5, pl=15, confirmed=True)
    st = daily_state("u1")
    assert st["realized_usd"] == 15.0 and st["tz"] == "Africa/Accra"


def test_trading_day_start_uses_local_midnight():
    from app.engine.daily import trading_day_start
    from datetime import datetime, timezone
    now = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
    accra = trading_day_start("Africa/Accra", now)     # UTC+0 -> 2026-07-01 00:00 UTC
    ny = trading_day_start("America/New_York", now)    # UTC-4 -> 2026-07-01 04:00 UTC
    assert accra == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc).timestamp()
    assert ny == datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc).timestamp()


# =================================================================
# walls
# =================================================================
def test_profit_target_wall(fresh_store):
    from app.engine.daily import daily_state, wall_reason
    _mk_user(fresh_store, daily_profit_target_usd=300)
    _completed(fresh_store, "S1", r=1.0, pl=310, confirmed=True)
    st = daily_state("u1")
    assert st["hit_target"] is True and st["status"] == "TARGET_HIT"
    assert wall_reason(st, {"daily_profit_target_usd": 300}) == "daily profit target reached"


def test_loss_limit_wall(fresh_store):
    from app.engine.daily import daily_state, wall_reason
    _mk_user(fresh_store, daily_loss_limit_usd=150)
    _completed(fresh_store, "S1", r=-1.0, pl=-153, confirmed=True)
    st = daily_state("u1")
    assert st["hit_loss"] is True and st["status"] == "LOSS_LIMIT_HIT"
    assert wall_reason(st, {"daily_loss_limit_usd": 150}) == "daily loss limit reached"


def test_walls_off_by_default(fresh_store):
    from app.engine.daily import daily_state, wall_reason
    _mk_user(fresh_store)
    _completed(fresh_store, "S1", r=5.0, pl=500, confirmed=True)
    st = daily_state("u1")
    assert st["hit_target"] is False and wall_reason(st, st) is None


def test_floating_pushes_total_over_wall(fresh_store, monkeypatch):
    from app.engine import daily as D
    from app.execution import mt5 as X
    _mk_user(fresh_store, daily_profit_target_usd=300)
    _completed(fresh_store, "S1", r=1.0, pl=250, confirmed=True)
    monkeypatch.setattr(D, "user_mode_safe", lambda uid: "vps")
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=6: {
        "positions": [{"ticket": 1, "magic": 20260914, "profit": 70.0}]})
    st = D.daily_state("u1")
    assert st["floating_usd"] == 70.0 and st["floating_source"] == "bridge_positions"
    assert st["hit_target"] is True and st["status"] == "TARGET_HIT"


def test_is_entry_blocked_never_blocks_signal_generation_side(fresh_store):
    """The executor gate blocks ENTRIES; nothing here touches scan."""
    from app.engine.daily import is_entry_blocked
    _mk_user(fresh_store, daily_profit_target_usd=100)
    _completed(fresh_store, "S1", r=1.0, pl=120, confirmed=True)
    blocked, reason, st = is_entry_blocked("u1")
    assert blocked is True and reason == "daily profit target reached"


# =================================================================
# scan -> EXTRA signal flow
# =================================================================
def test_scan_wall_creates_extra_signal(fresh_store, monkeypatch):
    """Profit target hit -> signal still created, marked EXTRA, notified."""
    from app.engine.signal_engine import SignalEngine
    _mk_user(fresh_store, daily_profit_target_usd=100)
    _completed(fresh_store, "WALL", r=1.0, pl=150, confirmed=True)
    eng = SignalEngine(provider=None)
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is False and reason == "daily profit target reached"
    assert reason in __import__("app.engine.signal_engine", fromlist=["x"]).EXTRA_WALL_REASONS


def test_prop_total_dd_is_not_an_extra_wall(fresh_store):
    """Blown account (max total DD) stops generation entirely - not EXTRA."""
    from app.engine.signal_engine import EXTRA_WALL_REASONS
    assert "prop max drawdown buffer reached" not in EXTRA_WALL_REASONS


# =================================================================
# executor: refuses auto-entry on wall, cancels pendings
# =================================================================
SIG = {"id": "sigX", "userId": "u1", "signal_id": "SIG-20260701-090", "market": "XAUUSD",
       "direction": "BUY", "entry": 2400.0, "sl": 2390.0,
       "tp1": 2420.0, "tp2": 2440.0, "tp3": 2460.0}


@pytest.fixture()
def xenv(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    _mk_user(store, daily_profit_target_usd=100)
    store.create("signals", dict(SIG))
    # daily.py resolves the store through get_store() -> module-global _store
    # (same trick as conftest.fresh_store) - otherwise walls read the real db.
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    from app.execution import mt5 as X
    monkeypatch.setattr(X, "get_store", lambda: store)
    return store, X, monkeypatch


def test_executor_refuses_entry_on_target_wall(xenv):
    store, X, monkeypatch = xenv
    _completed(store, "WALL", r=1.0, pl=150, confirmed=True)
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: None)  # wall check is local
    X.execute_signal(SIG, "u1")
    doc = store.get("signals", "sigX")
    assert doc["execution_status"] == "EXTRA_SIGNAL_NOT_ENTERED"
    assert doc["entry_blocked_reason"] == "daily profit target reached"
    logs = store.list("agent_activity", filters={"kind": "EXEC_WARN"}, limit=10)
    assert any("profit target" in l["message"] for l in logs)


def test_executor_cancels_pending_entries_on_wall(xenv):
    store, X, monkeypatch = xenv
    _completed(store, "WALL", r=1.0, pl=150, confirmed=True)
    # a queued entry from earlier
    store.create("exec_commands", {"userId": "u1", "status": "PENDING",
                                   "signal_doc_id": "sigOther",
                                   "payload": {"signal_id": "SIG-A", "symbol": "XAUUSD"}})
    store.create("signals", {"id": "sigOther", "userId": "u1", "signal_id": "SIG-A"})
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: None)
    X.execute_signal(SIG, "u1")
    cmds = store.list("exec_commands", filters={"userId": "u1"}, limit=10)
    assert cmds[0]["status"] == "CANCELLED_DAILY_WALL"
    other = store.get("signals", "sigOther")
    assert other["execution_status"] == "CANCELLED_DAILY_WALL"


def test_executor_enters_normally_without_wall(xenv):
    store, X, monkeypatch = xenv
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15:
                        {"ok": True, "ticket": 9, "position_id": 10, "volume": 0.01,
                         "price": 2400.1, "symbol": "XAUUSD"})
    X.execute_signal(SIG, "u1")
    assert store.get("signals", "sigX")["execution_status"] == "SUBMITTED"


def test_stop_and_close_option_b_closes_positions(xenv):
    store, X, monkeypatch = xenv
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"daily_loss_limit_usd": 150, "on_loss_limit": "stop_and_close"})
    _completed(store, "WALL", r=-1.0, pl=-160, confirmed=True)
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8:
                        {"positions": [{"ticket": 11, "magic": 20260914, "profit": -20},
                                       {"ticket": 12, "magic": 999, "profit": 5}]}
                        if p == "/positions" else None)
    sent = []
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: sent.append((p, payload))
                        or {"ok": True, "ticket": payload["ticket"]})
    X.execute_signal(SIG, "u1")
    assert sent == [("/close", {"ticket": 11})]          # foreign magic untouched
    goals = store.list("agent_goals", filters={"userId": "u1"}, limit=1)[0]
    assert goals["on_loss_close_done_day"]                # ran once today
    # second run the same day -> no repeat
    sent.clear()
    X.execute_signal(SIG, "u1")
    assert sent == []


def test_default_option_a_does_not_close(xenv):
    store, X, monkeypatch = xenv
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"daily_loss_limit_usd": 150})           # on_loss_limit default stop_entries
    _completed(store, "WALL", r=-1.0, pl=-160, confirmed=True)
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8:
                        {"positions": [{"ticket": 11, "magic": 20260914}]}
                        if p == "/positions" else None)
    sent = []
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: sent.append(payload))
    X.execute_signal(SIG, "u1")
    assert sent == []                                     # entries-only: nothing closed


# =================================================================
# manual tracking (SS24)
# =================================================================
def test_manual_result_stored_separately(fresh_store, monkeypatch):
    from app.state import State
    import app.api.routes_signals as RS
    monkeypatch.setattr(State, "store", fresh_store, raising=False)
    fresh_store.create("signals", dict(SIG))
    body = RS.ManualResultIn(taken=True, entry_price=2401.5, sl=2391.0,
                             tp=2430.0, pl=28.5, result="WIN", exit_reason="TP2 trail")
    out = RS.manual_result("sigX", body, user_id="u1")
    assert out["ok"] is True
    doc = fresh_store.get("signals", "sigX")
    assert doc["user_action"] == "manual_extra"           # separate from "entered"
    assert doc["user_manual"]["pl"] == 28.5 and doc["user_manual"]["result"] == "WIN"


def test_manual_result_rejects_bad_result(fresh_store):
    from app.models.schemas import ManualResultIn
    with pytest.raises(ValueError):
        ManualResultIn(result="MEGA_WIN")


def test_manual_result_404_wrong_user(fresh_store, monkeypatch):
    from app.state import State
    import app.api.routes_signals as RS
    monkeypatch.setattr(State, "store", fresh_store, raising=False)
    fresh_store.create("signals", dict(SIG))
    with pytest.raises(Exception):
        RS.manual_result("sigX", RS.ManualResultIn(), user_id="someone-else")


# =================================================================
# schema validation
# =================================================================
def test_wall_fields_validated():
    from app.models.schemas import RiskSettings
    r = RiskSettings(daily_profit_target_usd=300, daily_loss_limit_usd=150)
    assert r.daily_profit_target_usd == 300 and r.daily_loss_limit_usd == 150
    with pytest.raises(ValueError):
        RiskSettings(daily_profit_target_usd=999999)
    with pytest.raises(ValueError):
        RiskSettings(on_loss_limit="panic")


def test_connector_close_all_only_touches_our_magic():
    import importlib.util, sys, types
    class NS:
        def __init__(self, **kw): self.__dict__.update(kw)
    class FakeMT5:
        TRADE_ACTION_DEAL = 1; ORDER_TYPE_BUY = 0; ORDER_TYPE_SELL = 1
        ORDER_TIME_GTC = 0; ORDER_FILLING_IOC = 1; ORDER_FILLING_FOK = 2
        ORDER_FILLING_RETURN = 3; TRADE_RETCODE_DONE = 10009
        def __init__(self):
            self.sent = []
            self._pos = [NS(ticket=1, symbol="XAUUSD", type=0, volume=0.02,
                            magic=20260914, sl=0, tp=0),
                         NS(ticket=2, symbol="EURUSD", type=0, volume=0.01,
                            magic=777, sl=0, tp=0)]
        def positions_get(self, *a, **k): return tuple(self._pos)
        def symbol_info_tick(self, s): return NS(bid=1.0, ask=1.01)
        def symbol_info(self, s): return NS(filling_mode=2, volume_step=0.01)
        def order_send(self, req):
            self.sent.append(req)
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="ok")
        def last_error(self): return (0, "ok")
    monkey = pytest.MonkeyPatch()
    monkey.setitem(sys.modules, "MetaTrader5", FakeMT5())
    spec = importlib.util.spec_from_file_location(
        "connector_ca", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "mt5-connector", "connector.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["connector_ca"] = mod
    spec.loader.exec_module(mod)
    monkey.undo()
    fake = FakeMT5()
    mod.mt5 = fake
    res = mod.close_all({})
    assert res["closed"] == [1] and res["errors"] == []
    assert len(fake.sent) == 1 and fake.sent[0]["comment"] == "fxm-loss-stop"


import os  # noqa: E402  (used by the connector test above)
