"""FOREXMIND 3.0 stages 2/3/4/5/8: intelligence + research lab tests.

Debate (multi-role + critic, epistemic labels), market fingerprint,
performance DNA, memory lifecycle, hypothesis enrichment, TCA/broker
intelligence, exposure + adaptive-risk clamps, Monte Carlo, stress lab,
calibration honesty, knowledge graph, NL research (whitelist-validated),
multiple-testing summary.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch, tmp_path):
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
        app.dependency_overrides.pop(get_user_id, None)


class FakeRouter:
    def __init__(self):
        self.calls = []

    def analyze(self, prompt, context, escalate=False, user_id=None):
        self.calls.append(prompt[:40])
        if "CRITIC" in prompt:
            return {"text": json.dumps({
                "agreements": ["sample is thin"], "contradictions": ["recent WR fell"],
                "unknowns": ["live regime"], "would_change_conclusion_if": ["20 more trades"],
                "synthesis": "wait for evidence"}), "model": "fake", "layer": "primary"}
        if "filters" in prompt and "Translate" in prompt:
            return {"text": json.dumps({"market": "XAUUSD", "outcome": "LOSS",
                                        "evil_key": "drop-table"}),
                    "model": "fake", "layer": "primary"}
        if "incident commander" in prompt:
            return {"text": json.dumps({
                "probable_cause": "bridge process down", "evidence": ["no /account response"],
                "affected": ["execution"], "recommended_action": "restart bridge",
                "safe": True}), "model": "fake", "layer": "primary"}
        role = (context or {}).get("role", "ANALYST")
        return {"text": json.dumps({"position": "CONTRADICT" if "RISK" in role
                                    else "SUPPORT", "points": ["p1", "p2"]}),
                "model": "fake", "layer": "primary"}


def _user(store, uid="boss"):
    store.create("users", {"id": uid, "userId": uid, "email": f"{uid}@x.io",
                           "role": "admin", "status": "active"})


def _sig(store, i, outcome="WIN", market="EURUSD", sid="strategy_2_ema_atr",
         session="London", regime="TRENDING", r=1.0, status=None, when=None):
    t = when or (datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc) + timedelta(days=i % 20,
                                                                             minutes=i))
    doc = {"userId": "boss", "strategy_id": sid, "strategy_name": "EMA",
           "market": market, "completed": status is None, "outcome": outcome,
           "r_multiple": r, "tp_hits": 2, "mt5_confirmed": True,
           "dna": {"regime": regime, "session": session},
           "market_conditions": {"session": session, "regime": regime},
           "candle_time": t.isoformat(), "completed_at": t.isoformat(),
           "signal_id": f"S-{market}-{i}"}
    if status:
        doc["completed"] = False
        doc["status"] = status
        doc["execution_status"] = status
        doc["outcome"] = None
        doc["r_multiple"] = None
    store.create("signals", doc)


# ===========================================================================
# Stage 2: AI debate
# ===========================================================================
def test_debate_structure_epistemic_and_persistence(env):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.ai.debate import run_debate
    doc = run_debate("boss", "strategy_market", "strategy_2_ema_atr:EURUSD",
                     "Does S2 still work on EURUSD?", {"evidence": {"state": "PRELIMINARY"}},
                     router=FakeRouter(), store=store)
    assert doc and len(doc["arguments"]) == 5            # five analysts
    assert all(a["role"] for a in doc["arguments"])
    assert doc["critic"]["synthesis"] == "wait for evidence"
    assert "INTERPRETATION" in doc["epistemic"]          # never presented as fact
    assert doc["evidence_view"]["state"] == "PRELIMINARY"  # evidence joined before critic
    stored = store.list("debates", filters={"userId": "boss"})
    assert len(stored) == 1                              # traceable


def test_debate_route_requires_auth_shape(env):
    c, store, monkeypatch, settings = env
    _user(store)
    r = c.post("/api/ai/debate", json={"subject_type": "strategy_market",
                                       "subject_id": "strategy_2_ema_atr:EURUSD",
                                       "question": "q"})
    assert r.status_code in (200, 503)                   # router-dependent, never crashes


# ===========================================================================
# Stage 3: fingerprint + performance DNA + memory lifecycle + hypotheses
# ===========================================================================
def test_fingerprint_from_world_model(env):
    c, store, monkeypatch, settings = env
    from app.ai.fingerprint import build_fingerprint
    world = {"market": "EURUSD",
             "timeframes": {"15M": {"close": 1.5, "ema9": 1.4, "ema21": 1.3,
                                    "atr14": 0.002, "structure": "BULLISH"},
                            "H4": {"close": 1.5, "ema9": 1.35, "ema21": 1.3},
                            "D1": {"close": 1.5, "ema9": 1.4, "ema21": 1.3}},
             "freshness": {"verdict": "OK"},
             "spread": {"value": 0.00012},
             "regime": {"label": "TRENDING"}}
    fp = build_fingerprint(world, now=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc))
    assert fp["trend"] == "BULLISH"
    assert fp["structure_m15"] == "BULLISH"
    assert fp["session"] == "London"
    assert fp["regime"] == "TRENDING"
    assert fp["timeframe_alignment"] == "ALIGNED"
    assert fp["news_risk"] in ("LOW", "MEDIUM", "HIGH")
    # missing inputs stay None - never guessed
    fp2 = build_fingerprint({"market": "EURUSD", "timeframes": {}})
    assert fp2["trend"] is None and fp2["structure_h4"] is None


def test_performance_dna_derived_from_matrix_only(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(10):
        _sig(store, i, session="London", market="EURUSD", outcome="WIN", r=1.2)
        _sig(store, 100 + i, session="Asian", market="EURUSD", outcome="LOSS", r=-0.8)
    from app.learning.perf_dna import build_dna
    dna = build_dna("boss", "strategy_2_ema_atr")
    assert dna["sufficient"] is True
    assert dna["strengths"]["sessions"][0]["name"] == "London"
    assert dna["weaknesses"]["sessions"][0]["name"] == "Asian"
    assert dna["detail"]["by_session"][0]["n"] == 10     # min segment n respected


def test_memory_lifecycle_states_and_authority(env):
    c, store, monkeypatch, settings = env
    from app.ai import memory
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    store.create(memory.COLL, {"userId": "boss", "kind": "global",
                               "text": "old belief", "meta": {},
                               "createdAt": old})
    d2 = store.create(memory.COLL, {"userId": "boss", "kind": "global",
                                    "text": "contradicted belief",
                                    "meta": {"contradicted": True},
                                    "createdAt": datetime.now(timezone.utc).isoformat()})
    mems = memory.recall("boss")
    assert len(mems) == 2
    old_m = next(m for m in mems if m["text"] == "old belief")
    bad_m = next(m for m in mems if m["text"] == "contradicted belief")
    assert old_m["state"] == "STALE" and old_m["authority"] is False
    assert bad_m["state"] == "CONTRADICTED" and bad_m["authority"] is False
    # terminal states are sticky by design (CONTRADICTED stays until resolved)
    assert next(m for m in mems if m["text"] == "contradicted belief")["state"] == "CONTRADICTED"
    # a fresh invalidated belief is terminal immediately
    store.create(memory.COLL, {"userId": "boss", "kind": "global",
                               "text": "invalidated belief",
                               "meta": {"invalidated": True},
                               "createdAt": datetime.now(timezone.utc).isoformat()})
    mems = memory.recall("boss")
    assert next(m for m in mems if m["text"] == "invalidated belief")["state"] == "INVALIDATED"


def test_hypothesis_enrichment_fields(env):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.learning.hypotheses import create_hypothesis
    h = create_hypothesis("boss", "strategy_2_ema_atr", "fast_len",
                          int(9) + 1, "reason", "effect",
                          observation="loses in high-vol NY",
                          supporting_evidence=[{"n": 8, "wr": 16.7}],
                          contradictory_evidence=[{"n": 3, "wr": 60}],
                          evidence_state="INSUFFICIENT",
                          falsification="if next 20 high-vol trades keep WR >= baseline")
    assert h["observation"] == "loses in high-vol NY"
    assert h["supporting_evidence"] and h["contradictory_evidence"]
    assert h["evidence_state"] == "INSUFFICIENT"
    assert h["falsification_criteria"].startswith("if next 20 high-vol")


# ===========================================================================
# Stage 4: TCA + broker intelligence + exposure + adaptive risk
# ===========================================================================
def test_tca_slippage_and_reject_rate(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(8):
        s = store.create("signals", {"userId": "boss", "strategy_id": "strategy_2_ema_atr",
                                     "market": "EURUSD", "direction": "BUY",
                                     "entry": 1.1000, "mt5_open_price": 1.1002,
                                     "completed": True, "outcome": "WIN",
                                     "r_multiple": 1.0, "mt5_pl": 12.5,
                                     "mt5_confirmed": True, "signal_id": f"T-{i}"})
    for i in range(4):
        store.create("exec_events", {"userId": "boss", "kind": "ENTRY",
                                     "stage": "CONFIRMED", "ticket": 100 + i,
                                     "latency_ms": 210.0})
    store.create("exec_events", {"userId": "boss", "kind": "ENTRY",
                                 "stage": "FAILED", "detail": "reject"})
    rep = tca_report_live(c)
    assert rep["slippage"]["measured_n"] == 8
    assert rep["slippage"]["avg_signed_slippage"] == 0.0002   # worse fill for BUY
    bi = rep["broker_intelligence"]
    assert bi["entry_requests"] == 5 and bi["failed"] == 1
    assert bi["reject_rate_pct"] == 20.0
    assert rep["costs"]["commission_swap"] is None            # never estimated
    assert "net P/L" in rep["costs"]["note"]
    assert rep["latency_ms_avg"] == 210.0


def tca_report_live(c):
    r = c.get("/api/risk/tca")
    assert r.status_code == 200
    return r.json()


def test_exposure_currency_aggregation_and_adaptive_clamps(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    # force stored basis (bridge unreachable in test env)
    monkeypatch.setattr(settings, "bridge_url", "")
    store.create("signals", {"userId": "boss", "strategy_id": "strategy_2_ema_atr",
                             "market": "EURUSD", "direction": "BUY", "status": "open",
                             "completed": False, "mt5_volume": 0.3, "signal_id": "E1"})
    store.create("signals", {"userId": "boss", "strategy_id": "strategy_2_ema_atr",
                             "market": "USDJPY", "direction": "SELL", "status": "open",
                             "completed": False, "mt5_volume": 0.1, "signal_id": "E2"})
    r = c.get("/api/risk/exposure").json()
    assert r["open_positions"] == 2
    usd = r["currency_exposure"]["USD"]
    assert usd["buy_lots"] == 0.3 and usd["sell_lots"] == 0.1
    assert usd["net_lots"] == 0.2
    # adaptive suggestion is clamped and report-only by default
    a = c.get("/api/risk/adaptive?market=EURUSD").json()
    assert 0.5 <= a["suggested_multiplier"] <= 1.0
    assert a["enforced"] is False
    assert "REPORT-ONLY" in a["note"]


# ===========================================================================
# Stage 5: Monte Carlo + stress + calibration + multiple testing + shadow
# ===========================================================================
def test_montecarlo_distributions_and_insufficient(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(12):
        _sig(store, i, outcome="WIN" if i % 3 else "LOSS", r=1.0 if i % 3 else -1.0)
    r = c.post("/api/research/montecarlo", json={"strategy_id": "strategy_2_ema_atr"})
    body = r.json()
    assert body["status"] == "SIMULATION"                 # labeled, never a prediction
    assert body["source_trades"] == 12
    assert body["drawdown_r"]["p50"] is not None
    assert 0 <= body["risk_of_ruin"]["probability"] <= 1
    assert "simulation" in body["disclaimer"].lower()
    # insufficient data -> honest refusal
    r2 = c.post("/api/research/montecarlo", json={"strategy_id": "strategy_1_zero_lag"})
    assert r2.json()["status"] == "INSUFFICIENT_DATA"


def test_stresslab_runs_real_engine_on_synthetic(env):
    c, store, monkeypatch, settings = env
    r = c.post("/api/research/stress", json={"strategy_id": "strategy_2_ema_atr"})
    body = r.json()
    assert body["status"] == "SYNTHETIC_TEST"
    scen = {s["scenario"]: s for s in body["scenarios"]}
    assert set(scen) >= {"trend_up", "range", "high_vol", "gap", "rapid_reversal"}
    assert all("SYNTHETIC" in s["data"] for s in body["scenarios"])
    assert all("error" not in s for s in body["scenarios"])   # engine ran clean
    assert "NOT real historical evidence" in body["disclaimer"]


def test_calibration_insufficient_then_ok(env):
    c, store, monkeypatch, settings = env
    _user(store)
    r = c.get("/api/research/calibration").json()
    assert r["status"] == "CALIBRATION_INSUFFICIENT"       # never fabricate
    for i in range(25):
        win = i % 2 == 0
        _sig(store, i, outcome="WIN" if win else "LOSS")
        store.create("ai_decisions", {
            "userId": "boss", "signal_id": f"S-EURUSD-{i}",
            "brain": {"confidence_pct": 80.0 if win else 20.0}})
    r2 = c.get("/api/research/calibration").json()
    assert r2["status"] == "OK"
    assert r2["scored"] == 25
    assert 0 <= r2["brier_score"] <= 1
    assert r2["reliability_table"]


def test_research_summary_multiple_testing(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(7):
        store.create("experiments", {"userId": "boss", "result": {"passed": i == 6},
                                     "combinations": [1, 2, 3]})
    r = c.get("/api/research/summary").json()
    assert r["experiments_total"] == 7
    assert r["experiments_passed"] == 1
    assert r["experiments_failed"] == 6
    assert "best-of-7" in r["multiple_testing_note"]


def test_shadow_cycle_noop_and_evaluation(env, monkeypatch):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.research.shadow import run_shadow_cycle
    out = run_shadow_cycle("boss")
    assert out["strategies"] == []                        # nothing in shadow -> no-op
    # flag a strategy into shadow, record a pending hypothetical, evaluate it
    sid = "strategy_2_ema_atr"
    store.create("strategies", {"id": sid, "shadow": True, "status": "ACTIVE"})
    store.create("shadow_trades", {
        "userId": "boss", "strategy_id": sid, "market": "EURUSD",
        "direction": "BUY", "entry": 1.10, "sl": 1.0990, "tp1": 1.1030,
        "signal_time": "2026-09-01T00:00:00", "resolved": False,
        "execution": "HYPOTHETICAL - never sent to broker"})
    hist = [{"t": f"2026-09-0{d}T{h:02d}:00:00", "o": 1.1, "h": 1.1005,
             "l": 1.0995, "c": 1.1} for d in (1, 2, 3, 4) for h in range(24)]
    hist[30] = {"t": "2026-09-02T01:00:00", "o": 1.1, "h": 1.1035,
                "l": 1.0995, "c": 1.103}
    import app.market_data.candle_store as cs
    monkeypatch.setattr(cs, "history", lambda m, limit=300: hist)
    out2 = run_shadow_cycle("boss")
    assert sid in out2["strategies"]
    assert out2["evaluated"] == 1
    doc = [t for t in store.list("shadow_trades", limit=10) if t.get("resolved")][0]
    assert doc["outcome"] == "TP1+"


# ===========================================================================
# Stage 8: knowledge graph + NL research
# ===========================================================================
def test_knowledge_graph_and_query(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(6):
        _sig(store, i, market="EURUSD", session="London", regime="TRENDING")
    g = c.get("/api/knowledge/graph").json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"strategy", "market", "session", "regime"} <= kinds
    assert any(e["kind"] == "trades" for e in g["edges"])
    q = c.get("/api/knowledge/query?strategy_id=strategy_2_ema_atr&market=EURUSD"
              "&session=London&regime=TRENDING").json()
    assert q["n_total"] >= 6
    assert q["win_rate_blended"] is not None
    assert q["answer_basis"].startswith("structured matrix")


def test_nl_research_deterministic_and_ai_whitelist(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(6):
        _sig(store, i, market="EURUSD", outcome="LOSS")
    r = c.post("/api/research/ask", json={"question": "show me every strategy 2 "
                                                  "EURUSD loss during London"}).json()
    assert r["translation"]["via"] == "deterministic_parser"
    assert r["translation"]["filters"]["outcome"] == "LOSS"
    assert r["matched"] == 6
    assert r["sample"] and r["sample"][0]["outcome"] == "LOSS"
    # AI fallback is whitelist-validated: evil keys stripped, allowed kept
    r2 = c.post("/api/research/ask", json={"question": "something unparseable??"}).json()
    assert r2["translation"]["via"] in ("deterministic_parser", "ai_proposed_validated",
                                        "unparsed")
    for k in r2["translation"]["filters"]:
        assert k in ("strategy_id", "market", "outcome", "session", "regime")


def test_legacy_data_shapes_never_500(env):
    """Regression: prod has old experiment docs (result as string) and
    odd signal docs - aggregation endpoints must tolerate them."""
    c, store, monkeypatch, settings = env
    _user(store)
    store.create("experiments", {"userId": "boss", "result": "passed 3 folds",
                                 "combinations": None})
    store.create("experiments", {"userId": "boss", "result": {"passed": True},
                                 "combinations": [1, 2]})
    monkeypatch.setattr(settings, "bridge_url", "")
    r = c.get("/api/research/summary").json()
    assert r["experiments_total"] == 2 and r["experiments_passed"] == 1
    assert r["parameter_combinations_tested"] == 3
    assert c.get("/api/risk/exposure").status_code == 200
    store.create("signals", {"userId": "boss", "strategy_id": "strategy_2_ema_atr",
                             "market": "XAUUSD", "completed": False,
                             "status": "SKIPPED_RISK_NEWS", "sl": "bad", "tp1": None})
    assert c.get("/api/research/blocked").status_code == 200
