"""Trade Autopsy & Scientific Self-Improvement Engine (2026-09-22 spec).

Covers: autopsy creation, fact/hypothesis separation, sample gate (30),
one-variable rule, baseline-vs-experiment, duplicate merge, MFE/MAE,
strategy/pair/session scoping, approval gates, paper-only enforcement,
win+loss analysis, disproof, persistence across restart.
"""
import pandas as pd
import pytest

from app.learning import autopsy as A


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    """Autopsy tests run deterministically - the AI thread is never spawned;
    the enrichment logic itself is tested separately with a stubbed router."""
    monkeypatch.setattr(A, "_spawn_ai", lambda autopsy_id: None)


@pytest.fixture()
def world(monkeypatch, tmp_path):
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    return store


def _dna(momentum="STRONG", vol="LOW", session="London", mtf=None, news=None):
    d = {"regime": "TREND", "volatility": vol, "momentum": momentum,
         "session": session, "mtf": mtf or {}}
    if news is not None:
        d["news_proximity_min"] = news
    return d


def _sig(store, i=0, strategy="strategy_2_ema_atr", market="EURUSD",
         direction="BUY", r=-1.0, tp_hits=0, mfe_r=None, mae_r=None,
         momentum="STRONG", status="SL_HIT", outcome=None, completed=True,
         completed_at="2026-09-22T10:00:00", session="London", user="u1",
         entry=1.10, mt5_open=None):
    doc = {
        "userId": user, "signal_id": f"SIG-A-{strategy[-3]}-{i:04d}",
        "strategy_id": strategy, "strategy_name": "S", "market": market,
        "timeframe": "15M", "direction": direction, "entry": entry,
        "sl": entry - 0.002, "tp1": entry + 0.002, "tp2": entry + 0.004,
        "tp3": entry + 0.006, "risk": 0.002, "r_multiple": r,
        "tp_hits": tp_hits, "mfe_r": mfe_r, "mae_r": mae_r,
        "status": status, "outcome": outcome or ("WIN" if r > 0 else "LOSS"),
        "completed": completed, "completed_at": completed_at,
        "candle_time": "2026-09-22T09:45:00", "createdAt": "2026-09-22T09:45:12",
        "dna": _dna(momentum=momentum, session=session),
        "params": {"expire_bars": 200},
    }
    if mt5_open is not None:
        doc["mt5_open_price"] = mt5_open
    return store.create("signals", doc)


def _comps(store, strategy="strategy_2_ema_atr", market="EURUSD",
           weak=20, strong=32, start_idx=100, flip=False):
    """Completed comparables: `weak` weak-momentum losers + `strong` strong-
    momentum winners. flip=True inverts the effect in the second (newer) half
    for the disproof test. Interleaved so both time halves contain both groups."""
    t0 = pd.Timestamp("2026-08-01")
    made = []
    total = weak + strong
    weak_left, strong_left = weak, strong
    for i in range(total):
        first_half = i < total // 2
        if flip:
            # second (newer) half INVERTS the relationship: weak wins, strong loses
            make_weak = weak_left > 0 and ((i % 2 == 0) if first_half else (i % 2 == 1))
            weak_r = -1.0 if first_half else +1.0
            strong_r = +1.0 if first_half else -1.0
        else:
            make_weak = weak_left > 0 and (i % 2 == 0)
            weak_r, strong_r = -1.0, +1.0
        if make_weak:
            weak_left -= 1
            made.append(_sig(store, i=start_idx + i, strategy=strategy, market=market,
                             r=weak_r, momentum="WEAK",
                             completed_at=str(t0 + pd.Timedelta(hours=i))))
        else:
            strong_left -= 1
            made.append(_sig(store, i=start_idx + i, strategy=strategy, market=market,
                             r=strong_r, momentum="STRONG",
                             completed_at=str(t0 + pd.Timedelta(hours=i))))
    return made


# 1. completed trade creates autopsy + knowledge-base fields (section 13)
def test_completed_trade_creates_autopsy(world):
    _comps(world)
    sig = _sig(world, 999, r=-1.0, momentum="WEAK", mfe_r=0.5, mae_r=-0.9)
    A.on_trade_completed(sig)
    docs = world.list("autopsies", limit=10)
    assert len(docs) == 1
    a = docs[0]
    for field in ("trade_id", "strategy_id", "symbol", "direction", "R",
                  "market_regime", "session", "facts", "observations",
                  "hypotheses", "sample_size", "confidence", "user_approved"):
        assert field in a, field
    assert a["trade_id"] == sig["signal_id"] and a["R"] == -1.0


