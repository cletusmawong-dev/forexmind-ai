"""Phase 8+9: AI trade manager - decision validation, deterministic gate,
TP ladder hard rules, reconciliation, audit trail.

The AI layer is faked deterministically (fake router); the broker is a fake
bridge. Contract under test: AI NEVER enters; hard rules run without AI;
malformed AI output degrades to deterministic HOLD; every decision is
audited; decisions expire; SL protection is monotonic.
"""
import time
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.db.store import LocalStore

MAGIC = 20260914
NOW = time.time()


def _mk_world(store, **risk_over):
    store.create("agent_goals", {"userId": "u1", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0,
                                 "execution_enabled": True,
                                 "execution_mode": "vps"})
    risk = {"userId": "u1", "kind": "risk", "allowed_markets": ["XAUUSD"],
            "sessions": ["London", "NewYork", "Asian", "Late"],
            "max_signals_per_day": 6, "min_rr": 1.5, "max_daily_loss_pct": 3.0,
            "risk_per_trade_pct": 1.0, "session_tz": "UTC",
            "ai_manage_enabled": True}
    risk.update(risk_over)
    store.create("settings", risk)
    store.create("signals", dict(SIG))


SIG = {"id": "sig1", "userId": "u1", "signal_id": "SIG-20260917-001",
       "market": "XAUUSD", "strategy_name": "Zero Lag", "direction": "BUY",
       "entry": 2400.0, "sl": 2390.0, "tp1": 2420.0, "tp2": 2440.0, "tp3": 2460.0,
       "risk": 10.0, "timeframe": "15M", "status": "TP1_HIT", "completed": False}

POS = {"ticket": 11, "symbol": "XAUUSD", "app_market": "XAUUSD", "type": "BUY",
       "volume": 0.02, "price_open": 2400.0, "price_current": 2421.0,
       "sl": 2390.0, "tp": 2440.0, "profit": 21.0, "magic": MAGIC,
       "time": NOW - 3600, "comment": "SIG-20260917-001", "signal_id": "SIG-20260917-001"}


class FakeRouter:
    def __init__(self, response=None, text=""):
        self.response = response or {"text": text, "model": "fake/qwen",
                                     "layer": "primary", "escalated": False,
                                     "fallback": False, "notes": []}
        self.calls = []

    def analyze(self, prompt, context, escalate=False, user_id=None):
        self.calls.append({"escalate": escalate, "context": context})
        return dict(self.response)


@pytest.fixture()
def world(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    _mk_world(store)
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.execution import mt5 as X
    monkeypatch.setattr(X, "bridge_get", lambda p, timeout=8:
                        {"positions": [dict(POS)]} if p == "/positions" else {"balance": 1000})
    sent = []
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15:
                        sent.append((p, payload)) or {"ok": True, **(payload or {})})
    monkeypatch.setattr(X, "user_mode", lambda uid: "vps")
    from app.aimanager.engine import TradeManager
    router = FakeRouter(text='{"action": "HOLD", "continuation_assessment": "STRONG",'
                             '"confidence": 0.7, "reason_codes": ["TEST"], '
                             '"risk_state": "CONTROLLED"}')
    mgr = TradeManager(provider=None, router=router)
    return store, X, mgr, router, sent


def _hold():
    return {"text": '{"action": "HOLD", "continuation_assessment": "WEAKENING",'
                    '"confidence": 0.4, "reason_codes": ["M15_WEAK"], '
                    '"risk_state": "ELEVATED"}',
            "model": "qwen", "layer": "primary", "fallback": False, "notes": []}


# =================================================================
# decisions.py - SS18 validation
# =================================================================
def test_decision_valid_with_fences():
    from app.aimanager import decisions
    raw = '```json\n{"action": "protect", "recommended_sl": 2400, "confidence": 0.8,\n' \
          '"reason_codes": ["h1 bullish"], "partial_fraction": 0.9}\n```'
    d = decisions.validate(raw)
    assert d["action"] == "PROTECT" and d["recommended_sl"] == 2400.0
    assert d["reason_codes"] == ["H1 BULLISH"]


def test_decision_reject_bad_action_and_missing_evidence():
    from app.aimanager import decisions
    with pytest.raises(decisions.DecisionError):
        decisions.validate('{"action": "BUY", "reason_codes": ["X"], "confidence": 0.5}')
    with pytest.raises(decisions.DecisionError):
        decisions.validate('{"action": "HOLD", "confidence": 0.5}')       # no reasons
    with pytest.raises(decisions.DecisionError):
        decisions.validate('{"action": "HOLD", "reason_codes": ["X"], "confidence": 1.4}')
    with pytest.raises(decisions.DecisionError):
        decisions.validate("looks bullish to me")                          # no JSON


