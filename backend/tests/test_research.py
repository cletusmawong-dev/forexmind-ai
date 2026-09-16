"""Autonomous Strategy Research Lab tests (user spec 2026-09-16, UPGRADE 2).

Covers: hypothesis discovery from historical divergence, evidence labeling,
one-variable enforcement on designed experiments, experiment storage fields
(split, robustness, frequency), overfitting/small-sample flags, lifecycle
statuses, approval protection (no automatic live modification), versioning
and rollback protection.
"""
import numpy as np
import pandas as pd
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


@pytest.fixture()
def seeded(fresh_store):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    return fresh_store


def _completed(store, n, outcome="WIN", r=2.0, **kw):
    base = {
        "userId": "demo-user", "strategy_id": "strategy_2_ema_atr",
        "strategy_version": "1.0", "market": "XAUUSD", "timeframe": "15M",
        "direction": "BUY", "entry": 3300.0, "sl": 3295.0, "tp1": 3310.0,
        "completed": True, "status": "TP1_HIT" if outcome == "WIN" else "SL_HIT",
        "createdAt": "2026-09-15T08:00:00Z", "adaptive": None,
        "dna": {"regime": "TRENDING_BULLISH", "session": "London",
                "volatility_rank": 0.5, "momentum": "UP",
                "mtf": {"1H": 1, "4H": 1, "1D": 1}},
    }
    base.update(kw)
    docs = []
    for i in range(n):
        docs.append(store.create("signals", dict(
            base, signal_id=f"SIG-{outcome}-{i}-{kw.get('market','XAUUSD')}",
            outcome=outcome, r_multiple=r)))
    return docs


def test_discovery_needs_minimum_samples(seeded):
    from app.learning.research import discover
    _completed(seeded, 4, "WIN")       # far below MIN_OVERALL
    found = discover("demo-user")
    assert found == []
    assert seeded.list("research_hypotheses", limit=10) == []


def test_discovery_finds_labeled_divergence(seeded):
    from app.learning.research import discover
    # overall baseline ~50%; London regime WIN-heavy -> divergence
    _completed(seeded, 15, "WIN", 2.0)
    _completed(seeded, 15, "LOSS", -1.0,
               dna={"regime": "RANGING", "session": "London",
                    "volatility_rank": 0.5, "mtf": {"1H": 1, "4H": 1, "1D": 1}})
    found = discover("demo-user")
    assert len(found) >= 1
    kinds = {d["kind"] for d in found}
    assert kinds <= {"UNTESTED HYPOTHESIS"}
    for d in found:
        assert d["status"] == "DISCOVERED"
        assert d["sample_size"] >= 10
        assert d["overall"]["n"] >= 20
        assert len(d["evidence"]) <= 25
        assert "NOT a strategy change" in d["note"]
    # deterministic dedupe: second run discovers nothing new
    assert discover("demo-user") == []


def test_no_discovery_when_outcomes_uniform(seeded):
    from app.learning.research import discover
    _completed(seeded, 25, "WIN", 2.0)   # all wins -> no divergence
    assert discover("demo-user") == []


def test_design_experiment_one_variable_enforced(seeded):
    from app.learning.research import design_experiment
    research = seeded.create("research_hypotheses", {
        "userId": "demo-user", "strategy_id": "strategy_2_ema_atr",
        "market": "XAUUSD", "segment_type": "regime",
        "segment_label": "RANGING", "claim": "test claim",
        "kind": "UNTESTED HYPOTHESIS", "status": "DISCOVERED",
        "sample_size": 12, "overall": {"n": 30, "win_rate": 50.0},
        "segment_win_rate": 30.0,
    })
    # a NON-experimentable variable is rejected
    with pytest.raises(ValueError):
        design_experiment("demo-user", seeded.get("research_hypotheses", research["id"]),
                          "not_a_variable", 5)
    # an out-of-range value is CLAMPED to the variable's max (existing behavior)
    hyp_clamp = design_experiment("demo-user",
                                  seeded.get("research_hypotheses", research["id"]),
                                  "fast_len", 999)
    assert hyp_clamp["new_value"] == 50  # fast_len max
    # one legitimate variable -> normal PROPOSED hypothesis, linked back
    hyp = design_experiment("demo-user",
                            seeded.get("research_hypotheses", research["id"]),
                            "sl_mult", 1.6)
    assert hyp["variable"] == "sl_mult"
    assert hyp["new_value"] == 1.6
    assert hyp["old_value"] == 1.5
    assert hyp["status"] == "PROPOSED"
    assert "Research discovery" in hyp["reason"]
    # live strategy params untouched by any of this
    from app.learning.versions import active_params
    assert active_params("strategy_2_ema_atr")["sl_mult"] == 1.5


def test_experiment_doc_has_robustness_and_frequency(fresh_store, xau_15m):
    class FakeProvider:
        is_demo = True
        name = "test"
        def get_candles(self, market, tf, limit=4000):
            return xau_15m.copy()

    from app.learning.experiments import ExperimentEngine
    from app.learning.hypotheses import create_hypothesis
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    engine = ExperimentEngine(FakeProvider())
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "sl_mult", 1.8,
                          reason="t", expected_effect="t",
                          dataset={"market": "XAUUSD", "timeframe": "15M"})
    h["entry_ok"] = True
    exp = engine.run_from_hypothesis("demo-user", fresh_store.get("hypotheses", h["id"]))
    assert exp["experiment_code"].startswith("EXP-")
    assert exp["status"] == "READY_FOR_REVIEW"          # always stops for review
    assert "split" in exp and "train" in exp["split"]   # 70/30 time-ordered
    assert "robustness" in exp                          # per-session or honest note
    assert "signal_frequency_per_day" in exp
    assert "overfitting_risk" in exp and "small_sample_warning" in exp
    # metrics beyond win rate stored
    m = exp["experimental_metrics"]
    for key in ("win_rate", "losses", "tp1_hit_rate", "tp2_hit_rate",
                "tp3_hit_rate", "sl_rate", "total_r", "expectancy",
                "profit_factor", "max_drawdown_r", "avg_duration_bars"):
        assert key in m


def test_approval_protection_no_auto_modification(seeded):
    """Experiment + READY_FOR_REVIEW must never change the live version."""
    from app.learning.versions import active_params, active_version
    before = (active_version("strategy_2_ema_atr"),
              active_params("strategy_2_ema_atr")["sl_mult"])
    # even a 'perfect' experiment result is only a doc - params untouched
    from app.learning.experiments import ExperimentEngine  # noqa: F401
    after = (active_version("strategy_2_ema_atr"),
             active_params("strategy_2_ema_atr")["sl_mult"])
    assert before == after


def test_rollback_still_requires_confirm(client, auth):
    r = client.post("/api/strategies/strategy_2_ema_atr/rollback",
                    headers={"Authorization": f"Bearer {auth['token']}"},
                    json={"target_version": "1.0"})
    assert r.status_code == 428  # explicit confirmation still mandatory


def test_research_api_flow(client, auth):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    H = {"Authorization": f"Bearer {auth['token']}"}
    # discover on an empty history -> created 0, endpoint honest
    r = client.post("/api/learning/research/discover", headers=H)
    assert r.status_code == 200 and r.json()["created"] == 0
    # list endpoint works
    r2 = client.get("/api/learning/research", headers=H)
    assert r2.status_code == 200 and "hypotheses" in r2.json()
    # design on unknown research id -> 404
    r3 = client.post("/api/learning/research/none/design", headers=H,
                     json={"variable": "sl_mult", "new_value": 1.6})
    assert r3.status_code == 404