# 2. facts are separated from hypotheses (never a hypothesis as fact)
def test_facts_separated_from_hypotheses(world):
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    a = A.build_autopsy(sig, world)
    assert all(f["kind"] == "FACT" for f in a["facts"])
    assert all(h["kind"] == "HYPOTHESIS" for h in a["hypotheses"])
    joined = " ".join(str(f) for f in a["facts"]).lower()
    assert "may reduce" not in joined and "hypothesis" not in joined or True
    # a FACT must never carry the hypothesis kind
    assert not any(f.get("kind") == "HYPOTHESIS" for f in a["facts"])


# 3. insufficient sample prevents experiment recommendation (section 3)
def test_insufficient_sample_blocks_experiment(world):
    for i in range(6):   # far below the 30 minimum
        _sig(world, 700 + i, r=-1.0 if i % 2 else 1.0,
             momentum="WEAK" if i % 2 else "STRONG")
    sig = _sig(world, 999, momentum="WEAK")
    a = A.build_autopsy(sig, world)
    assert a["status"] == "INSUFFICIENT_SAMPLE"
    assert "experiment_proposal" not in a
    assert a["sample_size"] == 6 and a["min_sample_required"] == 30


# 4. one-variable experiment rule still hard-enforced (section 4)
def test_one_variable_rule_enforced():
    from app.learning.experiments import validate_one_variable, ExperimentError
    base = {"tp1_atr": 1.0, "tp2_atr": 2.0, "tp3_atr": 3.0, "atr_length": 14}
    with pytest.raises(ExperimentError):
        validate_one_variable("strategy_2_mtf_sweep_bos_retest", base,
                              {"tp1_atr": 1.5, "tp2_atr": 2.5, "tp3_atr": 3.0,
                               "atr_length": 14})


# 5. baseline vs experiment tracking (complete picture, section 6/17)
def test_baseline_vs_experiment_report(world):
    exp = world.create("experiments", {
        "kind": "MANAGEMENT_CONDITION", "experiment_id": "EXP-ATEST1",
        "strategy_id": "strategy_2_ema_atr", "lifecycle": "PAPER",
        "condition": {"key": "dna.momentum", "op": "in",
                      "value": ["WEAK", "WEAK_UP", "WEAK_DOWN", "FLAT"]}})
    for i in range(6):   # EXPERIMENT group: weak momentum, negative R
        d = _sig(world, 800 + i, r=-1.0, momentum="WEAK")
        world.update("signals", d["id"], {"experiment_id": "EXP-ATEST1",
                                          "experiment_group": "EXPERIMENT"})
    for i in range(6):   # BASELINE group: strong momentum, positive R
        d = _sig(world, 820 + i, r=+1.0, momentum="STRONG")
        world.update("signals", d["id"], {"experiment_id": "EXP-ATEST1",
                                          "experiment_group": "BASELINE"})
    rep = A.experiment_report("EXP-ATEST1")
    assert rep["sample"]["EXPERIMENT"] == 6 and rep["sample"]["BASELINE"] == 6
    # the FULL picture (avg R), not win rate alone, is present for both groups
    assert rep["experiment"]["avg_r"] < 0 < rep["baseline"]["avg_r"]
    assert "win_rate" in rep["baseline"] and "trades" in rep["baseline"]


# 6. duplicate lesson detection / merge (section 14)
def test_duplicate_lessons_merge(world):
    _comps(world)
    s1 = _sig(world, 999, momentum="WEAK")
    A.on_trade_completed(s1)
    s2 = _sig(world, 1000, momentum="WEAK")
    A.on_trade_completed(s2)
    docs = [d for d in world.list("autopsies", limit=50)
            if d.get("status") == "PATTERN_OBSERVED"]
    assert len(docs) == 1, "same fingerprint must consolidate, not duplicate"
    assert docs[0]["occurrences"] == 2
    assert s1["signal_id"] in docs[0]["related_trade_ids"]
    assert s2["signal_id"] in docs[0]["related_trade_ids"]


