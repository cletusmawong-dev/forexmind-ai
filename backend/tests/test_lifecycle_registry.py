"""Master-spec sections 3/30: strategy LIFECYCLE registry.

LIVE strategies execute; SLEEPING/RETIRED strategies may inform and research
but their execution is HARD-BLOCKED server-side (not just hidden in the UI).
The existing 9/21 EMA strategy must still execute exactly as before.
"""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X
from app.learning.versions import LIFECYCLE, RETIRED_STRATEGIES, is_live, lifecycle_of

SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260914-001", "market": "EURUSD",
       "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 6)
    monkeypatch.setattr(settings, "owner_user_id", "u1")
    return store


# ----------------------------------------------------------------- registry
def test_registry_states():
    assert LIFECYCLE["strategy_2_ema_atr"] == "LIVE"
    assert LIFECYCLE["strategy_2_mtf_sweep_bos_retest"] == "LIVE"
    assert LIFECYCLE["strategy_1_vp_pivots"] == "SLEEPING"
    assert LIFECYCLE["strategy_1_zero_lag"] == "RETIRED"
    assert is_live("strategy_2_ema_atr")
    assert not is_live("strategy_1_vp_pivots")
    assert not is_live("strategy_1_zero_lag")
    assert lifecycle_of("unknown") == "LIVE"          # fail-open for new modules
    assert "strategy_1_zero_lag" in RETIRED_STRATEGIES  # back-compat export


# -------------------------------------------------- sleeping cannot execute
def test_sleeping_strategy_cannot_execute(env, monkeypatch):
    store = env
    sig = {**SIG, "strategy_id": "strategy_1_vp_pivots"}
    store.create("signals", dict(sig))
    def boom(path, timeout=8):
        raise AssertionError("bridge MUST NOT be called for a SLEEPING strategy")
    monkeypatch.setattr(X, "bridge_get", boom)
    monkeypatch.setattr(X, "bridge_post", boom)
    X.execute_signal(sig, "u1")
    doc = store.get("signals", "sig1")
    assert doc["execution_status"] == "SKIPPED_LIFECYCLE"
    assert "SLEEPING" in (doc.get("mt5_note") or "")


def test_retired_strategy_cannot_execute(env, monkeypatch):
    store = env
    sig = {**SIG, "strategy_id": "strategy_1_zero_lag"}
    store.create("signals", dict(sig))
    def boom(path, timeout=8):
        raise AssertionError("bridge MUST NOT be called for a RETIRED strategy")
    monkeypatch.setattr(X, "bridge_get", boom)
    monkeypatch.setattr(X, "bridge_post", boom)
    X.execute_signal(sig, "u1")
    doc = store.get("signals", "sig1")
    assert doc["execution_status"] == "SKIPPED_LIFECYCLE"


def test_live_ema_still_executes(env, monkeypatch):
    """Master spec 38: the 9/21 EMA strategy path is untouched - full execute
    still reaches the bridge and records a SUBMITTED fill."""
    store = env
    sig = {**SIG, "strategy_id": "strategy_2_ema_atr"}
    store.create("signals", dict(sig))
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    monkeypatch.setattr(X, "bridge_post",
                        lambda p, payload, timeout=15: {"ok": True, "ticket": 777,
                                                        "position_id": 888,
                                                        "volume": 0.05, "price": 1.0999})
    X.execute_signal(sig, "u1")
    doc = store.get("signals", "sig1")
    assert doc["execution_status"] == "SUBMITTED"
    assert doc["mt5_ticket"] == 777