def test_decision_partial_requires_fraction():
    from app.aimanager import decisions
    with pytest.raises(decisions.DecisionError):
        decisions.validate('{"action": "PARTIAL_PROFIT", "reason_codes": ["X"], "confidence": 0.5}')
    d = decisions.validate('{"action": "PARTIAL_PROFIT", "partial_fraction": 0.5, '
                           '"reason_codes": ["X"], "confidence": 0.5}')
    assert d["partial_fraction"] == 0.5


def test_decision_sl_only_with_protect():
    from app.aimanager import decisions
    with pytest.raises(decisions.DecisionError):
        decisions.validate('{"action": "EXIT", "recommended_sl": 2400, '
                           '"reason_codes": ["X"], "confidence": 0.5}')


def test_deterministic_hold_is_the_only_invented_decision():
    from app.aimanager import decisions
    d = decisions.deterministic_hold("ai down")
    assert d["action"] == "HOLD" and d["confidence"] == 0.0


# =================================================================
# risk_gate.py - SS19/SS20 deterministic validation
# =================================================================
def _decision(action="PROTECT", sl=2400.0, ts=None, frac=None):
    return {"action": action, "continuation_assessment": "STRONG", "next_target": None,
            "confidence": 0.8, "reason_codes": ["EVIDENCE"], "risk_state": "CONTROLLED",
            "recommended_sl": sl, "partial_fraction": frac, "escalation_required": False,
            "raw": {}}


def test_gate_stale_decision_rejected():
    from app.aimanager import risk_gate
    ok, verdict, plan = risk_gate.validate_action(
        "u1", POS, _decision(), snapshot_ts=NOW - 999, current_sl=2390)
    assert ok is False and verdict == "STALE_DECISION" and plan == {}


def test_gate_duplicate_suppressed(world):
    from app.aimanager import risk_gate
    ok1, _, _ = risk_gate.validate_action("u1", POS, _decision(sl=2401),
                                          snapshot_ts=NOW, current_sl=2390,
                                          last_action={"action": "PROTECT", "sl": 2401,
                                                       "ts": NOW - 10})
    assert ok1 is False


def test_gate_protect_wrong_side_and_loosening():
    from app.aimanager import risk_gate
    ok, verdict, _ = risk_gate.validate_action("u1", POS, _decision(sl=2425),
                                               snapshot_ts=NOW, current_sl=2390)
    assert ok is False and verdict == "SL_WRONG_SIDE"          # BUY: SL above price
    ok, verdict, _ = risk_gate.validate_action("u1", POS, _decision(sl=2380),
                                               snapshot_ts=NOW, current_sl=2390)
    assert ok is False and verdict == "SL_WOULD_LOOSEN_RISK"   # must only tighten


def test_gate_protect_ok_and_exit_plan():
    from app.aimanager import risk_gate
    ok, verdict, plan = risk_gate.validate_action("u1", POS, _decision(sl=2400),
                                                  snapshot_ts=NOW, current_sl=2390)
    assert ok is True and plan == {"modify_sl": 2400.0}
    ok, verdict, plan = risk_gate.validate_action("u1", POS, _decision(action="EXIT", sl=None),
                                                  snapshot_ts=NOW, current_sl=2390)
    assert ok is True and plan == {"close_full": True}


# =================================================================
# engine - discovery, TP ladder, hard rules, audit
# =================================================================
def test_tick_skips_when_disabled(world):
    store, X, mgr, router, sent = world
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"ai_manage_enabled": False})
    out = mgr.tick("u1")
    assert out["skipped"] and router.calls == []


def test_tp2_hit_hard_rule_moves_sl_to_tp1_even_when_ai_says_hold(world):
    """SS15: THE hard rule. AI returns HOLD - the lock happens anyway."""
    store, X, mgr, router, sent = world
    router.response = _hold()
    pos = dict(POS, price_current=2441.0)                    # beyond TP2
    monkey = getattr(world, "_monkey", None)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    out = mgr.tick("u1")
    assert ("/modify_sl", {"ticket": 11, "sl": 2420.0}) in sent   # SL := TP1
    st = mgr._state[11]
    assert st["tp_state"] >= 2
    # AI was still asked about TP3 continuation (SS15 reassess)
    assert len(router.calls) >= 1
    audit = store.list("ai_decisions", filters={"userId": "u1"}, limit=10)
    assert any(a["gate_verdict"] in ("TP2_LADDER_APPLIED", "OK_HOLD") for a in audit)


