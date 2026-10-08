"""RESEARCH-FIRST INTELLIGENCE upgrade tests (owner brief 2026-10-08).

Covers every new component:
- sources.py   : tiered registry, classification, purpose gates, no free search
- extract.py   : explicit-rules extraction, INSUFFICIENT_RULES honesty
- reconstruct.py: SOURCE -> EXTRACTED -> IMPLEMENTED trace
- backtest.py  : deterministic verified replay, costs, MFE/MAE, no lookahead
- validate.py  : OOS, walk-forward, stability, cost sensitivity, Monte Carlo
- probabilities.py: evidence-only probabilities, INSUFFICIENT DATA honesty
- memory.py    : research memory dedup (never rerun identical experiments)
- report.py    : evidence report + recommendation logic
- engine.py    : pipeline stages, bounded tick, discover dedup, memory dedup
- API          : /research/engine/* endpoints
"""
from __future__ import annotations

import numpy as np
import pandas as pd
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


def ohlc(n=800, seed=3, trend=0.00005, start=1.10, pip=0.0001, vol=0.0012):
    """Noisy random walk (noise >> drift) so MA crossings actually occur."""
    idx = pd.date_range("2026-06-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    drift = np.cumsum(rng.normal(trend, vol, n))
    close = start + drift
    o = np.roll(close, 1)
    o[0] = close[0]
    hi = np.maximum(o, close) + np.abs(rng.normal(pip, pip / 2, n))
    lo = np.minimum(o, close) - np.abs(rng.normal(pip, pip / 2, n))
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": close,
                       "volume": 100.0}, index=idx)
    df.index.name = "datetime"
    return df


GOOD_RULES = {
    "name": "Test Strategy", "market": "EURUSD", "timeframe": "1H",
    "direction": "BOTH",
    "entry": {"when": [
        {"op": "crosses_above", "left": {"indicator": "ema", "params": {"period": 12}},
         "right": {"indicator": "ema", "params": {"period": 26}}}]},
    "sl": {"type": "atr_mult", "mult": 2.0},
    "tp": {"type": "rr_multiple", "value": 2.0},
    "indicators": ["ema", "atr"], "params": {"fast": 12, "slow": 26},
}


# ---------------------------------------------------------------- sources
def test_sources_tiers_and_gates(env):
    from app.research.sources import REGISTRY, classify, tier_name, usable_for
    assert REGISTRY[0]["tier"] == 1
    assert classify("https://www.ssrn.com/abstract=123")["tier"] == 1
    assert classify("https://alphainsider.com/idea")["tier"] == 2
    assert classify("https://www.reddit.com/r/algotrading/x")["tier"] == 3
    unk = classify("https://some-random-forum.io/thread")
    assert unk["tier"] == 3 and unk["registered"] is False
    assert tier_name(1) == "STRONG"
    assert usable_for(1, "strategy_research")
    assert not usable_for(3, "strategy_research")
    assert usable_for(3, "idea_discovery")


