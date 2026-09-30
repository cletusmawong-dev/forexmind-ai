"""Stage 4: regime-change events (P5), S×P×Session×Regime matrix +
[REVIEW][APPLY][REJECT] recommendations (P6), per-strategy sessions (P7),
automated research loop with walk-forward (P8), risk guards (P10).

Hard rules under test:
 - regime transitions emit events + owner notification (deduped)
 - recommendations NEVER auto-apply: APPLY drafts sessions only; activation
   is an explicit audited strategy PATCH; regime recs honestly refuse APPLY
 - per-strategy session gate blocks detection outside allowed sessions
 - experiments carry walk-forward consistency; auto loop runs at most ONE
   experiment per cycle and files a REVIEW recommendation
 - risk guards refuse entries (news/spread/vol/correlation) LOUDLY with
   SKIPPED_RISK_* + ledger + notification, and never fabricate data
"""
import os
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch, tmp_path):
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
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c, store, monkeypatch, settings
    finally:
        State.store = _prev
        State.ready = False
        from app.learning import regime as _regime
        _regime.clear_cache()          # never leak classifications across files
        app.dependency_overrides.pop(get_user_id, None)


def _user(store, uid="boss"):
    store.create("users", {"id": uid, "userId": uid, "email": f"{uid}@x.io",
                           "role": "admin", "status": "active"})


