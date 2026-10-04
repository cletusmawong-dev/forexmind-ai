"""Owner directive (2026-10-03): the research loop may run constantly, but it
may only surface a REVIEW recommendation + Telegram ping when it has found a
genuinely BETTER version - validated on the same dataset with enough trades,
no overfitting risk, no small-sample caveat, and walk-forward consistency.
Failed / inconclusive / risky experiments stay in the Learning Lab SILENTLY.
The human always decides via the app; nothing is ever auto-applied."""
import pytest

from app.db.store import LocalStore


@pytest.fixture()
def env(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    monkeypatch.setattr(State, "store", store, raising=False)
    yield store


def _exp(store, doc_id, result, overfit=False, small=False, wf_consistent=None,
         hyp_id="h0"):
    return store.create("experiments", {
        "userId": "boss", "experiment_code": "EXP-000001",
        "hypothesis_id": hyp_id, "strategy_id": "strategy_2_ema_atr",
        "result": result, "overfitting_risk": overfit,
        "small_sample_warning": small,
        "walk_forward": ({"consistent": wf_consistent, "folds": [], "note": None}
                         if wf_consistent is not None
                         else {"consistent": None, "note": "insufficient trades"}),
    }, doc_id=doc_id)


def _hyp(store):
    return store.create("hypotheses", {
        "userId": "boss", "strategy_id": "strategy_2_ema_atr",
        "strategy_name": "9/21 EMA Smart TP/SL", "status": "PROPOSED",
        "variable": "tp1_atr", "old_value": 1.0, "new_value": 1.25,
    }, doc_id="h1")


def _run_cycle(monkeypatch, store, notified):
    from app.state import State
    class StubEngine:
        def run_from_hypothesis(self, user_id, hyp):
            # mirror the real engine: the experiment doc now belongs to this hyp
            store.update("experiments", "e1", {"hypothesis_id": hyp["id"]})
            return {"id": "e1", "code": "EXP-000001",
                    "result": store.get("experiments", "e1").get("result")}
    monkeypatch.setattr(State, "experiments", StubEngine(), raising=False)
    import app.notifications.service as svc
    monkeypatch.setattr(svc, "notify",
                        lambda *a, **k: notified.append(a[1] if len(a) > 1 else "x"))
    from app.learning.auto_loop import run_research_cycle
    return run_research_cycle("boss")


def test_worse_experiment_never_pings(env, monkeypatch):
    _hyp(env); _exp(env, "e1", "WORSE")
    notified = []
    out = _run_cycle(monkeypatch, env, notified)
    assert out["experiment"]["surfaced"] is False
    assert out["recommended"] == 0 and notified == []
    assert env.list("recommendations", limit=10) == []


def test_insufficient_data_never_pings(env, monkeypatch):
    _hyp(env); _exp(env, "e1", "INSUFFICIENT_DATA")
    notified = []
    out = _run_cycle(monkeypatch, env, notified)
    assert out.get("recommended", 0) == 0 and notified == []


def test_improved_but_overfit_never_pings(env, monkeypatch):
    _hyp(env); _exp(env, "e1", "IMPROVED", overfit=True)
    notified = []
    out = _run_cycle(monkeypatch, env, notified)
    assert out.get("recommended", 0) == 0 and notified == []


def test_improved_but_walk_forward_inconsistent_never_pings(env, monkeypatch):
    _hyp(env); _exp(env, "e1", "IMPROVED", wf_consistent=False)
    notified = []
    out = _run_cycle(monkeypatch, env, notified)
    assert out.get("recommended", 0) == 0 and notified == []


def test_genuinely_better_surfaces_and_pings(env, monkeypatch):
    _hyp(env)
    _exp(env, "e1", "IMPROVED", wf_consistent=True)
    notified = []
    out = _run_cycle(monkeypatch, env, notified)
    assert out["experiment"]["surfaced"] is True and out["recommended"] == 1
    assert notified, "owner must be notified for a genuinely better version"
    recs = env.list("recommendations", limit=10)
    assert len(recs) == 1 and recs[0]["type"] == "EXPERIMENT_REVIEW"
    assert recs[0]["never_auto_applied"] is True


def test_no_duplicate_recommendations(env, monkeypatch):
    _hyp(env); _exp(env, "e1", "IMPROVED", wf_consistent=True)
    notified = []
    _run_cycle(monkeypatch, env, notified)
    env.update("hypotheses", "h1", {"status": "AWAITING_APPROVAL"})
    _run_cycle(monkeypatch, env, notified)
    assert len(env.list("recommendations", limit=10)) == 1


def test_gate_unit_semantics():
    from app.learning.auto_loop import genuinely_better
    assert genuinely_better({"result": "IMPROVED"}) is True
    assert genuinely_better({"result": "IMPROVED", "overfitting_risk": True}) is False
    assert genuinely_better({"result": "IMPROVED", "small_sample_warning": True}) is False
    assert genuinely_better({"result": "IMPROVED",
                             "walk_forward": {"consistent": False}}) is False
    assert genuinely_better({"result": "IMPROVED",
                             "walk_forward": {"consistent": True}}) is True
    assert genuinely_better({"result": "WORSE"}) is False
    assert genuinely_better({"result": "NO_SIGNIFICANT_CHANGE"}) is False
    assert genuinely_better({"result": "INSUFFICIENT_DATA"}) is False
    assert genuinely_better(None) is False