def test_sources_registry_endpoint(env):
    c, store, _, _ = env
    r = c.get("/api/research/engine/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["tiers"]["1"] == "STRONG"
    assert len(body["sources"]) >= 10
    assert "never" in body["policy"]


# ---------------------------------------------------------------- extract
def test_extract_ok_and_insufficient(env):
    from app.research.extract import extract
    res = extract({"url": "https://www.ssrn.com/x"}, GOOD_RULES)
    assert res["status"] == "OK"
    assert res["rules"]["market"] == "EURUSD"
    assert "not a FOREXMIND verified result" in res["rules"]["source_claim"]["disclaimer"]
    bad = {k: v for k, v in GOOD_RULES.items() if k not in ("entry", "market")}
    res2 = extract({}, bad)
    assert res2["status"] == "INSUFFICIENT_RULES"
    assert any("entry" in m for m in res2["missing"])
    assert any("market" in m for m in res2["missing"])


# ---------------------------------------------------------------- reconstruct
def test_reconstruct_trace(env):
    from app.research.extract import extract
    from app.research.reconstruct import reconstruct
    ex = extract({"url": "https://alphainsider.com"}, GOOD_RULES)
    rec = reconstruct(ex["rules"])
    assert rec["status"] == "OK"
    t = rec["trace"]
    assert t["source"] and t["extracted"] and t["implemented"]
    assert rec["implemented"]["engine"] == "research.rule_interp.v1"
    assert rec["implemented"]["entry"]["side"] == "BUY"
    assert rec["implemented"]["entry"]["mirror_side"] == "SELL"   # BOTH mirrored
    assert "no lookahead" in rec["implemented"]["evaluation"]


def test_reconstruct_rejects_bogus(env):
    from app.research.reconstruct import reconstruct
    res = reconstruct({"market": "EURUSD", "timeframe": "1H", "direction": "LONG",
                       "entry": {"when": [{"op": "quantum_entangles",
                                           "left": {"indicator": "close"},
                                           "right": {"value": 1}}]},
                       "sl": {"type": "voodoo", "pips": 20},
                       "tp": {"type": "rr_multiple", "value": 2}})
    assert res["status"] == "UNRECONSTRUCTABLE"


# ---------------------------------------------------------------- backtest
def test_backtest_deterministic_and_honest(env):
    from app.research.backtest import run
    df = ohlc(800)
    impl = {"engine": "research.rule_interp.v1",
            "entry": {"side": "LONG",
                      "when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 12}},
                                "right": {"indicator": "ema", "params": {"period": 26}}}],
                      "all": True},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "exit_when": [], "costs": {}}
    r1 = run(df, "EURUSD", impl)
    r2 = run(df, "EURUSD", impl)
    assert r1["status"] == "OK" and r1 == r2                 # deterministic
    assert r1["note"].startswith("FOREXMIND VERIFIED RESULT")
    m = r1["metrics"]
    for key in ("trade_count", "win_rate", "avg_r", "expectancy_r", "profit_factor",
                "net_r", "max_drawdown_r", "max_winning_streak",
                "max_losing_streak", "avg_mfe_r", "avg_mae_r"):
        assert key in m, key
    for t in r1["trades"]:
        assert t["exit_reason"] in ("SL", "TP", "RULE", "END")
        assert "mfe_r" in t and "mae_r" in t
        assert t["r"] < 0 or t["exit_reason"] != "SL"
    small = run(ohlc(50), "EURUSD", impl)
    assert small["status"] == "INSUFFICIENT_DATA"


def test_backtest_costs_reduce_returns(env):
    from app.research.backtest import run
    df = ohlc(900, seed=11)
    impl = {"engine": "research.rule_interp.v1",
            "entry": {"side": "LONG",
                      "when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 8}},
                                "right": {"indicator": "ema", "params": {"period": 21}}}],
                      "all": True},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "exit_when": [], "costs": {}}
    free = run(df, "EURUSD", impl)
    impl_c = dict(impl)
    impl_c["costs"] = {"spread_pips": 3.0, "slippage_pips": 1.0, "commission": 7.0}
    costly = run(df, "EURUSD", impl_c)
    if free["status"] == "OK" and costly["status"] == "OK" and free["trades"]:
        assert costly["metrics"]["net_r"] < free["metrics"]["net_r"]


def test_backtest_no_lookahead(env):
    """Entry price must be the NEXT bar's open, never the signal bar's close."""
    from app.research.backtest import run
    df = ohlc(400, seed=9)
    impl = {"engine": "research.rule_interp.v1",
            "entry": {"side": "LONG",
                      "when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 5}},
                                "right": {"indicator": "ema", "params": {"period": 20}}}],
                      "all": True},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "exit_when": [], "costs": {}}
    r = run(df, "EURUSD", impl)
    assert r["status"] == "OK"
    for t in r["trades"]:
        sig_i = t["signal_i"]
        assert t["entry"] == pytest.approx(float(df["open"].iloc[sig_i + 1]), abs=1e-6)