# ===========================================================================
# P5 REGIME-CHANGE EVENTS
# ===========================================================================
def test_regime_change_emits_event_and_notifies(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    import app.notifications.service as NS
    sent = []
    monkeypatch.setattr(NS, "notify", lambda uid, t, ti, b, **kw: sent.append(t))
    from app.learning import regime
    import pandas as pd
    idx = pd.to_datetime([time.time() - (60 - i) * 900 for i in range(60)], unit="s")
    df = pd.DataFrame({"open": 1.1, "high": 1.102, "low": 1.098, "close": 1.101},
                      index=idx.tz_localize(None))
    r1 = regime.record("EURUSD", df)            # first classification
    assert store.list("regime_events", limit=10) == []   # no transition yet
    # force a different classification -> transition
    df2 = df.copy()
    df2["close"] = 1.2                          # strong trend inputs
    df2["high"] = 1.201
    df2["low"] = 1.199
    r2 = regime.record("EURUSD", df2)
    if r2["regime"] != r1["regime"]:            # synthetic data may agree; force it
        evs = store.list("regime_events", limit=10)
        assert len(evs) == 1 and evs[0]["from"] == r1["regime"]
        assert evs[0]["to"] == r2["regime"]
        assert "REGIME_CHANGE" in sent
        # dedupe: same transition again within 30 min -> no new event
        regime.record("EURUSD", df)
        df3 = df2.copy(); df3["close"] = 1.1; df3["high"] = 1.102; df3["low"] = 1.098
        regime.record("EURUSD", df3)            # back
        df4 = df2.copy()
        regime.record("EURUSD", df4)            # and forth again
        evs2 = store.list("regime_events", limit=10)
        assert len(evs2) == len(evs)            # deduped (same来回 within window)
    else:
        # deterministic fallback: directly verify _emit_change behavior
        doc = regime._emit_change("EURUSD", {"regime": "RANGING"},
                                  {"regime": "TRENDING_BULLISH", "confidence": 80,
                                   "trend_score": 70, "volatility": "NORMAL"})
        assert doc and "REGIME_CHANGE" in sent
        assert regime._emit_change("EURUSD", {"regime": "RANGING"},
                                   {"regime": "TRENDING_BULLISH", "confidence": 80,
                                    "trend_score": 70, "volatility": "NORMAL"}) is None


# ===========================================================================
# P6 MATRIX + RECOMMENDATIONS
# ===========================================================================
def _seed_signals(store, weak_session="Asian"):
    """Strategy strong overall (80%) but terrible in one session (10%)."""
    for i in range(20):
        store.create("signals", {
            "userId": "boss", "strategy_id": "strat1", "strategy_name": "S1",
            "market": "EURUSD", "completed": True, "outcome": "WIN",
            "r_multiple": 1.5, "candle_time": "2026-09-20T10:00:00",
            "market_conditions": {"session": "London"},
            "signal_id": f"W{i}"})
    for i in range(10):
        store.create("signals", {
            "userId": "boss", "strategy_id": "strat1", "strategy_name": "S1",
            "market": "EURUSD", "completed": True, "outcome": "LOSS",
            "r_multiple": -1.0, "candle_time": "2026-09-21T02:00:00",
            "market_conditions": {"session": "Asian"},
            "signal_id": f"L{i}"})


def test_matrix_cells_and_recommendation_lifecycle(env):
    c, store, monkeypatch, settings = env
    _user(store)
    _seed_signals(store)
    r = c.get("/api/learning/matrix")
    assert r.status_code == 200
    mx = r.json()
    assert mx["total_completed"] == 30
    sessions = {cell["session"] for cell in mx["cells"]}
    assert any(cell["n"] == 20 for cell in mx["cells"])
    # generate -> weak session cell becomes a FILTER_SESSION recommendation
    r2 = c.post("/api/learning/recommendations/generate")
    assert r2.status_code == 200
    recs = r2.json()["recommendations"]
    assert recs and recs[0]["type"] == "FILTER_SESSION"
    rec = recs[0]
    assert rec["status"] == "REVIEW" and rec["never_auto_applied"] is True
    # dedupe: generating again creates nothing new
    r3 = c.post("/api/learning/recommendations/generate")
    assert r3.json()["created"] == 0
    # REVIEW -> APPLY drafts sessions on the strategy doc (needs a strategy doc)
    store.create("strategies", {"id": "strat1", "status": "ACTIVE",
                                "sessions": ["Asian", "London", "NewYork", "Late"]})
    assert c.post(f"/api/learning/recommendations/{rec['id']}/review").json()[
        "recommendation"]["status"] == "REVIEWING"
    ap = c.post(f"/api/learning/recommendations/{rec['id']}/apply").json()
    assert ap["recommendation"]["status"] == "APPLYING"
    assert "Asian" not in ap["recommendation"]["applied_draft"]["proposed_sessions"]
    sdoc = store.list("strategies", filters={"id": "strat1"}, limit=1)[0]
    assert sdoc["proposed_sessions"] == ["London", "NewYork", "Late"]
    # the strategy is NOT yet filtered - activation is an explicit audited PATCH
    assert sdoc["sessions"] == ["Asian", "London", "NewYork", "Late"]
    r4 = c.patch("/api/strategies/strat1",
                 json={"sessions": sdoc["proposed_sessions"]})
    assert r4.status_code == 200
    assert store.list("strategies", filters={"id": "strat1"}, limit=1)[0][
        "sessions"] == ["London", "NewYork", "Late"]
    # audit trail has both actions
    acts = [a["action"] for a in store.list("audit_log", limit=10)]
    assert "recommendation.reviewing" in acts and "strategy.update" in acts
    # REJECT lifecycle on a second rec
    store.create("signals", {
        "userId": "boss", "strategy_id": "strat1", "strategy_name": "S1",
        "market": "GBPUSD", "completed": True, "outcome": "LOSS",
        "r_multiple": -1.0, "candle_time": "2026-09-21T02:00:00",
        "market_conditions": {"session": "Asian"},
        "signal_id": "X1"})
    r5 = c.post("/api/learning/recommendations/generate")
    rec2 = (r5.json()["recommendations"] or [None])[0]
    if rec2:
        rr = c.post(f"/api/learning/recommendations/{rec2['id']}/reject",
                    json={"reason": "not convinced"})
        assert rr.json()["recommendation"]["status"] == "REJECTED"


def test_regime_recommendation_apply_refused(env):
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("recommendations", {
        "userId": "boss", "type": "REGIME_NOTE", "status": "REVIEW",
        "strategy_id": "strat1", "market": "EURUSD", "session": None,
        "regime": "HIGH_VOLATILITY", "claim": "x", "dedupe_key": "k1"})
    r = c.post("/api/learning/recommendations/nonexist/apply")
    assert r.status_code == 404
    rid = store.list("recommendations", limit=1)[0]["id"]
    r2 = c.post(f"/api/learning/recommendations/{rid}/apply")
    assert r2.status_code == 422
    assert store.list("recommendations", limit=1)[0]["status"] == "REVIEW"


# ===========================================================================
# P7 PER-STRATEGY SESSIONS
# ===========================================================================
def test_strategy_session_gate(env):
    c, store, monkeypatch, settings = env
    from app.engine.signal_engine import strategy_sessions_allowed
    assert strategy_sessions_allowed({}, "Asian") is True          # no override
    assert strategy_sessions_allowed({"sessions": ["London"]}, "London")
    assert not strategy_sessions_allowed({"sessions": ["London"]}, "Asian")
    assert not strategy_sessions_allowed({"sessions": ["London"]}, "")


def test_strategy_patch_validates_sessions(env):
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("strategies", {"id": "strat1", "status": "ACTIVE"})
    bad = c.patch("/api/strategies/strat1", json={"sessions": ["Martian"]})
    assert bad.status_code == 422
    empty = c.patch("/api/strategies/strat1", json={"sessions": []})
    assert empty.status_code == 422
    ok = c.patch("/api/strategies/strat1",
                 json={"status": "PAUSED", "sessions": ["London", "NewYork"]})
    assert ok.status_code == 200
    doc = store.list("strategies", filters={"id": "strat1"}, limit=1)[0]
    assert doc["status"] == "PAUSED" and doc["sessions"] == ["London", "NewYork"]
    nothing = c.patch("/api/strategies/strat1", json={})
    assert nothing.status_code == 422


# ===========================================================================
# P8 WALK-FORWARD + AUTO LOOP
# ===========================================================================
def test_walk_forward_consistency(env):
    from app.learning.experiments import walk_forward
    base, exp = [], []
    for i in range(30):
        base.append({"entry_time": f"2026-01-{i % 28 + 1:02d}T{i:02d}:00",
                     "expectancy": 0.2})
        # wins in window 1&2, loses in window 3 -> inconsistent overall? wins 2/3
        e = 0.5 if i < 20 else -0.5
        exp.append({"entry_time": f"2026-01-{i % 28 + 1:02d}T{i:02d}:30",
                    "expectancy": e})
    wf = walk_forward(base, exp, folds=3, min_per_fold=3)
    assert len(wf["folds"]) == 3 and wf["consistent"] is not None
    thin = walk_forward(base[:5], exp[:5])
    assert thin["consistent"] is None and "insufficient" in thin["note"]


def test_auto_loop_one_experiment_and_review_rec(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("hypotheses", {"userId": "boss", "strategy_id": "strat1",
                                "strategy_name": "S1", "hypothesis_id": "HYP-001",
                                "variable": "fast_len", "old_value": 9,
                                "new_value": 12, "status": "PROPOSED"})
    from app.learning import auto_loop
    fake_exp = {"id": "exp1", "experiment_code": "EXP-000001",
                "result": "IMPROVED"}

    class FakeEngine:
        def run_from_hypothesis(self, uid, hyp):
            self.hyp = hyp
            doc = store.create("experiments", {
                "userId": uid, "hypothesis_id": hyp["id"],
                "experiment_code": "EXP-000001", "result": "IMPROVED"})
            store.update("hypotheses", hyp["id"],
                         {"status": "AWAITING_APPROVAL",
                          "experiment_id": doc["id"]})
            return dict(fake_exp, id=doc["id"])

    fe = FakeEngine()
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "brain_v2_enabled", True)
    import app.state as state_mod
    monkeypatch.setattr(state_mod.State, "experiments", fe, raising=False)
    import app.notifications.service as NS
    sent = []
    monkeypatch.setattr(NS, "notify", lambda uid, t, ti, b, **kw: sent.append(t))
    out = auto_loop.run_research_cycle("boss")
    assert out["experiment"] and out["experiment"]["result"] == "IMPROVED"
    assert out["recommended"] == 1
    recs = store.list("recommendations", filters={"userId": "boss"}, limit=5)
    assert recs and recs[0]["status"] == "REVIEW"
    assert recs[0]["type"] == "EXPERIMENT_REVIEW"
    assert "RESEARCH_REVIEW" in sent
    # cycle 2: same hypothesis already tested -> no second experiment
    out2 = auto_loop.run_research_cycle("boss")
    assert out2["experiment"] is None
    assert len(store.list("recommendations", filters={"userId": "boss"},
                          limit=5)) == 1


# ===========================================================================
# P10 RISK GUARDS
# ===========================================================================
def _exec_env(store, monkeypatch):
    from app.execution import mt5 as X
    store.create("agent_goals", {"userId": "boss", "account_balance": 1000,
                                 "execution_enabled": True, "execution_mode": "vps"})
    sig = {"id": "sg1", "userId": "boss", "signal_id": "SIG-G1", "market": "EURUSD",
           "direction": "BUY", "entry": 1.10, "sl": 1.095, "tp1": 1.105,
           "tp2": 1.11, "tp3": 1.115}
    store.create("signals", dict(sig))
    return X, store.get("signals", "sg1")


def test_risk_guards_block_entries_loudly(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    X, sig = _exec_env(store, monkeypatch)
    import app.notifications.service as NS
    sent = []
    monkeypatch.setattr(NS, "notify", lambda uid, t, ti, b, **kw: sent.append(t))
    monkeypatch.setattr("app.execution.mt5.notify",
                        lambda uid, t, ti, b, **kw: sent.append(t))
    # SPREAD guard: wide spread on the bridge
    orig_get, orig_post = X.bridge_get, X.bridge_post
    monkeypatch.setattr(settings, "risk_max_spread_pips", 2.0)
    X.bridge_get = lambda p, timeout=8: (
        {"rates": {"EURUSD": {"spread": 0.0004}}} if p == "/rates"
        else {"positions": []} if p == "/positions" else {"balance": 1000})
    X.bridge_post = lambda p, payload, timeout=15: (_ for _ in ()).throw(
        AssertionError("order must NOT be sent"))
    try:
        from app.risk_checks import check_entry
        ok, guard, why = check_entry("boss", "EURUSD")
        assert not ok and guard == "SPREAD" and "cap" in why
        # full execute path refuses
        X.execute_signal(sig, "boss")
        doc = store.get("signals", "sg1")
        assert doc["execution_status"] == "SKIPPED_RISK_SPREAD"
        assert "EXECUTION_SKIPPED" in sent
        evs = [e for e in store.list("exec_events", filters={"userId": "boss"},
                                     limit=10) if e["kind"] == "ENTRY"]
        assert evs and evs[0]["stage"] == "SKIPPED" and "SPREAD" in evs[0]["detail"]
    finally:
        X.bridge_get, X.bridge_post = orig_get, orig_post


def test_correlation_guard_counts_open_positions(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    monkeypatch.setattr(settings, "risk_max_correlated", 1)
    from app.execution import mt5 as X
    from app.risk_checks import check_entry
    orig_get = X.bridge_get
    X.bridge_get = lambda p, timeout=8: (
        {"positions": [{"ticket": 1, "magic": X.MAGIC, "symbol": "GBPUSD",
                        "profit": 2.0}]} if p == "/positions" else {})
    try:
        ok, guard, why = check_entry("boss", "EURUSD")   # shares USD root
        assert not ok and guard == "CORRELATION"
        ok2, _, _ = check_entry("boss", "XAUUSD")        # XAU/USD also shares USD
        assert not ok2
    finally:
        X.bridge_get = orig_get


def test_news_and_vol_guards(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    import app.market_data.calendar as CAL
    monkeypatch.setattr(CAL, "is_blackout",
                        lambda m: (True, {"title": "NFP"}))
    import app.learning.regime as regime
    monkeypatch.setattr(regime, "current",
                        lambda m: {"volatility_rank": 0.99, "regime": "HIGH_VOLATILITY"})
    from app.risk_checks import check_entry
    ok, guard, why = check_entry("boss", "EURUSD")
    assert not ok and guard == "NEWS" and "NFP" in why
    monkeypatch.setattr(CAL, "is_blackout", lambda m: (False, None))
    ok2, guard2, why2 = check_entry("boss", "EURUSD")
    assert not ok2 and guard2 == "VOL" and "0.99" in why2
    # master switch disables everything
    monkeypatch.setattr(settings, "risk_guards_enabled", False)
    ok3, _, _ = check_entry("boss", "EURUSD")
    assert ok3
    monkeypatch.setattr(settings, "risk_guards_enabled", True)
    # guards pass cleanly when data is fine
    monkeypatch.setattr(regime, "current",
                        lambda m: {"volatility_rank": 0.5, "regime": "RANGING"})
    ok4, _, _ = check_entry("boss", "EURUSD")
    assert ok4


# ===========================================================================
# P6b: AI SESSION ADVISOR (best-session suggestions from previous data)
# ===========================================================================
def _seed_session_data(store):
    """London strong (90%), NewYork ok (70%) -> baseline 80% -> suggest London."""
    for i in range(10):
        store.create("signals", {"userId": "boss", "strategy_id": "strat9",
                                 "strategy_name": "S9", "market": "EURUSD",
                                 "completed": True, "outcome": "WIN",
                                 "r_multiple": 1.5, "n": i,
                                 "market_conditions": {"session": "London"},
                                 "candle_time": f"2026-09-20T10:{i:02d}:00",
                                 "signal_id": f"L{i}"})
    for i in range(10):
        store.create("signals", {"userId": "boss", "strategy_id": "strat9",
                                 "strategy_name": "S9", "market": "EURUSD",
                                 "completed": True,
                                 "outcome": "WIN" if i < 7 else "LOSS",
                                 "r_multiple": 0.8 if i < 7 else -0.8,
                                 "market_conditions": {"session": "NewYork"},
                                 "candle_time": f"2026-09-20T15:{i:02d}:00",
                                 "signal_id": f"N{i}"})


def test_session_advisor_suggests_best_sessions(env):
    c, store, monkeypatch, settings = env
    _user(store)
    _seed_session_data(store)
    r = c.post("/api/learning/recommendations/generate")
    assert r.status_code == 200
    recs = [x for x in r.json()["recommendations"]
            if x["type"] == "SESSION_SUGGESTION"]
    assert recs, "advisor should suggest the best session set"
    rec = recs[0]
    assert rec["proposed_sessions"] == ["London"]   # 100% >= 85% baseline; NY 70% < 85%
    assert rec["status"] == "REVIEW"
    assert rec["never_auto_applied"] is True
    assert "London 100.0% WR" in rec["claim"]
    assert "NewYork 70.0% WR" in rec["claim"]
    # dedupe
    r2 = c.post("/api/learning/recommendations/generate")
    assert not [x for x in r2.json()["recommendations"]
                if x["type"] == "SESSION_SUGGESTION"]
    # APPLY drafts the proposed set on the strategy doc (never auto-activates)
    store.create("strategies", {"id": "strat9", "status": "ACTIVE"})
    ap = c.post(f"/api/learning/recommendations/{rec['id']}/apply")
    assert ap.status_code == 200
    assert ap.json()["recommendation"]["applied_draft"]["proposed_sessions"] == ["London"]
    sdoc = store.list("strategies", filters={"id": "strat9"}, limit=1)[0]
    assert sdoc["proposed_sessions"] == ["London"]
    assert sdoc.get("sessions") in (None, ["Asian", "London", "NewYork", "Late"])  # NOT activated
    # explicit audited PATCH activates
    assert c.patch("/api/strategies/strat9", json={"sessions": ["London"]}).status_code == 200
    assert store.list("strategies", filters={"id": "strat9"}, limit=1)[0]["sessions"] == ["London"]


def test_session_advisor_skips_when_already_optimal(env):
    c, store, monkeypatch, settings = env
    _user(store)
    _seed_session_data(store)
    store.create("strategies", {"id": "strat9", "status": "ACTIVE",
                                "sessions": ["London"]})
    r = c.post("/api/learning/recommendations/generate")
    sug = [x for x in r.json()["recommendations"] if x["type"] == "SESSION_SUGGESTION"]
    assert sug == []          # already trading the best set -> nothing to suggest


# ===========================================================================
# DUPLICATE-PLACEMENT INCIDENT FIXES (2026-09-29: 3x USDJPY BUY stacked)
# ===========================================================================
def test_stacking_guard_blocks_same_market_same_direction(env, monkeypatch):
    """Second USDJPY BUY while one is open MUST be blocked - this exact
    sequence triple-stacked on 2026-09-29."""
    c, store, monkeypatch, settings = env
    _user(store)
    from app.execution import mt5 as X
    from app.risk_checks import check_entry
    monkeypatch.setattr(settings, "risk_max_correlated", 2)
    orig = X.bridge_get
    X.bridge_get = lambda p, timeout=8: (
        {"positions": [{"ticket": 1, "magic": X.MAGIC, "symbol": "USDJPYm",
                        "type": "BUY", "profit": -1.0}]} if p == "/positions" else {})
    try:
        ok, guard, why = check_entry("boss", "USDJPY", direction="BUY")
        assert not ok and guard == "STACKING" and "no stacking" in why
        # hedge (opposite direction) is not stacking - left to correlation cap
        ok2, _, _ = check_entry("boss", "USDJPY", direction="SELL")
        assert ok2
        # different market is fine
        ok3, _, _ = check_entry("boss", "EURUSD", direction="BUY")
        assert ok3
    finally:
        X.bridge_get = orig


def test_symbol_suffix_normalization_in_guards(env, monkeypatch):
    """EURUSDm open must correlate with a new EURUSD (suffix-blind before)."""
    c, store, monkeypatch, settings = env
    _user(store)
    from app.execution import mt5 as X
    from app.risk_checks import check_entry, _norm_symbol, _roots
    assert _norm_symbol("USDJPYm") == "USDJPY"
    assert _roots("XAUUSDm") == {"XAU", "USD"}     # metals split like fx pairs
    monkeypatch.setattr(settings, "risk_max_correlated", 2)
    orig = X.bridge_get
    X.bridge_get = lambda p, timeout=8: (
        {"positions": [{"ticket": 2, "magic": X.MAGIC, "symbol": "EURUSDm",
                        "type": "SELL", "profit": 0.5}]} if p == "/positions" else {})
    try:
        ok, guard, why = check_entry("boss", "EURUSD", direction="SELL")
        assert not ok and guard == "STACKING"          # same instrument+direction
        # drop the cap: GBPUSD now correlates with the open EURUSDm via USD
        monkeypatch.setattr(settings, "risk_max_correlated", 1)
        ok2, guard2, _ = check_entry("boss", "GBPUSD", direction="BUY")
        assert not ok2 and guard2 == "CORRELATION"     # shares USD via suffix-normalized root
    finally:
        X.bridge_get = orig


def test_position_guards_fail_closed_on_bridge_error(env, monkeypatch):
    """Bridge/DB read failure MUST block the entry (the old fail-open allowed
    unlimited duplicates when reads erred)."""
    c, store, monkeypatch, settings = env
    _user(store)
    from app.execution import mt5 as X
    from app.risk_checks import check_entry
    orig = X.bridge_get
    def dead(path, timeout=8, **kw):
        raise ConnectionError("bridge down")
    X.bridge_get = dead
    try:
        ok, guard, why = check_entry("boss", "USDJPY", direction="BUY")
        assert not ok and guard == "CORRELATION" and "fail-closed" in why
    finally:
        X.bridge_get = orig
    # DB-side failure (user_mode raises) -> also fail-closed
    X.bridge_get = orig
    from app.execution import mt5 as X2
    orig_um = X2.user_mode
    def boom(uid):
        raise RuntimeError("db quota")
    X2.user_mode = boom
    try:
        ok, guard, why = check_entry("boss", "USDJPY", direction="BUY")
        assert not ok and "fail-closed" in why
    finally:
        X2.user_mode = orig_um


def test_execute_signal_stacking_refusal_is_loud(env, monkeypatch):
    """Full executor path: a stacking attempt is refused loudly, no order."""
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("agent_goals", {"userId": "boss", "execution_enabled": True,
                                 "execution_mode": "vps"})
    from app.execution import mt5 as X
    orig = X.bridge_get
    posts = []
    X.bridge_get = lambda p, timeout=8: (
        {"positions": [{"ticket": 9, "magic": X.MAGIC, "symbol": "USDJPYm",
                        "type": "BUY", "profit": 0.0}]} if p == "/positions" else {})
    X.bridge_post = lambda p, j=None, timeout=30: posts.append(p) or {}
    try:
        store.create("signals", {"id": "sg-stack", "userId": "boss",
                                 "signal_id": "STACK-1", "market": "USDJPY"})
        sig = {"id": "sg-stack", "signal_id": "STACK-1", "market": "USDJPY",
               "direction": "BUY", "strategy_id": "strategy_2_ema_atr",
               "entry": 157.4, "sl": 157.2, "tp1": 157.8, "tp2": 158.0,
               "tp3": 158.4, "timeframe": "15M", "lot": 0.01}
        X.execute_signal(sig, "boss")
        doc = store.get("signals", "sg-stack")
        assert doc["execution_status"] == "SKIPPED_RISK_STACKING"
        assert posts == []                              # nothing sent to broker
        evs = [e for e in store.list("exec_events", limit=10) if e["kind"] == "ENTRY"]
        assert evs and evs[0]["stage"] == "SKIPPED" and "STACKING" in evs[0]["detail"]
    finally:
        X.bridge_get, X.bridge_post = orig, None
