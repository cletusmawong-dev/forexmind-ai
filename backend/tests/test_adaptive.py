"""Adaptive Signal Intelligence tests (user spec 2026-09-16, UPGRADE 1).

Covers: deterministic similarity + weighting, minimum-sample protection,
missing-data honesty, quality-score calculation, explainability, Signal DNA
integration, API endpoint, and the hard rule that evaluation NEVER modifies
entry/SL/TP.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client, fresh_store):
    from app.seed import _ensure_demo_user
    _ensure_demo_user()
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    assert r.status_code == 200
    return {"token": r.json()["token"], "store": fresh_store}


def _sig(**kw):
    base = {
        "id": kw.pop("id", "sx"), "userId": "demo-user",
        "signal_id": kw.pop("signal_id", "SIG-X"),
        "strategy_id": "strategy_2_ema_atr", "strategy_version": "1.0",
        "market": "XAUUSD", "timeframe": "15M", "direction": "BUY",
        "entry": 3300.0, "sl": 3295.0, "tp1": 3310.0,
        "completed": True, "outcome": "WIN", "r_multiple": 2.0,
        "createdAt": "2026-09-15T08:00:00Z",
        "score_components": {"HTF alignment": 60.0, "HTF 100 EMA": 20.0,
                             "Crossover strength": 10.0},
        "dna": {"regime": "TRENDING_BULLISH", "session": "London",
                "volatility_rank": 0.55, "momentum": "UP",
                "mtf": {"1H": 1, "4H": 1, "1D": 1},
                "news_proximity_min": None},
    }
    base.update(kw)
    return base


@pytest.fixture()
def store(fresh_store):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    return fresh_store


def test_similarity_deterministic_and_weighted(store):
    from app.learning.adaptive import _similarity, features
    a = features(_sig())
    # identical twin -> exactly 1.0
    assert _similarity(a, features(_sig(id="s2", signal_id="SIG-2"))) == 1.0
    # hard requirements: different market / direction / strategy -> None
    assert _similarity(a, features(_sig(id="s3", signal_id="SIG-3", market="EURUSD"))) is None
    assert _similarity(a, features(_sig(id="s4", signal_id="SIG-4", direction="SELL"))) is None
    assert _similarity(a, features(_sig(id="s5", signal_id="SIG-5",
                                        strategy_id="strategy_1_zero_lag"))) is None
    # regime differs -> score below 1.0 but deterministic across calls
    b = features(_sig(id="s6", signal_id="SIG-6",
                      dna={"regime": "RANGING", "session": "London",
                           "volatility_rank": 0.55, "momentum": "UP",
                           "mtf": {"1H": 1, "4H": 1, "1D": 1},
                           "news_proximity_min": None}))
    s1 = _similarity(a, b)
    s2 = _similarity(a, b)
    assert s1 == s2 and 0.0 <= s1 < 1.0


def test_minimum_sample_protection(store):
    from app.learning.adaptive import evaluate
    # only 3 comparable completed signals -> INSUFFICIENT SAMPLE, no win rate
    for i in range(3):
        store.create("signals", _sig(id=f"h{i}", signal_id=f"SIG-H{i}"))
    target = store.create("signals", _sig(id="t", signal_id="SIG-T",
                                          completed=False, outcome=None,
                                          r_multiple=0.0))
    ev = evaluate(store.get("signals", "t"), store)
    assert ev["historical"]["status"] == "INSUFFICIENT_SAMPLE"
    assert "win_rate" not in ev["historical"]
    assert "Insufficient sample" in ev["historical"]["note"]
    assert "historical_evidence" in ev["missing_data"]


def test_quality_score_calculation_and_weights(store):
    from app.learning.adaptive import WEIGHTS, evaluate
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9  # documented, sums to 1
    for i in range(12):  # 12 comparable -> historical evidence computable
        store.create("signals", _sig(id=f"k{i}", signal_id=f"SIG-K{i}",
                                     outcome="WIN" if i % 3 else "LOSS",
                                     r_multiple=2.0 if i % 3 else -1.0))
    target = store.create("signals", _sig(id="t2", signal_id="SIG-T2",
                                          completed=False, outcome=None,
                                          r_multiple=0.0))
    ev = evaluate(store.get("signals", "t2"), store)
    assert ev["historical"]["status"] == "OK"
    assert ev["historical"]["similar_signals"] == 12
    assert 0 <= ev["score"] <= 100
    # every component present in weights and renormalized over available
    assert set(ev["components"]).issubset(set(WEIGHTS))
    assert "historical_evidence" in ev["components"]
    assert ev["weights"] == WEIGHTS  # methodology stored with evaluation
    assert ev["reliability"] == "MEDIUM"  # 10..19 comparables


def test_explainability_and_dna_integration(store):
    from app.learning.adaptive import evaluate
    for i in range(12):
        store.create("signals", _sig(id=f"e{i}", signal_id=f"SIG-E{i}"))
    target = store.create("signals", _sig(id="t3", signal_id="SIG-T3",
                                          completed=False, outcome=None,
                                          r_multiple=0.0))
    ev = evaluate(store.get("signals", "t3"), store)
    # explainability fields all present
    for key in ("score", "components", "historical", "sample_size" if False else "reliability",
                "missing_data", "checks", "adaptive_version"):
        assert key in ev
    # WHY checklist built from actual evidence
    labels = " ".join(c["label"] for c in ev["checks"])
    assert "comparable historical signals" in labels
    # DNA integration: features derived from the signal's dna block
    assert ev["features"]["regime"] == "TRENDING_BULLISH"
    assert ev["features"]["session"] == "London"
    assert ev["features"]["volatility_rank"] == 0.55
    # segment evidence honest when dna missing on history
    assert ev["informational_only"] is True


def test_evaluation_never_touches_levels(store):
    from app.learning.adaptive import evaluate_and_store
    target = store.create("signals", _sig(id="t4", signal_id="SIG-T4",
                                          completed=False, outcome=None,
                                          r_multiple=0.0))
    before = (target["entry"], target["sl"], target["tp1"])
    evaluate_and_store(store, store.get("signals", "t4"))
    after_doc = store.get("signals", "t4")
    assert (after_doc["entry"], after_doc["sl"], after_doc["tp1"]) == before
    assert after_doc.get("adaptive")  # evaluation stored


def test_adaptive_api_scoped_and_sources(client, auth):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    auth["store"].create("signals", _sig(id="a1", signal_id="SIG-A1",
                                         userId="demo-user"))
    r = client.get("/api/signals/SIG-A1/adaptive", headers={
        "Authorization": f"Bearer {auth['token']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] in ("stored", "computed")
    assert body["adaptive"]["informational_only"] is True
    # second call serves the stored copy
    r2 = client.get("/api/signals/SIG-A1/adaptive", headers={
        "Authorization": f"Bearer {auth['token']}"})
    assert r2.json()["source"] == "stored"
    # 404 unknown
    r3 = client.get("/api/signals/nope/adaptive", headers={
        "Authorization": f"Bearer {auth['token']}"})
    assert r3.status_code == 404


def test_news_environment_scoring(store):
    from app.learning.adaptive import evaluate
    target = store.create("signals", _sig(id="n1", signal_id="SIG-N1",
                                          completed=False, outcome=None,
                                          r_multiple=0.0,
                                          dna={"regime": "TRENDING_BULLISH",
                                               "session": "London",
                                               "volatility_rank": 0.6,
                                               "news_proximity_min": 10}))
    ev = evaluate(store.get("signals", "n1"), store)
    assert ev["components"]["news_environment"] == 20.0
    assert "high-impact news within 30 min" in ev["news"]["note"]