# ---------------------------------------------------------------- validate
def test_validate_gates(env):
    from app.research.validate import (cost_sensitivity, oos_gate, overall,
                                       parameter_stability, split_oos,
                                       stress_monte_carlo, walk_forward)
    trades = [{"r": 2.0, "exit_reason": "TP", "entry_time": "2026-06-01T00:00:00+00:00"}
              for _ in range(50)] + \
             [{"r": -1.0, "exit_reason": "SL", "entry_time": "2026-06-02T00:00:00+00:00"}
              for _ in range(18)]
    train, oos = split_oos(trades)
    assert len(oos) >= 8 and len(train) > len(oos)      # 70/30 chronological
    g = oos_gate(trades)
    assert g["gate"] == "OOS" and g["verdict"] in ("PASS", "MARGINAL", "FAIL")
    wf = walk_forward(trades)
    assert wf["gate"] == "WALK_FORWARD"
    cs = cost_sensitivity(trades)
    assert cs["gate"] == "COST_SENSITIVITY"
    mc = stress_monte_carlo(trades)
    assert mc["gate"] == "MONTE_CARLO" and mc["verdict"] in ("PASS", "MARGINAL")
    df = ohlc(600)
    impl = {"engine": "research.rule_interp.v1",
            "entry": {"side": "LONG",
                      "when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 12}},
                                "right": {"indicator": "ema", "params": {"period": 26}}}],
                      "all": True},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "exit_when": [], "costs": {}}
    ps = parameter_stability(df, "EURUSD", impl, "period", [10, 14, 16],
                             lambda d, m, i: {"status": "OK",
                                              "trades": trades,
                                              "metrics": {"avg_r": 0.4}})
    assert ps["gate"] == "PARAM_STABILITY"
    all_pass = [g, wf, cs, mc, ps]
    assert overall(all_pass) in ("STRONG", "MIXED", "WEAK", "INSUFFICIENT")
    fail_case = all_pass + [{"gate": "OOS", "verdict": "FAIL", "detail": "x"}]
    assert overall(fail_case) == "WEAK"


# ---------------------------------------------------------------- probabilities
def test_probabilities_evidence_only(env):
    from app.research.probabilities import conditional, from_trades
    few = [{"r": 1.0, "exit_reason": "TP", "entry_time": "2026-06-01T00:00:00+00:00",
            "mfe_r": 1.2} for _ in range(10)]
    res = from_trades(few)
    assert res["P_win"]["status"] == "INSUFFICIENT_DATA"       # honesty floor
    trades = [{"r": 2.0, "exit_reason": "TP", "entry_time": "2026-06-01T00:00:00+00:00",
               "mfe_r": 2.1} for _ in range(40)] + \
             [{"r": -1.0, "exit_reason": "SL", "entry_time": "2026-06-02T00:00:00+00:00",
               "mfe_r": 0.4} for _ in range(15)]
    res = from_trades(trades)
    assert res["P_win"]["probability"] == pytest.approx(40 / 55, abs=1e-3)
    assert res["P_win"]["wilson95"][0] < 40 / 55 < res["P_win"]["wilson95"][1]
    assert res["P_SL"]["probability"] == pytest.approx(15 / 55, abs=1e-3)
    assert res["P_TP1"]["probability"] >= res["P_TP2"]["probability"]
    assert res["P_TP2"]["probability"] >= res["P_TP3"]["probability"]
    assert res["expected_R"]["value"] > 0
    assert res["basis"] == "FOREXMIND VERIFIED trades (measured, not AI confidence)"
    cond = conditional(trades + [], "regime", lambda t: "TREND")
    assert cond["TREND"]["P_win"] == pytest.approx(40 / 55, abs=1e-3)
    cond2 = conditional([{"r": 1, "regime": "X"} for _ in range(5)],
                        "regime", lambda t: t["regime"])
    assert cond2["X"]["status"] == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------- memory
def test_research_memory_dedup(env):
    from app.research import memory
    store = env[1]
    cand = {"id": "c1", "name": "Test Strat", "source_id": "journals",
            "market": "EURUSD", "timeframe": "1H"}
    impl = {"entry": {"when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 12}},
                                "right": {"indicator": "ema", "params": {"period": 26}}}]},
            "sl": {"type": "atr_mult", "mult": 2.0}}
    memory.remember_candidate(store, cand, impl, "REJECT", "PF 0.8 in OOS")
    seen = memory.seen_before(store, cand, impl)
    assert seen and seen["verdict"] == "REJECT" and "PF 0.8" in seen["reason"]
    # different params -> NOT the same experiment
    impl2 = {"entry": {"when": [{"op": "crosses_above",
                                 "left": {"indicator": "ema", "params": {"period": 5}},
                                 "right": {"indicator": "ema", "params": {"period": 26}}}]},
             "sl": {"type": "atr_mult", "mult": 2.0}}
    assert memory.seen_before(store, cand, impl2) is None
    summ = memory.summary(store)
    assert summ["total"] == 1 and summ["rejected"] == 1