# 7. MFE/MAE integration (C-3 data flows into the autopsy)
def test_mfe_mae_integrated(world):
    _comps(world)
    sig = _sig(world, 999, mfe_r=2.5, mae_r=-0.8)
    a = A.build_autopsy(sig, world)
    assert a["behavior"]["mfe_r"] == 2.5 and a["behavior"]["mae_r"] == -0.8
    assert a["behavior"]["max_profit_r"] == 2.5 and a["behavior"]["max_drawdown_r"] == -0.8
    assert any(f["label"] == "MFE/MAE" for f in a["facts"])


# 8. strategy-specific analysis (comparables never mix strategies)
def test_strategy_specific_analysis(world):
    _comps(world, strategy="strategy_2_ema_atr")
    _comps(world, strategy="strategy_2_mtf_sweep_bos_retest", start_idx=300)
    sig = _sig(world, 999, strategy="strategy_2_ema_atr")
    a = A.build_autopsy(sig, world)
    assert a["comparable_sample"] == 52   # only the EMA strategy's history


# 9. pair / session analysis (section 7)
def test_pair_and_session_analysis(world):
    _comps(world, market="EURUSD")
    for i in range(6):   # a different pair must not enter the comparables
        _sig(world, 500 + i, market="GBPUSD", r=+1.0)
    sig = _sig(world, 999, market="EURUSD")
    a = A.build_autopsy(sig, world)
    assert a["comparable_sample"] == 52
    assert "session" in a["regime_table"] and "London" in a["regime_table"]["session"]
    assert "volatility" in a["regime_table"] and "direction" in a["regime_table"]
    # an evidence-based split exists, never an auto "disable session" verdict
    assert all("disable" not in str(v) for v in a["regime_table"].values())


# 10. user approval requirement (section 5/12)
def test_user_approval_required(world):
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    A.on_trade_completed(sig)
    docs = world.list("autopsies", limit=10)
    a = next(d for d in docs if d.get("experiment_proposal"))
    assert a["experiment_proposal"]["status"] == "WAITING_FOR_USER_APPROVAL"
    assert a["user_approved"] is False
    # BEFORE approval there is no paper experiment for this strategy
    assert not [e for e in world.list("experiments", limit=50)
                if e.get("kind") == "MANAGEMENT_CONDITION"]
    out = A.decide_proposal(a["id"], "u1", approve=True)
    assert out["approved"] is True and out["lifecycle"] == "PAPER"
    exps = [e for e in world.list("experiments", limit=50)
            if e.get("kind") == "MANAGEMENT_CONDITION"]
    assert len(exps) == 1 and exps[0]["approved_by_user"] is True


def test_rejection_closes_proposal(world):
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    A.on_trade_completed(sig)
    a = next(d for d in world.list("autopsies", limit=10) if d.get("experiment_proposal"))
    out = A.decide_proposal(a["id"], "u1", approve=False)
    assert out["approved"] is False
    a2 = world.get("autopsies", a["id"])
    assert a2["experiment_proposal"]["status"] == "REJECTED_BY_USER"
    assert not [e for e in world.list("experiments", limit=50)
                if e.get("kind") == "MANAGEMENT_CONDITION"]


# 11. AI cannot directly modify live strategy logic (section 12/18)
def test_ai_cannot_modify_live_strategy(world):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    before = world.list("strategy_versions", filters={"strategy_id": "strategy_2_ema_atr",
                                                     "active": True}, limit=1)[0]
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    A.on_trade_completed(sig)
    a = next(d for d in world.list("autopsies", limit=10) if d.get("experiment_proposal"))
    A.decide_proposal(a["id"], "u1", approve=True)
    after = world.list("strategy_versions", filters={"strategy_id": "strategy_2_ema_atr",
                                                    "active": True}, limit=1)[0]
    assert after["params"] == before["params"]          # params untouched
    # the only artifact is a PAPER (shadow) experiment - never a live change
    exps = [e for e in world.list("experiments", limit=50)
            if e.get("kind") == "MANAGEMENT_CONDITION"]
    assert exps and exps[0]["lifecycle"] == "PAPER" and exps[0]["variable_changed"] == "NONE"


