"""Bridge v2 + connector parity: /modify_sl and /partial_close (Phase 3).

The MetaTrader5 package is Windows-only - both VPS modules are loaded with a
deterministic fake MT5 injected into sys.modules, then every test swaps the
module-global `mt5` for its own fake instance. No network, no broker.
"""
import importlib.util
import os
import sys
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMT5:
    """Minimal deterministic MT5: one open BUY 0.02 XAUUSD @2400, SL 2390, TP 2440."""
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3
    TRADE_RETCODE_DONE = 10009

    def __init__(self, pos_volume=0.02):
        self.sent = []
        self.retcode = self.TRADE_RETCODE_DONE
        self._pos = NS(ticket=1, symbol="XAUUSD", type=0, volume=pos_volume,
                       price_open=2400.0, price_current=2410.0, sl=2390.0,
                       tp=2440.0, profit=20.0, magic=20260914, time=0,
                       comment="SIG-1")
        self._info = NS(symbol="XAUUSD", visible=True, filling_mode=2,
                        point=0.01, trade_stops_level=0, volume_step=0.01,
                        volume_min=0.01, volume_max=100.0)
        self._tick = NS(bid=2410.00, ask=2410.20)

    def positions_get(self, ticket=None, symbol=None):
        return (self._pos,) if ticket in (None, self._pos.ticket) else ()

    def symbol_info(self, s):
        return self._info

    def symbol_info_tick(self, s):
        return self._tick

    def order_send(self, req):
        self.sent.append(req)
        return NS(retcode=self.retcode, order=99, position=1,
                  volume=req.get("volume", 0.0), price=2410.0, comment="done")

    def terminal_info(self):
        return NS(name="MT5", connected=True, trade_allowed=True)

    def account_info(self):
        return NS(login=1, server="s", currency="USD", balance=1000.0,
                  equity=1010.0, leverage=100, margin_free=900.0)

    def initialize(self, *a, **k):
        return True

    def symbol_select(self, s, enable):
        return True

    def symbols_get(self):
        return [NS(name="XAUUSD")]

    def history_deals_get(self, *a):
        return ()

    @staticmethod
    def last_error():
        return (0, "ok")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # pydantic resolves string annotations via this
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bridge_mod():
    os.environ["BRIDGE_TOKEN"] = "test-token"
    monkey = pytest.MonkeyPatch()
    monkey.setitem(sys.modules, "MetaTrader5", FakeMT5())
    mod = _load("bridge_under_test",
                os.path.join(REPO, "vps-bridge", "bridge.py"))
    monkey.undo()          # keep the module; tests swap mod.mt5 explicitly
    return mod


@pytest.fixture(scope="module")
def connector_mod():
    monkey = pytest.MonkeyPatch()
    monkey.setitem(sys.modules, "MetaTrader5", FakeMT5())
    mod = _load("connector_under_test",
                os.path.join(REPO, "mt5-connector", "connector.py"))
    monkey.undo()
    return mod


# =================================================================
# bridge pure helpers
# =================================================================
def test_validate_sl_buy_and_sell_ok(bridge_mod):
    v = bridge_mod.validate_sl_modify
    assert v(True, 2400.0, bid=2410.0, ask=2410.2, stops_dist=0.0) is None
    assert v(False, 2420.0, bid=2410.0, ask=2410.2, stops_dist=0.0) is None


def test_validate_sl_rejects_wrong_side_and_tight(bridge_mod):
    v = bridge_mod.validate_sl_modify
    assert v(True, 2415.0, bid=2410.0, ask=2410.2, stops_dist=0.0) is not None  # SL above bid for BUY
    assert v(False, 2405.0, bid=2410.0, ask=2410.2, stops_dist=0.0) is not None  # SL below ask for SELL
    assert v(True, 2409.98, bid=2410.0, ask=2410.2, stops_dist=0.05) is not None  # too close
    assert v(True, 0, bid=2410.0, ask=2410.2, stops_dist=0.0) is not None        # non-positive
    assert v(True, None, bid=2410.0, ask=2410.2, stops_dist=0.0) is not None


def test_split_partial_fraction_and_step(bridge_mod):
    sp = bridge_mod.split_partial
    vol, full, err = sp(None, 0.5, 0.02, 0.01, 0.01, 100.0)
    assert (vol, full, err) == (0.01, False, None)
    # never rounds UP beyond what was asked (0.015 floors to 0.01)
    vol, full, err = sp(0.015, None, 0.05, 0.01, 0.01, 100.0)
    assert (vol, full, err) == (0.01, False, None)