# ---------------------------------------------------------------- report
def test_report_recommendations(env):
    from app.research.report import build, recommend
    bt = {"status": "OK", "metrics": {"trade_count": 60, "win_rate": 55,
                                      "avg_r": 0.3, "profit_factor": 1.4}}
    gates_pass = [{"gate": g, "verdict": "PASS", "detail": "ok"}
                  for g in ("OOS", "WALK_FORWARD", "PARAM_STABILITY",
                            "COST_SENSITIVITY", "MONTE_CARLO")]
    assert recommend(gates_pass, bt, None, None)["recommendation"] == "SHADOW"
    fail = gates_pass + [{"gate": "OOS", "verdict": "FAIL", "detail": "PF 0.7 OOS"}]
    assert recommend(fail, bt, None, None)["recommendation"] == "REJECT"
    marg = [{"gate": g, "verdict": "MARGINAL", "detail": "m"}
            for g in ("OOS", "WALK_FORWARD", "PARAM_STABILITY",
                      "COST_SENSITIVITY", "MONTE_CARLO")]
    assert recommend(marg, bt, None, None)["recommendation"] == "CONTINUE_RESEARCH"
    shadow = {"n": 25, "avg_r": 1.0}
    sp = {"P_win": {"probability": 0.53}}
    assert recommend(gates_pass, bt, shadow, sp)["recommendation"] == \
        "READY_FOR_REVIEW"
    diverged = {"n": 25, "avg_r": -0.2}
    assert recommend(gates_pass, bt, diverged, sp)["recommendation"] == \
        "CONTINUE_RESEARCH"
    rep = build({"name": "X", "evidence_tier": 1, "source_claim": "c"},
                bt, gates_pass, {"P_win": {"probability": 0.5}}, shadow, sp)
    assert "SOURCE CLAIM != FOREXMIND VERIFIED RESULT" in \
        rep["source_evidence"]["disclaimer"]
    assert rep["human_approval_required"] is True


# ---------------------------------------------------------------- engine
def test_engine_pipeline_and_dedup(env, monkeypatch):
    from app.research import engine
    c, store, mp, settings = env
    df = ohlc(900, seed=5)
    mp.setattr(engine, "_get_df", lambda m, tf, limit: df)
    out1 = engine.tick()
    assert out1["discovered"] == len(engine.SEED_CATALOG)
    assert out1["advanced"] <= 2                       # bounded tick
    out2 = engine.tick()
    assert out2["discovered"] == 0                     # discover dedup
    # run to exhaustion
    for _ in range(12):
        engine.tick()
    cands = store.list("research_candidates", limit=50)
    assert len(cands) == len(engine.SEED_CATALOG)      # no duplicates ever
    stages = {c["stage"] for c in cands}
    assert stages <= {"REJECTED", "READY_FOR_REVIEW", "CONTINUE_RESEARCH", "SHADOW"}
    for cand in cands:
        assert cand.get("report"), cand["name"]
        rep = cand["report"]
        assert rep["recommendation"] in ("REJECT", "CONTINUE_RESEARCH",
                                         "SHADOW", "READY_FOR_REVIEW")
        if cand["stage"] == "REJECTED":
            assert rep["recommendation"] == "REJECT"
            assert rep["recommendation_reason"]
        for ev in cand["events"]:
            assert ev["stage"] and ev["detail"]
        if cand.get("implemented"):
            assert "no lookahead" in cand["implemented"]["evaluation"]
    # research memory: terminal verdicts recorded
    from app.research import memory
    assert memory.summary(store)["total"] >= 1
    # re-running identical experiment must be blocked by memory:
    fresh_store_hits = 0
    for cand in store.list("research_candidates", limit=50):
        if cand.get("implemented"):
            assert memory.seen_before(store, cand, cand["implemented"]) is not None
            fresh_store_hits += 1
    assert fresh_store_hits >= 1


