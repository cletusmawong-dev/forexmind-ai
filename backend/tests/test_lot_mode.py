"""Risk lot-mode setting (user request 2026-09-21): low / medium / high.

medium = exactly the previous behavior; low = ~half the computed lot;
high = ~1.5x; floored to the broker 0.01 step, never below it. The
multiplier applies AFTER the risk-% math in BOTH execution paths.
"""
import pytest

from app.config import settings
from app.db.store import LocalStore
from app.execution import mt5 as X


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True})
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_max_trades_per_day", 99)
    return store


def set_mode(store, mode):
    docs = store.list("settings", filters={"userId": "u1", "kind": "risk"}, limit=1)
    if docs:
        store.update("settings", docs[0]["id"], {"lot_mode": mode})
    else:
        store.create("settings", {"userId": "u1", "kind": "risk", "lot_mode": mode})


# ------------------------------------------------------------------ math
def test_apply_lot_mode_math():
    assert X.apply_lot_mode(0.40, "medium") == 0.40     # unchanged
    assert X.apply_lot_mode(0.40, "low") == 0.20        # half
    assert X.apply_lot_mode(0.40, "high") == 0.60       # 1.5x
    assert X.apply_lot_mode(0.13, "high") == 0.19       # 0.195 floors DOWN
    assert X.apply_lot_mode(0.13, "low") == 0.06        # 0.065 floors DOWN
    assert X.apply_lot_mode(0.01, "low") == 0.01        # broker min respected
    assert X.apply_lot_mode(0.40, "bogus") == 0.40      # unknown -> 1.0


# ------------------------------------------------------- mode resolution
def test_lot_mode_resolution(env):
    assert X._lot_mode("u1") == "medium"                # no doc -> default
    set_mode(env, "high")
    assert X._lot_mode("u1") == "high"
    set_mode(env, "LOW")
    assert X._lot_mode("u1") == "low"                   # case tolerant
    docs = env.list("settings", filters={"userId": "u1", "kind": "risk"}, limit=1)
    env.update("settings", docs[0]["id"], {"lot_mode": "garbage"})
    assert X._lot_mode("u1") == "medium"                # junk -> safe default


# --------------------------------------------------- settings round-trip
def test_patch_risk_lot_mode_roundtrip(fresh_store):
    from app.agent.core import get_risk, patch_risk
    from app.agent import core as core_mod
    from app.db import store as store_mod
    store = store_mod._store
    from app.agent.core import ensure_user_docs
    ensure_user_docs("u-lot")
    assert get_risk("u-lot")["lot_mode"] == "medium"   # honest default pre-PATCH
    out = patch_risk("u-lot", {"lot_mode": "high"})
    assert out["lot_mode"] == "high"
    assert get_risk("u-lot")["lot_mode"] == "high"
    with pytest.raises(ValueError):
        patch_risk("u-lot", {"lot_mode": "extreme"})


# ------------------------------------------------ executor, both paths
SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-LOT-001", "market": "EURUSD",
       "direction": "SELL", "entry": 1.1000, "sl": 1.1020,
       "tp1": 1.0950, "tp2": 1.0900, "tp3": 1.0850}   # 20 pip SL -> base 0.05


def _vps_lots(monkeypatch):
    sent = {}
    monkeypatch.setattr(X, "bridge_get",
                        lambda p, timeout=8: {"balance": 1000} if p == "/account" else None)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake:8700")

    def fake_post(path, payload, timeout=15):
        sent.update(payload)
        return {"ok": True, "ticket": 1, "position_id": 2, "volume": payload["lots"], "price": 1.09}
    monkeypatch.setattr(X, "bridge_post", fake_post)
    X.execute_signal(SIG, "u1")
    return sent["lots"]


def test_executor_low_mode_halves_lots(env, monkeypatch):
    set_mode(env, "low")
    assert _vps_lots(monkeypatch) == 0.02      # 0.05 base -> 0.025 -> floor 0.02


def test_executor_high_mode_raises_lots(env, monkeypatch):
    set_mode(env, "high")
    assert _vps_lots(monkeypatch) == 0.07      # 0.05 * 1.5 = 0.075 -> floor 0.07


def test_executor_medium_is_previous_behavior(env, monkeypatch):
    assert _vps_lots(monkeypatch) == 0.05      # identical to before the feature
