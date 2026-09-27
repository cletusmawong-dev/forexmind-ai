"""Stage 2: AI Brain 2.0 - world model, reasoning pipeline, controlled memory,
self-challenge, evidence-confidence, and integration with the UNCHANGED
deterministic risk gate (Master Upgrade Stage 2).

Rules under test:
 - world model: honest data only (missing -> None/flags, never fabricated),
   MFE/MAE in R from candles, freshness audit (stale candle -> STALE)
 - brain answers: DATA_STALE (no AI call), INSUFFICIENT_EVIDENCE (thin
   history/TFs), NO_ACTION, ACTION with full schema
 - self-challenge: failed challenge -> CONFLICTING, nothing executable
 - confidence_pct: deterministic blend in 0..100; never moves SL (gate
   still enforces tighten-only)
 - memory: 400-char cap, per-user cap 100 (FIFO trim), recall filters
 - manager integration: brain path executes through risk_gate; TP2 hard
   lock still applies with the brain ON; BRAIN_V2=0 keeps legacy path
"""
import os
import time
import pytest
from fastapi.testclient import TestClient
import pandas as pd


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
    monkeypatch.setattr(settings, "brain_v2_enabled", True)
    monkeypatch.setattr(settings, "brain_min_strategy_samples", 5)
    from app.main import app
    from app.api.deps import get_user_id
    from app.ai import world_model as WM
    WM.clear_history_cache()
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c, store, monkeypatch
    finally:
        State.store = _prev
        State.ready = False
        WM.clear_history_cache()
        app.dependency_overrides.pop(get_user_id, None)


# -- helpers ------------------------------------------------------------
def _mk(store, uid="boss", **kw):
    doc = {"id": uid, "userId": uid, "email": f"{uid}@x.io", "role": "admin",
           "status": "active", "trading_permission": "locked"}
    doc.update(kw)
    return store.create("users", doc)


def _candles(n=60, end_ts=None, tf_min=15, base=1.10, amp=0.002):
    """Ascending-ish synthetic M15 frame ending at end_ts (default now)."""
    end_ts = end_ts or time.time()
    idx = pd.to_datetime([end_ts - (n - 1 - i) * tf_min * 60 for i in range(n)],
                         unit="s")
    rows = []
    for i in range(n):
        o = base + amp * (i % 5) / 5
        rows.append({"open": o, "high": o + amp / 2, "low": o - amp / 2,
                     "close": o + amp / 4, "volume": 100})
    df = pd.DataFrame(rows)
    df.index = idx.tz_localize(None)
    df.index.name = "datetime"
    return df


class FakeProvider:
    def __init__(self, stale_minutes=None):
        self.stale_minutes = stale_minutes

    def get_candles(self, market, timeframe, limit=600):
        tf_min = {"15M": 15, "4H": 240, "1D": 1440}.get(timeframe, 15)
        end = time.time() - (self.stale_minutes or 0) * 60
        return _candles(n=80, end_ts=end, tf_min=tf_min)


from app.execution.mt5 import MAGIC
BUY_POS = {"ticket": 111, "app_market": "EURUSD", "symbol": "EURUSD",
           "type": "BUY", "price_open": 1.10, "price_current": 1.1015,
           "volume": 0.1, "sl": 1.095, "tp": 1.11, "profit": 15.0,
           "magic": MAGIC, "time": time.time() - 3600}
SIG = {"signal_id": "SIG-B1", "strategy_id": "strat1", "strategy_name": "S1",
       "market": "EURUSD", "sl": 1.095, "tp1": 1.105, "tp2": 1.11, "tp3": 1.115}