def test_split_partial_full_close(bridge_mod):
    vol, full, err = bridge_mod.split_partial(0.05, None, 0.05, 0.01, 0.01, 100.0)
    assert (vol, full, err) == (0.05, True, None)
    # a 0.01 position with fraction 0.99 CANNOT be split (0.0099 < min lot):
    # rejected honestly instead of surprising the user with a full close
    vol, full, err = bridge_mod.split_partial(None, 0.99, 0.01, 0.01, 0.01, 100.0)
    assert full is False and err is not None


def test_split_partial_rejections(bridge_mod):
    sp = bridge_mod.split_partial
    assert sp(None, None, 0.02, 0.01, 0.01, 100.0)[2] is not None   # nothing asked
    assert sp(None, 0.0, 0.02, 0.01, 0.01, 100.0)[2] is not None    # fraction bounds
    assert sp(None, 1.0, 0.02, 0.01, 0.01, 100.0)[2] is not None
    assert sp(0.005, None, 0.02, 0.01, 0.01, 100.0)[2] is not None  # below min lot
    # a 0.01-lot position cannot be split at all
    assert sp(None, 0.5, 0.01, 0.01, 0.01, 100.0)[2] is not None


# =================================================================
# bridge endpoints (called directly - no HTTP layer needed)
# =================================================================
def test_modify_sl_endpoint_happy(bridge_mod):
    fake = FakeMT5()
    bridge_mod.mt5 = fake
    res = bridge_mod.modify_sl(bridge_mod.SLIn(ticket=1, sl=2400.0),
                               x_bridge_token="test-token")
    assert res["ok"] is True and res["sl"] == 2400.0 and res["tp"] == 2440.0
    req = fake.sent[0]
    assert req["action"] == bridge_mod.mt5.TRADE_ACTION_SLTP
    assert req["position"] == 1 and req["tp"] == 2440.0   # TP never cleared


def test_modify_sl_endpoint_rejects_bad_side(bridge_mod):
    fake = FakeMT5()
    bridge_mod.mt5 = fake
    with pytest.raises(bridge_mod.HTTPException) as ei:
        bridge_mod.modify_sl(bridge_mod.SLIn(ticket=1, sl=2415.0),
                             x_bridge_token="test-token")
    assert ei.value.status_code == 400
    assert fake.sent == []                                # nothing reached MT5


def test_modify_sl_endpoint_404(bridge_mod):
    bridge_mod.mt5 = FakeMT5()
    with pytest.raises(bridge_mod.HTTPException) as ei:
        bridge_mod.modify_sl(bridge_mod.SLIn(ticket=42, sl=2400.0),
                             x_bridge_token="test-token")
    assert ei.value.status_code == 404


def test_modify_sl_endpoint_broker_reject(bridge_mod):
    fake = FakeMT5()
    fake.retcode = 10016                                  # invalid stops
    bridge_mod.mt5 = fake
    with pytest.raises(bridge_mod.HTTPException) as ei:
        bridge_mod.modify_sl(bridge_mod.SLIn(ticket=1, sl=2400.0),
                             x_bridge_token="test-token")
    assert ei.value.status_code == 400


def test_partial_close_endpoint_fraction(bridge_mod):
    fake = FakeMT5()
    bridge_mod.mt5 = fake
    res = bridge_mod.partial_close(bridge_mod.PartialCloseIn(ticket=1, fraction=0.5),
                                   x_bridge_token="test-token")
    assert res["ok"] is True and res["closed_volume"] == 0.01
    assert res["remaining_volume"] == 0.01 and res["closes_full"] is False
    req = fake.sent[0]
    assert req["type"] == bridge_mod.mt5.ORDER_TYPE_SELL   # opposite of BUY
    assert req["position"] == 1


def test_partial_close_endpoint_too_small(bridge_mod):
    fake = FakeMT5(pos_volume=0.01)
    bridge_mod.mt5 = fake
    with pytest.raises(bridge_mod.HTTPException) as ei:
        bridge_mod.partial_close(bridge_mod.PartialCloseIn(ticket=1, fraction=0.5),
                                 x_bridge_token="test-token")
    assert ei.value.status_code == 400
    assert fake.sent == []


# =================================================================
# connector parity
# =================================================================
def test_connector_modify_sl_happy(connector_mod):
    fake = FakeMT5()
    connector_mod.mt5 = fake
    res = connector_mod.modify_sl({"ticket": 1, "sl": 2400.0})
    assert res["ok"] is True and res["tp"] == 2440.0
    assert fake.sent[0]["action"] == connector_mod.mt5.TRADE_ACTION_SLTP