def test_tp3_hit_closes_without_ai(world):
    store, X, mgr, router, sent = world
    router.response = _hold()
    pos = dict(POS, price_current=2461.0)                    # beyond TP3
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    calls_before = len(router.calls)
    mgr.tick("u1")
    assert ("/close", {"ticket": 11}) in sent
    assert len(router.calls) == calls_before                 # position gone - no AI


def test_tp1_hit_ai_decides_default(world):
    """SS14: TP1 does NOT auto-close; the AI decides (here: PROTECT)."""
    store, X, mgr, router, sent = world
    router.response = {"text": '{"action": "PROTECT", "recommended_sl": 2410, '
                               '"confidence": 0.75, "reason_codes": ["M15_STRONG", "H1_SUPPORT"], '
                               '"risk_state": "CONTROLLED"}',
                       "model": "qwen", "layer": "primary", "fallback": False, "notes": []}
    pos = dict(POS, price_current=2420.5)                    # just past TP1
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    out = mgr.tick("u1")
    assert ("/modify_sl", {"ticket": 11, "sl": 2410.0}) in sent
    assert out["reviews"] >= 1


def test_tp1_policy_protect_is_deterministic(world):
    store, X, mgr, router, sent = world
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"tp1_policy": "protect"})
    router.response = _hold()
    pos = dict(POS, price_current=2420.5)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr.tick("u1")
    assert ("/modify_sl", {"ticket": 11, "sl": 2400.0}) in sent   # breakeven lock


def test_malformed_ai_json_degrades_to_hold(world):
    store, X, mgr, router, sent = world
    router.response = {"text": "the trade looks fine honestly", "model": "qwen",
                       "layer": "primary", "fallback": False, "notes": []}
    pos = dict(POS, price_current=2410.0)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr._state.clear()                                        # position_opened trigger
    out = mgr.tick("u1")
    assert sent == []                                         # nothing executed
    audit = store.list("ai_decisions", filters={"userId": "u1"}, limit=5)
    assert audit and audit[0]["decision"]["action"] == "HOLD"  # deterministic hold
    assert audit[0]["model"] == "qwen"


def test_reconcile_repairs_violated_tp2_lock(world):
    """SS5: after restart, if TP2 was surpassed but SL is looser than TP1,
    the deterministic repair happens WITHOUT any AI call."""
    store, X, mgr, router, sent = world
    pos = dict(POS, price_current=2445.0, sl=2395.0)          # beyond TP2, SL < TP1
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    calls = len(router.calls)
    n = mgr.reconcile("u1")
    assert n == 1
    assert ("/modify_sl", {"ticket": 11, "sl": 2420.0}) in sent
    assert len(router.calls) == calls                          # deterministic repair


def test_exit_uses_close_primitive(world):
    store, X, mgr, router, sent = world
    router.response = {"text": '{"action": "EXIT", "confidence": 0.9, '
                               '"reason_codes": ["H1_REVERSAL", "NEWS_RISK"], '
                               '"risk_state": "CRITICAL"}',
                       "model": "qwen", "layer": "primary", "fallback": False, "notes": []}
    pos = dict(POS, price_current=2412.0)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr._state.clear()
    mgr.tick("u1")
    assert ("/close", {"ticket": 11}) in sent


def test_every_review_is_audited(world):
    store, X, mgr, router, sent = world
    pos = dict(POS, price_current=2411.0)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr._state.clear()
    mgr.tick("u1")
    docs = store.list("ai_decisions", filters={"userId": "u1"}, limit=10)
    assert docs
    a = docs[0]
    for k in ("position_ticket", "trigger", "model", "decision", "gate_verdict",
              "input_context_version", "createdAt"):
        assert k in a


# =================================================================
# close_position primitive + connector close_full
# =================================================================
def test_close_position_vps_and_off(monkeypatch, tmp_path):
    from app.execution import mt5 as X
    store = LocalStore(path=str(tmp_path / "db.json"))
    store.create("agent_goals", {"userId": "u1", "execution_enabled": True,
                                 "execution_mode": "vps"})
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(X, "get_store", lambda: store)
    monkeypatch.setattr(settings, "execution_mode", "mt5_bridge")
    monkeypatch.setattr(settings, "bridge_url", "http://fake:8700")
    sent = []
    monkeypatch.setattr(X, "bridge_post", lambda p, payload, timeout=15:
                        sent.append((p, payload)) or {"ok": True, "ticket": 1})
    res = X.close_position("u1", 11, reason="AI EXIT")
    assert res["ok"] is True and sent == [("/close", {"ticket": 11})]
    # user-level mode off (env default only applies without an explicit mode)
    goals = store.list("agent_goals", filters={"userId": "u1"}, limit=1)[0]
    store.update("agent_goals", goals["id"], {"execution_mode": "off"})
    res2 = X.close_position("u1", 11)
    assert res2["ok"] is False and res2["error"] == "execution off"


