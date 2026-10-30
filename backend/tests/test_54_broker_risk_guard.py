"""Dollar-risk guard + broker-truth sizing (postmortem 2026-10-30).

Deal #41479978: NAS100 SELL sized 0.26 lots against a stale static pip
table ($1/pt) - the Aqua broker charges $10/pt, so the order risked $482
against the user's $50 setting. The firm's daily-loss liquidation then
closed every position and BREACHED the account. Contract under test:
  * lot sizing is re-derived from the broker's OWN tick values
    (bridge /symbol_info), never from a static table alone;
  * the FINAL order's true dollar risk must be within 35% of the user's
    dollar risk setting - anything bigger is refused loudly.
"""
import json
import tempfile

import pytest

from app.execution.mt5 import _pip_value_usd as _REAL_pip_value_usd


def _world(monkeypatch, pip_table_override=None, symbol_info=None):
    from app.config import settings
    settings.owner_user_id = "u1"
    import app.execution.mt5 as m5
    from app.db.store import LocalStore
    store = LocalStore(path=str(tempfile.mktemp()))
    m5.get_store = lambda: store
    monkeypatch.setattr(m5, "user_mode", lambda u: "vps")
    monkeypatch.setattr(m5, "execution_enabled", lambda u: True)
    monkeypatch.setattr(m5, "_goals_doc",
                        lambda u: {"risk_per_trade_pct": 1.0,
                                   "risk_per_trade_usd": 50.0})
    monkeypatch.setattr(m5, "_executed_today", lambda u: 0)
    monkeypatch.setattr(m5, "_lot_mode", lambda u: "medium")
    import app.api.routes_accounts as ra
    monkeypatch.setattr(ra, "active_account", lambda u: None)
    import app.learning.versions as lv
    monkeypatch.setattr(lv, "lifecycle_of", lambda sid: "LIVE")
    import app.engine.daily as daily
    monkeypatch.setattr(daily, "is_entry_blocked", lambda u: (False, None, {}))
    monkeypatch.setattr(daily, "daily_state",
                        lambda u: {"total_usd": 0.0, "consecutive_losses": 0})
    import app.risk_checks as rc
    monkeypatch.setattr(rc, "check_entry", lambda *a, **k: (True, None, None))
    store.create("agent_goals", {"userId": "u1", "account_balance": 5000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True,
                                 "execution_mode": "vps"})
    if pip_table_override is not None:
        monkeypatch.setattr(m5, "PIP_VALUE", dict(pip_table_override))
    # this suite verifies the BROKER-TRUTH path: restore the real
    # bridge-backed lookup (conftest stubs it to the static table)
    monkeypatch.setattr(m5, "_pip_value_usd", _REAL_pip_value_usd)
    m5._symbol_info_cache.clear()

    def fget(path, timeout=8):
        if path == "/account":
            return {"balance": 5050.72}
        if path.startswith("/symbol_info"):
            return symbol_info
        if path.startswith("/rates"):
            return {"bars": [{"c": 28802.0}]}
        if path.startswith("/quote"):
            return {"bid": 28724.97, "ask": 28725.47}
        return None

    monkeypatch.setattr(m5, "bridge_get", fget)
    calls = []
    monkeypatch.setattr(m5, "bridge_post",
                        lambda p, payload, timeout=15:
                        calls.append((p, dict(payload)))
                        or {"ok": True, "ticket": 1,
                            "volume": payload.get("lots"), "price": 28724.97})
    return m5, store, calls


SIG_NAS = {"id": "snas", "userId": "u1", "signal_id": "SIG-NAS", "market": "NAS100",
           "strategy_name": "H1 Trend Breakout", "direction": "SELL",
           "entry": 28802.17, "sl": 28987.65, "tp1": 28539.49, "tp2": 28354.0,
           "tp3": 28168.52, "risk": 185.48, "timeframe": "15M",
           "status": "LIVE", "completed": False}


def test_stale_table_cannot_oversize_broker_truth_resizes(monkeypatch):
    """The 10-30 breach replay: static table says $1/pt, broker says $10/pt.
    Sizing must come out ~0.03 lots ($50), NOT 0.26 lots ($482)."""
    m5, store, calls = _world(
        monkeypatch,
        pip_table_override={"EURUSD": 10.0, "GBPUSD": 10.0, "USDJPY": 6.3,
                            "XAUUSD": 10.0, "XAGUSD": 50.0, "NAS100": 1.0},
        symbol_info={"trade_tick_value": 1.0, "trade_tick_size": 0.1})
    store.create("signals", dict(SIG_NAS))
    m5.execute_signal(SIG_NAS, "u1")
    doc = store.get("signals", "snas")
    ex = [p for op, p in calls if op == "/execute"]
    assert ex, f"no order: {doc.get('execution_status')} {(doc.get('mt5_note') or '')[:120]}"
    lots = ex[-1]["lots"]
    risk = 185.48 / 1.0 * 10.0 * lots          # Aqua truth: $10/pt/lot
    assert risk <= 50.0 * 1.35, f"order still risks ${risk:.2f}"


def test_unanswerable_broker_truth_refuses_oversize(monkeypatch):
    """If the broker cannot be asked AND the (stale) table disagrees with
    reality, the guard refuses the order - a wrong table alone can never
    breach the account again."""
    m5, store, calls = _world(
        monkeypatch,
        pip_table_override={"EURUSD": 10.0, "GBPUSD": 10.0, "USDJPY": 6.3,
                            "XAUUSD": 10.0, "XAGUSD": 50.0, "NAS100": 1.0},
        symbol_info=None)                       # bridge cannot answer
    store.create("signals", dict(SIG_NAS))
    m5.execute_signal(SIG_NAS, "u1")
    doc = store.get("signals", "snas")
    assert doc["execution_status"] == "REJECTED_PRETRADE"
    assert "CONTRACT_TRUTH_UNAVAILABLE" in (doc.get("mt5_note") or "")
    assert not [1 for op, _ in calls if op == "/execute"], "order reached the broker"


def test_correct_broker_values_trade_the_right_size(monkeypatch):
    """With truthful inputs the order sails through at ~$50 risk."""
    m5, store, calls = _world(
        monkeypatch,
        symbol_info={"trade_tick_value": 1.0, "trade_tick_size": 0.1})
    store.create("signals", dict(SIG_NAS))
    m5.execute_signal(SIG_NAS, "u1")
    doc = store.get("signals", "snas")
    ex = [p for op, p in calls if op == "/execute"]
    assert doc["execution_status"] == "SUBMITTED" and ex
    lots = ex[-1]["lots"]
    assert 0.02 <= lots <= 0.05                 # ~$50 at $10/pt/lot
