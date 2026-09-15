"""Advanced Intelligence Upgrade tests (SPEC §27-§53).

Covers: Signal DNA endpoint, forensics endpoint, replay endpoint (honest
coverage), regime endpoints, observations labeling, rollback confirmation +
audit trail, experiment codes + anti-overfitting split fields.
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
    """Fresh isolated store + demo user + token. auth["store"] is the SAME
    store the token was issued against."""
    from app.seed import _ensure_demo_user
    _ensure_demo_user()
    r = client.post("/api/auth/login", json={"email": "demo@forexmind.ai",
                                             "password": "demo1234"})
    assert r.status_code == 200
    return {"token": r.json()["token"], "store": fresh_store,
            "user_id": r.json()["user"]["id"]}


def A(auth):
    return {"Authorization": f"Bearer {auth['token']}"}


# ---------- Signal DNA ----------

def test_dna_unknown_signal_404(client, auth):
    r = client.get("/api/signals/does-not-exist/dna", headers=A(auth))
    assert r.status_code == 404


def test_dna_pre_capture_honest(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-DNA-1", "market": "XAUUSD",
        "completed": False,
    })
    r = client.get("/api/signals/SIG-DNA-1/dna", headers=A(auth))
    assert r.status_code == 200
    body = r.json()
    assert body["captured"] is False and body["dna"] is None
    assert "predates" in body["note"]


def test_dna_captured_when_present(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-DNA-2", "market": "XAUUSD",
        "dna": {"regime": "TRENDING_BULLISH", "atr14": 2.1},
    })
    r = client.get("/api/signals/SIG-DNA-2/dna", headers=A(auth))
    body = r.json()
    assert body["captured"] is True
    assert body["dna"]["regime"] == "TRENDING_BULLISH"


# ---------- Forensics ----------

def test_forensics_incomplete_signal_deferred(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-F-1", "market": "XAUUSD",
        "completed": False,
    })
    r = client.get("/api/signals/SIG-F-1/forensics", headers=A(auth))
    body = r.json()
    assert body["forensics"] is None and body["source"] == "none"
    assert "completes" in body["note"]


def test_forensics_computed_with_labeled_findings(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-F-2", "market": "XAUUSD",
        "completed": True, "outcome": "LOSS", "r_multiple": -1.0,
        "strategy_id": "strategy_2_ema_atr",
    })
    r = client.get("/api/signals/SIG-F-2/forensics", headers=A(auth))
    body = r.json()
    assert body["source"] == "computed"
    f = body["forensics"]
    assert f and "findings" in f and "sample_size" in f
    for finding in f["findings"]:
        assert finding["kind"] in ("FACT", "POSSIBLE_EXPLANATION",
                                   "UNTESTED HYPOTHESIS", "HYPOTHESIS")


# ---------- Replay ----------

def test_replay_honest_when_no_candle_coverage(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-R-1", "market": "XAUUSD",
        "entry": 3300.0, "sl": 3295.0, "tp1": 3310.0, "tp2": 3320.0,
        "tp3": 3330.0, "direction": "BUY", "completed": False,
        "candle_time": "2026-09-15T08:00:00+00:00",
    })
    r = client.get("/api/signals/SIG-R-1/replay", headers=A(auth))
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] in ("unavailable", "partial", "full")
    assert body["markers"]["entry"] == 3300.0
    assert body["signal"]["direction"] == "BUY"
    if body["coverage"] == "unavailable":
        assert body["note"]


# ---------- Regimes ----------

def test_market_regimes_informational_only(client, auth):
    r = client.get("/api/market-regimes?market=XAUUSD&days=3", headers=A(auth))
    assert r.status_code == 200
    body = r.json()
    assert body["informational_only"] is True
    assert "current" in body and "history" in body


# ---------- Observations ----------

def test_observations_labeled_and_honest(client, auth):
    auth["store"].create("signals", {
        "userId": "demo-user", "signal_id": "SIG-O-1", "completed": True,
        "outcome": "WIN", "r_multiple": 2.0, "dna": {"regime": "RANGING"},
    })
    r = client.get("/api/learning/observations", headers=A(auth))
    assert r.status_code == 200
    body = r.json()
    assert body["informational_only"] is True
    for obs in body["observations"]:
        assert obs["kind"] in ("FACT", "POSSIBLE_EXPLANATION",
                               "UNTESTED HYPOTHESIS")
        assert "sample_size" in obs
    small = [o for o in body["observations"]
             if o.get("regime") == "RANGING"]
    assert small and small[0]["kind"] == "UNTESTED HYPOTHESIS"


# ---------- Rollback confirmation + audit ----------

def test_rollback_requires_explicit_confirmation(client, auth):
    r = client.post("/api/strategies/strategy_2_ema_atr/rollback",
                    headers=A(auth), json={"target_version": "1.0"})
    assert r.status_code == 428
    assert "confirmation" in r.json()["detail"]


def test_rollback_with_confirm_writes_audit(client, auth):
    from app.learning.hypotheses import create_hypothesis
    from app.learning.versions import approve_hypothesis, ensure_strategy_docs
    ensure_strategy_docs()
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "sl_mult", 2.0,
                          reason="t", expected_effect="t")
    approve_hypothesis("demo-user", h["id"])
    r = client.post("/api/strategies/strategy_2_ema_atr/rollback",
                    headers=A(auth),
                    json={"target_version": "1.0", "confirm": True,
                          "reason": "test rollback"})
    assert r.status_code == 200
    assert r.json()["rolled_back"] is True
    events = auth["store"].list("version_events",
                                filters={"strategy_id": "strategy_2_ema_atr"})
    assert events and events[-1]["kind"] == "ROLLBACK"
    assert events[-1]["reason"] == "test rollback"
    assert events[-1]["to_version"] == "1.0"


# ---------- Experiment engine upgrades ----------

def test_experiment_doc_has_code_status_and_split(fresh_store, xau_15m):
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
    h = create_hypothesis("demo-user", "strategy_2_ema_atr", "sl_mult", 2.5,
                          reason="t", expected_effect="t",
                          dataset={"market": "XAUUSD", "timeframe": "15M"})
    h["entry_ok"] = True
    exp = engine.run_from_hypothesis("demo-user", fresh_store.get("hypotheses", h["id"]))
    assert exp["experiment_code"].startswith("EXP-")
    assert len(exp["experiment_code"]) == len("EXP-000000")
    assert exp["status"] == "READY_FOR_REVIEW"
    assert "overfitting_risk" in exp
    assert exp["split"] and "train" in exp["split"] and "validation" in exp["split"]