def test_connector_close_full_and_dispatch():
    import importlib.util
    import sys
    import types

    class NS:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeMT5:
        TRADE_ACTION_DEAL = 1; ORDER_TYPE_BUY = 0; ORDER_TYPE_SELL = 1
        ORDER_TIME_GTC = 0; ORDER_FILLING_IOC = 1; ORDER_FILLING_FOK = 2
        ORDER_FILLING_RETURN = 3; TRADE_RETCODE_DONE = 10009

        def __init__(self):
            self.sent = []
            self._pos = NS(ticket=11, symbol="XAUUSD", type=0, volume=0.02,
                           magic=MAGIC, sl=0, tp=0)
        def positions_get(self, ticket=None, *a, **k):
            return (self._pos,) if ticket in (None, 11) else ()
        def symbol_info_tick(self, s): return NS(bid=2410.0, ask=2410.2)
        def symbol_info(self, s): return NS(filling_mode=2, volume_step=0.01)
        def order_send(self, req):
            self.sent.append(req)
            return NS(retcode=self.TRADE_RETCODE_DONE, comment="ok", price=2410.0)
        def last_error(self): return (0, "ok")

    monkey = pytest.MonkeyPatch()
    fake = FakeMT5()
    monkey.setitem(sys.modules, "MetaTrader5", fake)
    import os
    spec = importlib.util.spec_from_file_location(
        "connector_cf", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "mt5-connector", "connector.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["connector_cf"] = mod
    spec.loader.exec_module(mod)
    monkey.undo()
    mod.mt5 = fake
    res = mod.run_command({"type": "close_full", "ticket": 11})
    assert res["ok"] is True and res["closes_full"] is True
    assert fake.sent[0]["position"] == 11 and fake.sent[0]["volume"] == 0.02


# =================================================================
# Phase 9: policy variants + schema validation
# =================================================================
def test_tp3_hold_ai_leaves_management_to_ai(world):
    """tp3_policy=hold_ai: TP3 does NOT close; the AI keeps managing."""
    store, X, mgr, router, sent = world
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"tp3_policy": "hold_ai"})
    router.response = _hold()
    pos = dict(POS, price_current=2461.0)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr.tick("u1")
    assert ("/close", {"ticket": 11}) not in sent            # no final close
    # the TP2 lock still fired on the way up (SS15 is unconditional)
    assert ("/modify_sl", {"ticket": 11, "sl": 2420.0}) in sent
    assert mgr._state[11]["tp_state"] >= 3


def test_tp1_partial_policy_closes_half(world):
    store, X, mgr, router, sent = world
    store.update("settings", store.list("settings", filters={"userId": "u1"}, limit=1)[0]["id"],
                 {"tp1_policy": "partial"})
    router.response = _hold()
    pos = dict(POS, price_current=2420.5)
    X.bridge_get = lambda p, timeout=8: {"positions": [pos]} if p == "/positions" else {"balance": 1000}
    mgr.tick("u1")
    assert ("/partial_close", {"ticket": 11, "fraction": 0.5}) in sent


def test_tp_policy_schema_validation():
    from app.models.schemas import RiskSettings
    r = RiskSettings(tp1_policy="protect", tp3_policy="hold_ai", ai_manage_enabled=True)
    assert r.tp1_policy == "protect" and r.ai_manage_enabled is True
    with pytest.raises(ValueError):
        RiskSettings(tp1_policy="yolo")
    with pytest.raises(ValueError):
        RiskSettings(tp3_policy="close_everything")


def test_patch_settings_accepts_manager_fields(fresh_store, monkeypatch):
    import app.agent.core as core
    monkeypatch.setattr(core, "get_store", lambda: fresh_store)
    out = core.patch_risk("u1", {"ai_manage_enabled": True,
                                 "tp1_policy": "partial", "tp3_policy": "close"})
    assert out["ai_manage_enabled"] is True and out["tp1_policy"] == "partial"
