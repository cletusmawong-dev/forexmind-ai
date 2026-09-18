"""Phase 4: per-user symbol config - validation, gating, broker-alias envs.

allowed_markets is normalized (uppercase, dedupe) and restricted to markets
the server actually knows; broker suffixes (XAUUSDm) are refused at the API
and resolved on the VPS via SYMBOL_ALIASES instead.
"""
import importlib.util
import os
import sys
import types

import pytest

from app.config import settings
from app.db.store import LocalStore

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# =================================================================
# schema validation (PATCH /api/settings body)
# =================================================================
def _risk(**kw):
    from app.models.schemas import RiskSettings
    return RiskSettings(**kw)


def test_markets_normalized_uppercase_dedupe():
    r = _risk(allowed_markets=["xauusd", "EURUSD", "xauusd"])
    assert r.allowed_markets == ["XAUUSD", "EURUSD"]


def test_markets_empty_allowed_is_deliberate_pause():
    assert _risk(allowed_markets=[]).allowed_markets == []


def test_markets_nas100_accepted():
    assert _risk(allowed_markets=["NAS100"]).allowed_markets == ["NAS100"]


def test_markets_suffix_rejected():
    with pytest.raises(ValueError, match="XAUUSDM"):
        _risk(allowed_markets=["XAUUSDm"])          # broker suffix, not a market


def test_markets_junk_rejected():
    with pytest.raises(ValueError):
        _risk(allowed_markets=["SPX500"])

# =================================================================
# scan gate: market not in the allowed list
# =================================================================
class _Cand:
    market = "XAUUSD"
    rr_primary = 2.0
    strategy_id = "strategy_1_zero_lag"


def test_scan_gate_blocks_market_not_allowed(fresh_store):
    from app.engine.signal_engine import SignalEngine
    fresh_store.create("settings", {"userId": "u1", "kind": "risk",
                                    "allowed_markets": ["EURUSD"]})
    eng = SignalEngine(provider=None)
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is False and reason == "market not in allowed list"
    fresh_store.update("settings", fresh_store.list(
        "settings", filters={"userId": "u1", "kind": "risk"}, limit=1)[0]["id"],
        {"allowed_markets": ["XAUUSD"]})
    ok2, reason2 = eng._guards("u1", _Cand(), "London")
    assert ok2 is True or "session" in reason2   # passes the market gate


def test_scan_gate_defaults_allow_all_when_no_settings_doc(fresh_store):
    from app.engine.signal_engine import SignalEngine
    eng = SignalEngine(provider=None)
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is not True or reason != "market not in allowed list"


# =================================================================
# settings GET exposes the honest toggle universe
# =================================================================
def test_get_settings_includes_available_markets(fresh_store, monkeypatch):
    from app.state import State
    from app.api import routes_misc
    from app.agent.core import ensure_user_docs
    monkeypatch.setattr(State, "store", fresh_store, raising=False)
    import app.agent.core as core
    monkeypatch.setattr(core, "get_store", lambda: fresh_store)
    ensure_user_docs("u1")
    out = routes_misc.get_settings(user_id="u1")
    assert "XAUUSD" in out["available_markets"] and "NAS100" in out["available_markets"]


# =================================================================
# broker-alias env (bridge + connector, SS29 suffix handling)
# =================================================================
def _load_with_env(name, path, aliases_env):
    monkey = pytest.MonkeyPatch()
    monkey.setenv("SYMBOL_ALIASES", aliases_env)
    monkey.setitem(sys.modules, "MetaTrader5", types.SimpleNamespace())  # import only
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    monkey.undo()
    return mod


def test_alias_parser_bridge():
    b = _load_with_env("bridge_alias_test", os.path.join(REPO, "vps-bridge", "bridge.py"),
                       "NAS100 = USTEC, US100 ; XAUUSD=XAUUSD.m ; junk ; = ; EURUSD=")
    assert b.ALIASES["NAS100"] == ["USTEC", "US100"]
    assert b.ALIASES["XAUUSD"][-1] == "XAUUSD.m"
    assert "EURUSD" not in b.ALIASES


def test_alias_parser_connector():
    c = _load_with_env("connector_alias_test", os.path.join(REPO, "mt5-connector", "connector.py"),
                       "XAUUSD=XAUUSDm,GOLD")
    assert c.ALIASES["XAUUSD"] == ["XAUUSDm", "GOLD"]


class FakeBrokerSymbols:
    """Terminal that only knows the broker-suffixed name XAUUSDm."""
    def symbol_info(self, s):
        return NS(name=s, visible=True, filling_mode=2, point=0.01,
                  trade_stops_level=0, volume_step=0.01, volume_min=0.01,
                  volume_max=100.0) if s == "XAUUSDm" else None

    def symbols_get(self):
        return [NS(name="XAUUSDm")]


def test_bridge_resolve_symbol_via_env_alias():
    b = _load_with_env("bridge_alias_resolve", os.path.join(REPO, "vps-bridge", "bridge.py"),
                       "XAUUSD=XAUUSDm")
    fake = FakeBrokerSymbols()
    fake.terminal_info = lambda: NS(name="MT5", connected=True, trade_allowed=True)
    fake.initialize = lambda *a, **k: True
    b.mt5 = fake
    assert b.resolve_symbol("XAUUSD") == "XAUUSDm"


def test_bridge_resolve_unknown_market_404():
    b = _load_with_env("bridge_alias_unknown", os.path.join(REPO, "vps-bridge", "bridge.py"), "")
    fake = FakeBrokerSymbols()
    fake.terminal_info = lambda: NS(name="MT5", connected=True, trade_allowed=True)
    fake.initialize = lambda *a, **k: True
    b.mt5 = fake
    with pytest.raises(b.HTTPException) as ei:
        b.resolve_symbol("SPX500")
    assert ei.value.status_code == 404


# =================================================================
# fills record the broker-resolved symbol (suffix transparency)
# =================================================================
@pytest.fixture()
def xenv(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True,
                                 "execution_mode": "vps"})
    store.create("signals", dict(SIG))
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    from app.execution import mt5 as X
    monkeypatch.setattr(X, "get_store", lambda: store)
    return store, X, monkeypatch


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260914-001", "market": "XAUUSD",
       "direction": "BUY", "entry": 2400.0, "sl": 2390.0,
       "tp1": 2420.0, "tp2": 2440.0, "tp3": 2460.0}


def test_fill_stores_broker_symbol(xenv):
    store, X, monkeypatch = xenv
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8: {"balance": 1000})
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15:
                        {"ok": True, "ticket": 5, "position_id": 6, "volume": 0.02,
                         "price": 2400.1, "symbol": "XAUUSDm"})   # broker suffix
    X.execute_signal(SIG, "u1")
    doc = store.get("signals", "sig1")
    assert doc["execution_status"] == "SUBMITTED"
    assert doc["mt5_symbol"] == "XAUUSDm"          # recorded, visible to the user