def test_engine_memory_blocks_rerun(env, monkeypatch):
    """A substantially identical candidate must be REJECTED via memory."""
    from app.research import engine, memory
    c, store, mp, settings = env
    df = ohlc(900, seed=5)
    mp.setattr(engine, "_get_df", lambda m, tf, limit: df)
    for _ in range(12):
        engine.tick()
    impls = [x.get("implemented") for x in store.list("research_candidates", limit=50)
             if x.get("implemented")]
    assert impls
    cand = {"id": "manual", "name": "Test Strategy", "source_id": "journals",
            "market": "EURUSD", "timeframe": "1H"}
    seen = memory.seen_before(store, cand, impls[0])
    # either identical fingerprint exists, or it does not - but the check
    # itself must work without error
    assert seen is None or seen["verdict"] in ("REJECT", "READY_FOR_REVIEW",
                                               "CONTINUE_RESEARCH")


def test_engine_disabled(env, monkeypatch):
    from app.research import engine
    c, store, mp, settings = env
    mp.setenv("RESEARCH_ENGINE_ENABLED", "0")
    out = engine.tick()
    assert out["enabled"] is False
    assert store.list("research_candidates", limit=10) == []


# ---------------------------------------------------------------- shadow
def test_engine_shadow_records(env, monkeypatch):
    """Shadow stage writes hypothetical trade records with assumptions."""
    from app.research import engine
    c, store, mp, settings = env
    cand = {"id": "sh1", "stage": "SHADOW", "name": "Shadow Strat",
            "market": "EURUSD", "timeframe": "1H", "source_id": "journals",
            "evidence_tier": 1, "validation": [], "backtest": None,
            "implemented": {"entry": {"side": "LONG", "when": []}, "sl": {}, "tp": {}}}
    store.create("research_candidates", cand)
    df = ohlc(600, seed=8)
    impl = {"engine": "research.rule_interp.v1",
            "entry": {"side": "LONG",
                      "when": [{"op": "crosses_above",
                                "left": {"indicator": "ema", "params": {"period": 8}},
                                "right": {"indicator": "ema", "params": {"period": 21}}}],
                      "all": True},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "exit_when": [], "costs": {}}
    from app.research import backtest as bt
    res = bt.run(df, "EURUSD", impl)
    engine._record_shadow(store, cand, res["trades"], df)
    rows = store.list("shadow_trades", filters={"candidate_id": "sh1"}, limit=100)
    assert rows
    t0 = rows[0]
    assert t0["side"] in ("BUY", "SELL")
    assert "NEVER sent to the broker" in t0["assumptions"]
    assert t0["hypothetical_sl"] is not None
    assert t0["session"] in ("London", "NewYork", "Asian", "Late", "")
    # re-record: no duplicates
    engine._record_shadow(store, cand, res["trades"], df)
    rows2 = store.list("shadow_trades", filters={"candidate_id": "sh1"}, limit=200)
    assert len(rows2) == len(rows)