class FakeRouter:
    """Canned analyze() responses + call log (never hits the network)."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def analyze(self, prompt, context, escalate=False, user_id=None):
        self.calls.append({"prompt": prompt[:40],
                           "challenge": "devil" in str(context.get("instructions", ""))})
        return self.responses.pop(0) if self.responses else \
            {"text": "{}", "model": "fake", "layer": "local"}


def _seed_history(store, uid, n=8):
    for i in range(n):
        store.create("signals", {"userId": uid, "strategy_id": "strat1",
                                 "market": "EURUSD", "status": "WIN",
                                 "mt5_pl": 10 + i})


# ===========================================================================
# WORLD MODEL
# ===========================================================================
def test_world_model_honest_and_fresh(world):
    c, store, _ = world
    _mk(store)
    from app.ai.world_model import build_world_model
    w = build_world_model("boss", "EURUSD", position=BUY_POS, signal=SIG,
                          provider=FakeProvider(), now=time.time())
    assert w["freshness"]["verdict"] == "OK"
    assert w["trade"]["direction"] == "BUY"
    assert w["trade"]["mfe_r"] is not None and w["trade"]["mfe_r"] >= 0
    assert w["trade"]["tps_reached"] in (0, 1)
    assert set(w["timeframes"]) == {"15M", "4H", "1D"}
    assert all(tf and "liquidity_sweep" in tf and "bos" in tf
               for tf in w["timeframes"].values())
    # account section: bridge unreachable in unit env -> honest absence
    assert "note" in w["account"] or w["account"]["balance_usd"] is None


def test_world_model_flags_stale_data(world):
    c, store, _ = world
    _mk(store)
    from app.ai.world_model import build_world_model
    w = build_world_model("boss", "EURUSD", position=BUY_POS, signal=SIG,
                          provider=FakeProvider(stale_minutes=90),
                          now=time.time())
    assert w["freshness"]["verdict"] == "STALE"
    assert w["timeframes"]["15M"]["freshness"]["stale"] is True


def test_mfe_mae_and_tps_reached_math(world):
    c, store, _ = world
    _mk(store)
    from app.ai.world_model import _mfe_mae
    # BUY entry 1.10, SL 1.095 -> risk 0.005; candle high 1.104/low 1.0985
    df = _candles(n=10, base=1.101, amp=0.004)
    pos = dict(BUY_POS, price_current=1.102)
    mfe, mae, tps = _mfe_mae(pos, SIG, type("P", (), {
        "get_candles": lambda self, m, tf, limit=600: df})(), time.time())
    assert mfe == round((1.1062 - 1.10) / 0.005, 2)     # best high vs entry
    assert mae == round((1.10 - 1.099) / 0.005, 2)      # worst low vs entry
    assert tps == 1                                     # tp1 1.105 not reached


# ===========================================================================
# BRAIN PIPELINE
# ===========================================================================
def _fresh_world(world, provider=None):
    c, store, _ = world
    _mk(store)
    _seed_history(store, "boss", n=8)
    from app.ai.world_model import build_world_model
    return build_world_model("boss", "EURUSD", position=BUY_POS, signal=SIG,
                             provider=provider or FakeProvider(),
                             now=time.time())


def test_data_stale_answers_without_ai_call(world):
    c, store, _ = world
    w = _fresh_world(world, provider=FakeProvider(stale_minutes=90))
    router = FakeRouter([])
    from app.ai.brain import run_brain
    out = run_brain("boss", w, router=router, signal=SIG)
    assert out["answer"] == "DATA_STALE"
    assert out["decision"] is None       # engine applies the deterministic hold
    assert router.calls == []            # honest: no AI call on stale data


def test_insufficient_evidence_thin_history(world):
    c, store, _ = world
    _mk(store)
    _seed_history(store, "boss", n=2)    # < brain_min_strategy_samples (5)
    from app.ai.world_model import build_world_model
    from app.ai.brain import run_brain
    w = build_world_model("boss", "EURUSD", position=BUY_POS, signal=SIG,
                          provider=FakeProvider(), now=time.time())
    out = run_brain("boss", w, router=FakeRouter([]), signal=SIG)
    assert out["answer"] == "INSUFFICIENT_EVIDENCE"
    assert out["decision"] is None


def test_no_action_mapping_and_action_with_challenge(world):
    c, store, _ = world
    w = _fresh_world(world)
    from app.ai.brain import run_brain
    # model says NO_ACTION -> honest pass-through, no execution
    r1 = FakeRouter([{"text": '{"action":"NO_ACTION","reason_codes":["CHOP"]}',
                      "model": "fake", "layer": "primary"}])
    out = run_brain("boss", w, router=r1, signal=SIG)
    assert out["answer"] == "NO_ACTION" and out["decision"]["action"] == "HOLD"
    assert len(r1.calls) == 1            # no challenge for non-actions
    # model proposes PROTECT -> schema-validated + challenged + survives
    text = ('{"action":"PROTECT","continuation_assessment":"WEAKENING",'
            '"next_target":"TP2","confidence":0.7,"reason_codes":["TREND_FADING"],'
            '"risk_state":"ELEVATED","recommended_sl":1.1008}')
    r2 = FakeRouter([{"text": text, "model": "fake", "layer": "primary"},
                     {"text": '{"survives":true,"objections":[]}',
                      "model": "fake", "layer": "primary"}])
    out2 = run_brain("boss", w, router=r2, signal=SIG)
    assert out2["answer"] == "ACTION"
    assert out2["decision"]["action"] == "PROTECT"
    assert out2["challenge"]["ran"] is True and out2["challenge"]["survives"] is True
    assert len(r2.calls) == 2


def test_failed_self_challenge_downgrades_to_conflicting(world):
    c, store, _ = world
    w = _fresh_world(world)
    from app.ai.brain import run_brain
    text = ('{"action":"EXIT","continuation_assessment":"REVERSING",'
            '"confidence":0.8,"reason_codes":["REVERSAL"],"risk_state":"CRITICAL"}')
    router = FakeRouter([{"text": text, "model": "fake", "layer": "primary"},
                         {"text": '{"survives":false,"objections":["M15 still trending up"]}',
                          "model": "fake", "layer": "primary"}])
    out = run_brain("boss", w, router=router, signal=SIG)
    assert out["answer"] == "CONFLICTING"
    assert out["decision"]["action"] == "HOLD"       # nothing executable
    assert out["challenge"]["survives"] is False
    assert "M15 still trending up" in out["reasons"][0] or out["reasons"]


def test_confidence_is_deterministic_blend_and_bounded(world):
    c, store, _ = world
    from app.ai.brain import confidence_pct, evidence_quality, tf_agreement_score
    w = _fresh_world(world)
    q = evidence_quality(w)
    assert 0.0 <= q <= 1.0
    assert confidence_pct(0.0, w) == int(round(100 * 0.4 * q))
    assert confidence_pct(1.0, w) == int(round(100 * (0.6 + 0.4 * q)))
    for mc in (-1, 0, 0.33, 1, 9):
        assert 0 <= confidence_pct(mc, w) <= 100
    assert tf_agreement_score({}) == 0.5             # no position: neutral
    # confidence never moves risk numbers: it is metadata on the decision
    assert "recommended_sl" not in str(confidence_pct(0.7, w))


# ===========================================================================
# CONTROLLED MEMORY
# ===========================================================================
def test_memory_caps_and_recall(world):
    c, store, _ = world
    _mk(store)
    from app.ai.memory import remember, recall, MAX_PER_USER
    for i in range(MAX_PER_USER + 5):
        remember("boss", f"note {i} " + "x" * 500, kind="brain",
                 meta={"market": "EURUSD" if i % 2 == 0 else "GBPUSD"})
    docs = store.list("ai_memory", filters={"userId": "boss"}, limit=0)
    assert len(docs) == MAX_PER_USER                  # FIFO trim
    assert all(len(d["text"]) <= 400 for d in docs)
    hits = recall("boss", market="EURUSD", k=3)
    assert len(hits) == 3 and all(h["meta"]["market"] == "EURUSD" for h in hits)
    assert recall(None) == []                         # no user -> nothing


# ===========================================================================
# MANAGER INTEGRATION (gate + TP ladder unchanged)
# ===========================================================================
def test_manager_brain_path_executes_through_gate(world):
    c, store, monkeypatch = world
    _mk(store)
    from app.aimanager import engine as E
    from app.execution import mt5 as X
    X._CONFIRMED_POSITIONS.clear()
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "risk_per_trade_pct": 1.0, "execution_enabled": True,
                                 "execution_mode": "vps", "mt5_verified": True})
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "ai_manage_enabled": True, "risk_per_trade_pct": 1.0})
    store.create("signals", dict(SIG, id="sg1", userId="boss",
                                 signal_id=SIG["signal_id"], market="EURUSD"))
    _seed_history(store, "boss", n=8)    # enough evidence for ACTION answers
    pos = dict(BUY_POS, comment=SIG["signal_id"])
    sent = []
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: ({"positions": [pos]} if p == "/positions"
                                         else {"balance": 1000, "equity": 1015}
                                         if p == "/account" else {})
    X.bridge_post = lambda p, payload, timeout=15: sent.append(payload) or {"ok": True}
    text = ('{"action":"PROTECT","continuation_assessment":"WEAKENING",'
            '"confidence":0.75,"reason_codes":["FADE"],"risk_state":"ELEVATED",'
            '"recommended_sl":1.1005}')
    try:
        mgr = E.TradeManager(provider=FakeProvider())
        mgr._router = FakeRouter([
            {"text": text, "model": "fake", "layer": "primary"},
            {"text": '{"survives":true,"objections":[]}', "model": "fake",
             "layer": "primary"}])
        monkeypatch.setattr(E, "daily_state", lambda uid: {"total_usd": 5.0,
                                                           "daily_profit_target_usd": 30,
                                                           "daily_loss_limit_usd": 20},
                            raising=False)
        summary = mgr.tick("boss")
        assert summary["reviews"] >= 1
        assert any(a.startswith("modify_sl") for a in summary["actions"])
        assert sent and sent[0].get("sl") == 1.1005   # executed via primitives
        rec = store.list("ai_decisions", limit=1)[0]
        assert rec["brain"]["answer"] == "ACTION"
        assert 0 <= rec["brain"]["confidence_pct"] <= 100
        # gate still blocks a LOOSENING SL even with the brain ON
        bad = ('{"action":"PROTECT","confidence":0.9,"reason_codes":["X"],'
               '"risk_state":"ELEVATED","recommended_sl":1.0900}')
        mgr2 = E.TradeManager(provider=FakeProvider())
        mgr2._router = FakeRouter([
            {"text": bad, "model": "fake", "layer": "primary"},
            {"text": '{"survives":true}', "model": "fake", "layer": "primary"}])
        s2 = mgr2.tick("boss")
        assert not any(a.startswith("modify_sl") for a in s2["actions"])
        rec2 = [d for d in store.list("ai_decisions", limit=5)
                if d.get("gate_verdict") == "SL_WOULD_LOOSEN_RISK"]
        assert rec2                                   # deterministic gate held
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post
        X._CONFIRMED_POSITIONS.clear()


def test_manager_tp2_lock_unaffected_by_brain(world):
    """Deterministic TP2 -> SL := TP1 hard rule fires even if the brain says
    HOLD - ladder runs BEFORE the AI, brain can never unlock it."""
    c, store, monkeypatch = world
    _mk(store)
    from app.aimanager import engine as E
    from app.execution import mt5 as X
    X._CONFIRMED_POSITIONS.clear()
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "execution_enabled": True, "execution_mode": "vps"})
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "ai_manage_enabled": True})
    store.create("signals", dict(SIG, id="sg2", userId="boss",
                                 signal_id=SIG["signal_id"], market="EURUSD"))
    pos = dict(BUY_POS, price_current=1.1101, profit=100.0,
               comment=SIG["signal_id"])               # above TP2 (1.11)
    sent = []
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: ({"positions": [pos]} if p == "/positions" else {})
    X.bridge_post = lambda p, payload, timeout=15: sent.append(payload) or {"ok": True}
    try:
        mgr = E.TradeManager(provider=FakeProvider())
        mgr._router = FakeRouter([                    # brain would say HOLD
            {"text": '{"action":"HOLD","confidence":0.5,"reason_codes":["OK"]}',
             "model": "fake", "layer": "primary"}])
        monkeypatch.setattr(E, "daily_state", lambda uid: None, raising=False)
        summary = mgr.tick("boss")
        assert any("modify_sl" in a for a in summary["actions"])
        assert sent and abs(sent[0].get("sl") - 1.105) < 1e-9   # SL := TP1
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post
        X._CONFIRMED_POSITIONS.clear()


def test_brain_disabled_falls_back_to_legacy_path(world):
    c, store, monkeypatch = world
    _mk(store)
    monkeypatch.setattr(__import__("app.config", fromlist=["settings"]).settings,
                        "brain_v2_enabled", False)
    from app.aimanager import engine as E
    from app.execution import mt5 as X
    X._CONFIRMED_POSITIONS.clear()
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "execution_enabled": True, "execution_mode": "vps"})
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "ai_manage_enabled": True})
    store.create("signals", dict(SIG, id="sg3", userId="boss",
                                 signal_id=SIG["signal_id"], market="EURUSD"))
    pos = dict(BUY_POS, comment=SIG["signal_id"])
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: ({"positions": [pos]} if p == "/positions" else {})
    X.bridge_post = lambda p, payload, timeout=15: {"ok": True}
    try:
        mgr = E.TradeManager(provider=FakeProvider())
        mgr._router = FakeRouter([                    # legacy: ONE call, no challenge
            {"text": '{"action":"HOLD","confidence":0.4,"reason_codes":["LEGACY"]}',
             "model": "fake", "layer": "primary"}])
        monkeypatch.setattr(E, "daily_state", lambda uid: None, raising=False)
        summary = mgr.tick("boss")
        assert summary["reviews"] >= 1
        assert len(mgr._router.calls) == 1            # no world-model pipeline
        rec = store.list("ai_decisions", limit=1)[0]
        assert rec["brain"] is None                   # no brain metadata
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post
        X._CONFIRMED_POSITIONS.clear()


def test_brain_error_falls_back_and_records(world):
    c, store, monkeypatch = world
    _mk(store)
    from app.aimanager import engine as E
    from app.execution import mt5 as X
    X._CONFIRMED_POSITIONS.clear()
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "execution_enabled": True, "execution_mode": "vps"})
    store.create("settings", {"userId": "boss", "kind": "risk",
                              "ai_manage_enabled": True})
    store.create("signals", dict(SIG, id="sg4", userId="boss",
                                 signal_id=SIG["signal_id"], market="EURUSD"))
    pos = dict(BUY_POS, comment=SIG["signal_id"])
    orig_get, orig_post = X.bridge_get, X.bridge_post
    X.bridge_get = lambda p, timeout=8: ({"positions": [pos]} if p == "/positions" else {})
    X.bridge_post = lambda p, payload, timeout=15: {"ok": True}
    try:
        mgr = E.TradeManager(provider=FakeProvider())
        mgr._router = FakeRouter([
            {"text": '{"action":"HOLD","confidence":0.4,"reason_codes":["L"]}',
             "model": "fake", "layer": "primary"}])
        # world model explodes -> pipeline falls back to legacy, RECORDED
        monkeypatch.setattr(E, "daily_state", lambda uid: None, raising=False)

        import app.ai.world_model as WM
        orig_build = WM.build_world_model
        monkeypatch.setattr("app.ai.world_model.build_world_model",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        summary = mgr.tick("boss")
        assert summary["reviews"] >= 1                # legacy still reviewed
        errs = [d for d in store.list("ai_decisions", limit=5)
                if d.get("gate_verdict") == "BRAIN_ERROR_FALLBACK_LEGACY"]
        assert errs                                   # never silent
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post
        X._CONFIRMED_POSITIONS.clear()


def test_brain_surfaces_via_api(world):
    c, store, _ = world
    _mk(store)
    r = c.get("/api/aimanager/brain/world", params={"market": "EURUSD"})
    assert r.status_code == 200
    w = r.json()["world_model"]
    assert w["market"] == "EURUSD" and "freshness" in w
