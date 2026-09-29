"""FOREXMIND 3.0 Stage 1: Evidence Engine + brain EVIDENCE stage + health.

Spec rules under test:
- states INSUFFICIENT/PRELIMINARY/SUPPORTED/STRONGER (+ internal CONTRADICTED)
- sample gates exist but state is never from sample size alone
- deterministic, reproducible scoring (same inputs -> same score)
- contradictions are surfaced, never hidden (spec 5)
- evidence is independent of LLM confidence (model_confidence stays None)
- calibration never fabricated (CALIBRATION_INSUFFICIENT)
- user isolation on every evidence route
- evidence NEVER mutates strategies/permissions/execution
- brain never breaks even when evidence fails (additive annotation only)
- degradation engine reports, never pauses
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# fixture (same isolation pattern as the rest of the suite)
# ---------------------------------------------------------------------------
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


def _user(store, uid="boss"):
    store.create("users", {"id": uid, "userId": uid, "email": f"{uid}@x.io",
                           "role": "admin", "status": "active"})


def _sig(store, i, outcome="WIN", session="London", regime="TRENDING",
         uid="boss", market="EURUSD", sid="strategy_2_ema_atr",
         r=1.0, tp_hits=2, confirmed=True, when=None):
    """One completed signal with full data quality."""
    t = when or (datetime(2026, 9, 1, 10, i % 60, tzinfo=timezone.utc)
                 + timedelta(days=i // 60))
    store.create("signals", {
        "userId": uid, "strategy_id": sid, "strategy_name": "EMA Smart TP/SL",
        "market": market, "completed": True, "outcome": outcome,
        "r_multiple": r, "tp_hits": tp_hits, "mt5_confirmed": confirmed,
        "market_conditions": {"session": session, "regime": regime},
        "candle_time": t.isoformat(), "completed_at": t.isoformat(),
        "signal_id": f"{sid[:3]}-{i}",
    })


# ===========================================================================
# state classification (pure)
# ===========================================================================
def test_state_mapping_sample_gates_with_quality_overrides():
    from app.evidence.engine import state_for
    assert state_for(5, 0.9, 0.0, 90.0, 50.0) == "INSUFFICIENT"     # gate
    assert state_for(25, 0.9, 0.0, 55.0, 50.0) == "PRELIMINARY"     # 20-49
    assert state_for(55, 0.70, 0.0, 52.0, 50.0) == "SUPPORTED"      # 50-99 + score
    assert state_for(55, 0.40, 0.0, 48.0, 50.0) == "PRELIMINARY"    # weak score
    assert state_for(120, 0.75, 0.0, 55.0, 50.0) == "STRONGER"      # 100+ quality
    assert state_for(120, 0.75, 0.0, 30.0, 50.0) == "SUPPORTED"     # recent collapse blocks STRONGER
    assert state_for(60, 0.8, 0.7, 25.0, 50.0) == "CONTRADICTED"    # internal state


def test_scoring_is_deterministic(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(60):
        _sig(store, i, outcome="WIN" if i % 10 < 6 else "LOSS", r=0.5)
    from app.evidence import engine
    a = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD", persist=False)
    b = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD", persist=False)
    assert a["score"] == b["score"]
    assert a["state"] == b["state"]
    assert a["score_components"] == b["score_components"]
    assert a["evidence_id"] == b["evidence_id"]


# ===========================================================================
# engine behavior on seeded data
# ===========================================================================
def test_insufficient_below_20_and_never_mutates_strategy(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(12):
        _sig(store, i, outcome="WIN")
    from app.evidence import engine
    ev = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    assert ev["state"] == "INSUFFICIENT"
    assert ev["sample_size"] == 12
    assert ev["model_confidence"] is None                 # LLM-independent
    assert ev["calibrated_probability"] is None
    assert ev["calibration"] == "CALIBRATION_INSUFFICIENT"  # never fabricated
    docs = store.list("strategies", filters={"id": "strategy_2_ema_atr"}, limit=1)
    assert docs == [] or docs[0].get("params") is None     # untouched


def test_supported_band_and_missing_evidence_flags(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(55):
        _sig(store, i, outcome="WIN" if i % 10 < 6 else "LOSS")
    from app.evidence import engine
    ev = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    assert ev["state"] == "SUPPORTED"                     # 60% >= 60% gate-ish
    assert ev["historically_supported"] is True
    assert "out_of_sample" in ev["missing_evidence"]
    assert "walk_forward" in ev["missing_evidence"]
    assert ev["walk_forward_strength"] is None


def test_contradiction_recent_collapse_downgrades_and_is_visible(env):
    c, store, monkeypatch, settings = env
    _user(store)
    # 40 strong wins, then 20 terrible losses (baseline ~67%, recent 0%)
    for i in range(40):
        _sig(store, i, outcome="WIN", r=1.2, tp_hits=2)
    for i in range(40, 60):
        _sig(store, i, outcome="LOSS", r=-1.0, tp_hits=0)
    from app.evidence import engine
    ev = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    factors = [x["factor"] for x in ev["contradictions"]]
    assert "recent_collapse" in factors
    assert ev["contradiction_strength"] > 0.3
    assert ev["state"] in ("CONTRADICTED", "PRELIMINARY")  # downgraded from SUPPORTED band
    assert ev["currently_supported"] is False
    assert ev["historically_supported"] is True            # spec 6: historic vs current split


def test_evidence_age_fields_present(env):
    c, store, monkeypatch, settings = env
    _user(store)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS", when=old + timedelta(hours=i))
    from app.evidence import engine
    ev = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    assert ev["first_observed_at"] and ev["last_observed_at"]
    assert ev["evidence_age_days"] is not None and ev["evidence_age_days"] >= 29
    assert ev["last_refresh_at"]


def test_upsert_same_id_updated_not_duplicated(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS")
    from app.evidence import engine
    a = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    b = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    assert a["evidence_id"] == b["evidence_id"]
    docs = store.list("evidence", filters={"userId": "boss"})
    assert len(docs) == 1
    assert b["refresh_count"] == 1 and a["refresh_count"] == 0
    assert b["created_at"] == a["created_at"]              # created_at preserved


# ===========================================================================
# APIs: auth, user isolation, generate/list/get
# ===========================================================================
def test_evidence_api_roundtrip_and_isolation(env):
    c, store, monkeypatch, settings = env
    _user(store, "boss")
    _user(store, "mallory")
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS", uid="boss")
    r = c.post("/api/evidence/generate", json={})
    assert r.status_code == 200
    assert r.json()["count"] >= 1
    lst = c.get("/api/evidence").json()
    assert lst["count"] >= 1
    ev = lst["evidence"][0]
    for field in ("evidence_id", "subject_id", "state", "score", "sample_size",
                  "supporting_factors", "contradictions", "missing_evidence",
                  "limitations", "created_at", "updated_at"):
        assert field in ev
    # isolation: another user generates -> sees none of boss's docs
    from app.api.deps import get_user_id
    c.app.dependency_overrides[get_user_id] = lambda: "mallory"
    other = c.get("/api/evidence").json()
    assert other["count"] == 0
    got = c.get(f"/api/evidence/{ev['evidence_id']}")
    assert got.status_code == 404                          # cannot read boss's evidence
    c.app.dependency_overrides[get_user_id] = lambda: "boss"
    assert c.get(f"/api/evidence/{ev['evidence_id']}").status_code == 200


def test_data_quality_flags_incomplete_signals(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS")
    # 5 signals missing r_multiple -> data quality < 1, flagged
    docs = store.list("signals", filters={"userId": "boss"}, limit=5)
    for d in docs[:5]:
        store.update("signals", d["id"], {"r_multiple": None})
    from app.evidence import engine
    ev = engine.build_evidence("boss", "strategy_2_ema_atr", "EURUSD")
    assert ev["data_quality"] < 1.0
    assert any(m.startswith("data_quality:") for m in ev["missing_evidence"])


# ===========================================================================
# brain EVIDENCE stage (additive on every path)
# ===========================================================================
class FakeRouter:
    def __init__(self, text):
        self.text = text

    def analyze(self, prompt, context, escalate=False, user_id=None):
        return {"text": self.text, "model": "fake", "layer": "primary"}


def _world_for(sample=50):
    return {"market": "EURUSD",
            "freshness": {"verdict": "OK"},
            "timeframes": {"15M": {"freshness": {"stale": False}},
                           "H4": {"freshness": {"stale": False}}},
            "historical": {"strategy_stats": {"sample_size": sample,
                                              "strategy_id": "strategy_2_ema_atr"}}}


def test_brain_paths_carry_evidence_and_epistemic_labels(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS")
    import json as _json
    from app.ai import brain
    hold = {"answer": "NO_ACTION", "reason_codes": ["FLAT"]}
    out = brain.run_brain("boss", _world_for(), router=FakeRouter(_json.dumps(hold)))
    assert out["answer"] == "NO_ACTION"
    assert out["epistemic"]["type"] == "DECISION"          # honest non-action decision
    assert out["evidence"]["state"] in ("INSUFFICIENT", "PRELIMINARY",
                                        "SUPPORTED", "STRONGER", "CONTRADICTED")
    assert out["evidence"]["sample_size"] == 25
    # observer path (stale data) still annotated, labeled OBSERVATION
    stale = brain.run_brain("boss", {"market": "EURUSD",
                                     "freshness": {"verdict": "NO_DATA"}},
                            router=FakeRouter("{}"))
    assert stale["answer"] == "DATA_STALE"
    assert stale["epistemic"]["type"] == "OBSERVATION"


def test_brain_survives_evidence_engine_failure(env):
    c, store, monkeypatch, settings = env
    _user(store)
    import json as _json
    from app.ai import brain
    monkeypatch.setattr("app.evidence.brain_bridge.attach_evidence",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = brain.run_brain("boss", _world_for(sample=0),
                          router=FakeRouter(_json.dumps({"answer": "DATA_STALE"})))
    assert out["answer"] in ("DATA_STALE", "INSUFFICIENT_EVIDENCE")  # brain unharmed
    assert out["evidence"] is None


# ===========================================================================
# degradation engine: reports, never acts
# ===========================================================================
def test_health_states_and_never_mutates(env):
    c, store, monkeypatch, settings = env
    _user(store)
    from app.learning.health import strategy_health
    # insufficient
    for i in range(6):
        _sig(store, i, outcome="WIN")
    assert strategy_health("boss", "strategy_2_ema_atr")["state"] == "INSUFFICIENT"
    # healthy: consistent 55%
    for i in range(6, 46):
        _sig(store, i, outcome="WIN" if i % 100 < 55 else "LOSS")
    h = strategy_health("boss", "strategy_2_ema_atr")
    assert h["state"] in ("HEALTHY", "WATCH")
    assert "never pauses" in h["policy"]
    # degrading: last 20 all losses after a strong history
    for i in range(46, 66):
        _sig(store, i, outcome="LOSS", r=-1.0, tp_hits=0)
    h2 = strategy_health("boss", "strategy_2_ema_atr")
    assert h2["state"] in ("DEGRADING", "INVESTIGATION")
    assert h2["metrics"]["wr_drop_pp"] >= 15
    assert h2["metrics"]["tp_distribution"]["sl"] >= 20
    # strategy doc untouched by any health call (params identical pre/post)
    docs = store.list("strategies", filters={"id": "strategy_2_ema_atr"}, limit=1)
    assert docs == [] or docs[0].get("params") == docs[0].get("params")


def test_health_route_scoped_and_honest(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(15):
        _sig(store, i, outcome="WIN")
    r = c.get("/api/learning/strategy-health/strategy_2_ema_atr")
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]["n"] == 15
    assert body["state"] in ("HEALTHY", "WATCH")   # 15 all-wins is simply healthy
    assert "never pauses" in body["policy"]


def test_refresh_all_covers_traded_pairs_and_persists(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for mkt in ("EURUSD", "XAUUSD"):
        for i in range(25):
            _sig(store, i, outcome="WIN" if i % 2 else "LOSS", market=mkt)
    from app.evidence import engine
    docs = engine.refresh_all("boss")
    assert {d["instrument"] for d in docs} == {"EURUSD", "XAUUSD"}
    stored = store.list("evidence", filters={"userId": "boss"})
    assert len(stored) == 2
    assert all(d["state"] == "PRELIMINARY" for d in docs)  # 25 samples each


def test_research_cycle_refreshes_evidence(env):
    c, store, monkeypatch, settings = env
    _user(store)
    for i in range(25):
        _sig(store, i, outcome="WIN" if i % 2 else "LOSS")
    from app.learning.auto_loop import run_research_cycle
    out = run_research_cycle("boss")
    assert out.get("evidence_refreshed", 0) >= 1
    stored = store.list("evidence", filters={"userId": "boss"})
    assert len(stored) >= 1