def test_connector_modify_sl_rejects_wrong_side(connector_mod):
    fake = FakeMT5()
    connector_mod.mt5 = fake
    res = connector_mod.modify_sl({"ticket": 1, "sl": 2415.0})   # above bid for BUY
    assert res["ok"] is False and fake.sent == []


def test_connector_partial_close_uses_opposite_side(connector_mod):
    fake = FakeMT5()
    connector_mod.mt5 = fake
    res = connector_mod.partial_close({"ticket": 1, "fraction": 0.5})
    assert res["ok"] is True and res["closed_volume"] == 0.01
    assert fake.sent[0]["type"] == connector_mod.mt5.ORDER_TYPE_SELL


def test_run_command_dispatch(connector_mod):
    fake = FakeMT5()
    connector_mod.mt5 = fake
    # no type -> legacy entry path
    res = connector_mod.run_command({"signal_id": "SIG-1", "symbol": "XAUUSD",
                                     "direction": "BUY", "lots": 0.02,
                                     "sl": 2390.0, "tp": 2440.0})
    assert res["ok"] is True
    assert fake.sent[0]["action"] == connector_mod.mt5.TRADE_ACTION_DEAL
    # explicit types route to management handlers
    res = connector_mod.run_command({"type": "modify_sl", "ticket": 1, "sl": 2400.0})
    assert res["ok"] is True
    assert fake.sent[-1]["action"] == connector_mod.mt5.TRADE_ACTION_SLTP
    res = connector_mod.run_command({"type": "partial_close", "ticket": 1, "fraction": 0.5})
    assert res["ok"] is True and res["closed_volume"] == 0.01


# =================================================================
# cloud primitives (app/execution/mt5.py)
# =================================================================
@pytest.fixture()
def xenv(monkeypatch, tmp_path):
    from app.config import settings
    from app.db.store import LocalStore
    from app.execution import mt5 as X
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    return store, X, settings, monkeypatch


def test_cloud_modify_sl_vps_calls_bridge(xenv):
    store, X, settings, monkeypatch = xenv
    calls = []
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: calls.append((p, payload))
                        or {"ok": True, "ticket": 1, "sl": payload["sl"]})
    res = X.modify_sl("u1", 1, 2400.0, reason="TP2 protection")
    assert res["ok"] is True
    assert calls == [("/modify_sl", {"ticket": 1, "sl": 2400.0})]
    logs = store.list("agent_activity", filters={"kind": "EXEC"}, limit=5)
    assert any("SL moved" in l["message"] for l in logs)


def test_cloud_partial_close_vps_payload(xenv):
    store, X, settings, monkeypatch = xenv
    calls = []
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: calls.append((p, payload))
                        or {"ok": True, "closed_volume": 0.01, "remaining_volume": 0.01})
    res = X.partial_close_position("u1", 1, fraction=0.5)
    assert res["closed_volume"] == 0.01
    assert calls == [("/partial_close", {"ticket": 1, "fraction": 0.5})]


def test_cloud_modify_sl_off_refused(xenv):
    store, X, settings, monkeypatch = xenv
    monkeypatch.setattr(settings, "execution_mode", "off")
    monkeypatch.setattr(settings, "bridge_url", "")
    sent = []
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: sent.append(payload))
    res = X.modify_sl("u1", 1, 2400.0)
    assert res["ok"] is False and res["error"] == "execution off"
    assert sent == []


def test_cloud_management_queues_for_manual_pc(xenv):
    store, X, settings, monkeypatch = xenv
    monkeypatch.setattr(settings, "execution_mode", "off")   # user-level override below
    goals = store.list("agent_goals", filters={"userId": "u1"}, limit=1)[0]
    store.update("agent_goals", goals["id"], {"execution_mode": "manual"})
    res = X.modify_sl("u1", 7, 2395.0)
    assert res["queued"] is True
    res2 = X.partial_close_position("u1", 7, fraction=0.5)
    assert res2["queued"] is True
    pulled = X.pull_commands("u1")
    assert sorted(p["type"] for p in pulled) == ["modify_sl", "partial_close"]
    by_type = {p["type"]: p for p in pulled}
    assert by_type["modify_sl"]["ticket"] == 7 and by_type["modify_sl"]["sl"] == 2395.0
    assert by_type["partial_close"]["fraction"] == 0.5


def test_cloud_modify_sl_never_raises(xenv):
    store, X, settings, monkeypatch = xenv
    def boom(p, payload, timeout=15):
        raise RuntimeError("bridge down")
    monkeypatch.setattr(X, "bridge_post", boom)
    res = X.modify_sl("u1", 1, 2400.0)
    assert res["ok"] is False