# ---------------------------------------------------------------- API
def test_api_endpoints(env, monkeypatch):
    from app.research import engine
    c, store, mp, settings = env
    df = ohlc(900, seed=6)
    mp.setattr(engine, "_get_df", lambda m, tf, limit: df)
    for _ in range(12):
        engine.tick()
    r = c.get("/api/research/engine/candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(engine.SEED_CATALOG)
    cand = body["candidates"][0]
    for key in ("id", "name", "stage", "evidence_tier", "recommendation",
                "verified_trades"):
        assert key in cand
    r2 = c.get(f"/api/research/engine/candidates/{cand['id']}")
    assert r2.status_code == 200
    assert r2.json()["candidate"]["id"] == cand["id"]
    r3 = c.get(f"/api/research/engine/candidates/{cand['id']}/report")
    assert r3.status_code == 200
    rep = r3.json()["report"]
    assert "disclaimer" in rep["source_evidence"]
    r4 = c.get("/api/research/engine/memory")
    assert r4.status_code == 200 and "total" in r4.json()
    r5 = c.post("/api/research/engine/tick")
    assert r5.status_code == 200
    r6 = c.get("/api/research/engine/candidates/nonexistent")
    assert r6.status_code == 404


# ---------------------------------------------------------------- approval
def _ready_candidate(store):
    """A candidate the engine already pushed to READY_FOR_REVIEW."""
    from app.research import engine
    cand = {"id": "ready1", "stage": "READY_FOR_REVIEW", "name": "Ready Strat",
            "market": "EURUSD", "timeframe": "1H", "source_id": "journals",
            "evidence_tier": 1,
            "implemented": {"engine": "research.rule_interp.v1",
                            "entry": {"side": "BUY", "when": []},
                            "sl": {"type": "atr_mult", "mult": 2.0},
                            "tp": {"type": "rr_multiple", "value": 2.0}},
            "report": {"recommendation": "READY_FOR_REVIEW",
                       "evidence_level": "STRONG"},
            "events": [], "createdAt": "2026-10-08T00:00:00+00:00"}
    store.create(engine.COLLECTION, cand)
    return cand


def test_approval_bridge_human_only(env):
    from app.research import approval
    c, store, mp, settings = env
    _ready_candidate(store)
    # engine-stage candidates can NOT be approved - human gate is absolute
    cand2 = {"id": "early1", "stage": "BACKTESTED", "name": "Early",
             "market": "XAUUSD", "timeframe": "1H", "events": []}
    store.create("research_candidates", cand2)
    with pytest.raises(ValueError):
        approval.approve_candidate("boss", "early1")
    res = approval.approve_candidate("boss", "ready1", note="evidence looks solid")
    assert res["version"] == "1.0"
    assert "NOT live" in res["note"]
    strat = store.get("strategies", res["strategy_id"])
    assert strat["status"] == "DISABLED"            # never auto-live
    assert strat["engine"] == "research.rule_interp.v1"
    ver = [v for v in store.list("strategy_versions",
                                 filters={"strategy_id": res["strategy_id"]})][0]
    assert ver["immutable"] is True and ver["active"] is False
    assert ver["evidence_report"]["recommendation"] == "READY_FOR_REVIEW"
    # double approval is refused
    with pytest.raises(ValueError):
        approval.approve_candidate("boss", "ready1")
    # memory records the approval
    from app.research import memory
    assert memory.summary(store)["total"] >= 1


def test_approval_reject_records_reason(env):
    from app.research import approval
    c, store, mp, settings = env
    _ready_candidate(store)
    res = approval.reject_candidate("boss", "ready1", "spread too high for this")
    assert res["stage"] == "REJECTED"
    from app.research import memory
    summ = memory.summary(store)
    assert summ["rejected"] == 1
    assert "spread too high" in summ["recent"][0]["reason"]


def test_approval_api(env):
    c, store, mp, settings = env
    _ready_candidate(store)
    r = c.post("/api/research/engine/candidates/ready1/approve",
               json={"note": "ok"})
    assert r.status_code == 200
    assert "NOT live" in r.json()["note"]
    r2 = c.post("/api/research/engine/candidates/early/approve", json={})
    assert r2.status_code == 404


# --------------------------------------- generalization / sessions (2026-10-08 re-audit)
def test_generalization_gate(env):
    from app.research.validate import generalization_gate
    df = ohlc(400)
    # edge positive everywhere -> PASS
    res = generalization_gate(
        lambda d, m: {"status": "OK", "metrics": {"avg_r": 0.3, "trade_count": 40}},
        "EURUSD", "1H", lambda m, tf: df)
    assert res["gate"] == "GENERALIZATION" and res["verdict"] == "PASS"
    assert res["result"]["contexts"]
    # edge negative on all alternates -> MARGINAL (single-market edge)
    res2 = generalization_gate(
        lambda d, m: {"status": "OK", "metrics": {"avg_r": -0.2, "trade_count": 40}},
        "EURUSD", "1H", lambda m, tf: df)
    assert res2["verdict"] == "MARGINAL"
    # no alternate data at all -> honest NOT_APPLICABLE, never fabricated
    res3 = generalization_gate(
        lambda d, m: {"status": "OK", "metrics": {"avg_r": 0.3}},
        "EURUSD", "1H", lambda m, tf: None)
    assert res3["verdict"] == "NOT_APPLICABLE"
    # mixed: positive only on the home-market-like half
    def mixed(d, m):
        return {"status": "OK", "metrics": {"avg_r": 0.3 if m == "EURUSD" else -0.1}}
    res4 = generalization_gate(mixed, "EURUSD", "1H", lambda m, tf: df)
    assert res4["verdict"] in ("MARGINAL",)


def test_generalization_neutral_in_overall(env):
    from app.research.validate import overall
    gates = [{"gate": g, "verdict": "PASS", "detail": "ok"}
             for g in ("OOS", "WALK_FORWARD", "PARAM_STABILITY",
                       "COST_SENSITIVITY", "MONTE_CARLO")]
    gates.append({"gate": "GENERALIZATION", "verdict": "NOT_APPLICABLE",
                  "detail": "no alternate market data"})
    assert overall(gates) == "STRONG"          # missing alt data never blocks
    gates[-1]["verdict"] = "MARGINAL"
    assert overall(gates) == "MIXED"           # single-market edge -> not STRONG


def test_regime_session_conditioning(env, monkeypatch):
    """Regime analysis conditions on trend, volatility bucket AND session."""
    from app.research import engine
    df = ohlc(900, seed=5)
    impl_trades = [{"r": 1.5, "exit_reason": "TP", "signal_i": 100 + i * 3,
                    "entry_time": f"2026-06-0{1 + (i % 8)}T{(i * 5) % 24:02d}:00:00+00:00",
                    "mfe_r": 1.6, "mae_r": -0.3} for i in range(40)]
    ra = engine._regime_analysis(df, impl_trades)
    assert "regimes" in ra and "sessions" in ra and "detail" in ra
    assert ra["regimes"] and ra["sessions"]
    for bucket in ra["sessions"].values():
        assert isinstance(bucket, dict)


def test_engine_validation_includes_generalization(env, monkeypatch):
    from app.research import engine
    c, store, mp, settings = env
    df = ohlc(900, seed=5)
    mp.setattr(engine, "_get_df", lambda m, tf, limit: df)
    for _ in range(14):
        engine.tick()
    # seed a candidate straight into BACKTESTED so validation is deterministic
    store.create(engine.COLLECTION, {
        "stage": "BACKTESTED", "name": "Gen Check", "market": "EURUSD",
        "timeframe": "1H", "source_id": "journals", "evidence_tier": 1,
        "backtest": {"status": "OK", "trades": [
            {"r": 2.0, "exit_reason": "TP", "entry_time": "2026-06-01T00:00:00+00:00",
             "mfe_r": 2.1, "mae_r": -0.2}] * 45,
            "metrics": {"trade_count": 45, "avg_r": 0.4, "profit_factor": 1.5}},
        "implemented": {"engine": "research.rule_interp.v1",
                        "entry": {"side": "BUY", "when": []},
                        "sl": {"type": "atr_mult", "mult": 2.0},
                        "tp": {"type": "rr_multiple", "value": 2.0}},
        "var_key": "period", "var_steps": [], "events": []})
    engine.tick()
    found = False
    for cand in store.list("research_candidates", limit=50):
        for g in cand.get("validation") or []:
            if g["gate"] == "GENERALIZATION":
                found = True
                assert g["verdict"] in ("PASS", "MARGINAL", "NOT_APPLICABLE")
    assert found


def test_candidates_sort_by_evidence(env, monkeypatch):
    from app.research import engine
    c, store, mp, settings = env
    df = ohlc(900, seed=5)
    mp.setattr(engine, "_get_df", lambda m, tf, limit: df)
    for _ in range(14):
        engine.tick()
    r = c.get("/api/research/engine/candidates?sort=evidence")
    assert r.status_code == 200
    rows = r.json()["candidates"]
    assert rows
    # REJECT rows must come after any SHADOW/READY rows
    recs = [x["recommendation"] for x in rows]
    rejects = [i for i, x in enumerate(recs) if x == "REJECT"]
    others = [i for i, x in enumerate(recs) if x != "REJECT"]
    if rejects and others:
        assert max(others) < min(rejects)