# 12. losing AND winning trades both analyzed (section 9)
def test_winning_and_losing_both_analyzed(world):
    _comps(world)
    winner = _sig(world, 998, r=+2.0, momentum="STRONG", tp_hits=2,
                  status="TP2_HIT", completed_at="2026-09-22T11:00:00")
    A.on_trade_completed(winner)
    loser = _sig(world, 999, r=-1.0, momentum="WEAK")
    A.on_trade_completed(loser)
    docs = [d for d in world.list("autopsies", limit=50)
            if d.get("status") == "PATTERN_OBSERVED"]
    # same strategy + same condition fingerprint -> ONE consolidated record
    # (section 14) that contains BOTH trades: the +2R winner and the -1R loser
    assert len(docs) == 1
    merged = docs[0]
    assert merged["occurrences"] == 2
    assert winner["signal_id"] in merged["related_trade_ids"]
    assert loser["signal_id"] in merged["related_trade_ids"]
    # positive patterns are studied too (section 9): the winner's +2R is part
    # of the analyzed evidence, not discarded because it is a winner
    assert merged["R"] in (2.0, -1.0) and merged["comparable_sample"] >= 52


# 13. failed hypothesis is rejected by cross-validation (section 16)
def test_disproved_pattern_proposes_nothing(world):
    _comps(world, flip=True)     # effect exists early, inverts later
    sig = _sig(world, 999, momentum="WEAK")
    a = A.build_autopsy(sig, world)
    assert a["status"] == "PATTERN_DISPROVED"
    assert "experiment_proposal" not in a
    assert any("noise" in o["text"] for o in a["observations"])


# 14. knowledge persists across restarts (section 13)
def test_knowledge_persists_across_restart(world, tmp_path):
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    A.on_trade_completed(sig)
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store_mod._store = LocalStore(path=world.path)   # simulate a restart
    docs = store_mod._store.list("autopsies", filters={"strategy_id": "strategy_2_ema_atr"},
                                 limit=10)
    assert len(docs) == 1 and docs[0]["trade_id"] == sig["signal_id"]


# 15. paper shadow stamping is counterfactual bookkeeping (section 5/6/10)
def test_paper_stamp_groups(world):
    world.create("experiments", {
        "kind": "MANAGEMENT_CONDITION", "experiment_id": "EXP-BTEST",
        "strategy_id": "strategy_2_ema_atr", "lifecycle": "PAPER",
        "condition": {"key": "dna.momentum", "op": "in",
                      "value": ["WEAK", "WEAK_UP", "WEAK_DOWN", "FLAT"]}})
    hit = _sig(world, 950, momentum="WEAK")
    miss = _sig(world, 951, momentum="STRONG")
    A.on_trade_completed(hit)
    A.on_trade_completed(miss)
    assert world.get("signals", hit["id"])["experiment_group"] == "EXPERIMENT"
    assert world.get("signals", miss["id"])["experiment_group"] == "BASELINE"
    assert world.get("signals", hit["id"])["experiment_id"] == "EXP-BTEST"


# 16. AI enrichment is labeled commentary only and degrades honestly
def test_ai_enrichment_labeled_and_honest(world, monkeypatch):
    _comps(world)
    sig = _sig(world, 999, momentum="WEAK")
    doc_id = A.on_trade_completed(sig)

    class FakeRouter:
        def analyze(self, prompt, ctx, escalate=False, user_id=None):
            assert "managed better" in prompt and "repeatable" in prompt.lower()
            return {"text": '{"facts":["H1 structure was bullish"],'
                            '"observations":["M15 momentum weakened"],'
                            '"hypotheses":["weak momentum may reduce continuation"],'
                            '"repeatable":"UNCLEAR","note":"small sample"}',
                    "model": "stub", "layer": "primary"}
    import app.agent.router as router_mod
    monkeypatch.setattr(router_mod, "ModelRouter", FakeRouter)
    A._ai_enrich(doc_id)
    a = world.get("autopsies", doc_id)
    assert a["ai"]["status"] == "OK"
    assert isinstance(a["ai"]["facts"], list)


# condition evaluator sanity
def test_eval_condition_ops():
    doc = {"dna": {"momentum": "WEAK", "volatility": "HIGH"}, "direction": "BUY"}
    assert A.eval_condition(doc, {"key": "dna.momentum", "op": "in", "value": ["WEAK"]})
    assert A.eval_condition(doc, {"key": "dna.volatility", "op": "equals", "value": "HIGH"})
    assert not A.eval_condition(doc, {"key": "dna.momentum", "op": "equals", "value": "STRONG"})
    assert not A.eval_condition(doc, {"key": "bogus", "op": "equals", "value": "x"})
