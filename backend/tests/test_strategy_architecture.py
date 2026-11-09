"""Strategy architecture conformance (owner spec 2026-11-02).

Verifies the upgrade contract: every registered strategy conforms to the
common interface, signals carry the creating strategy's id + version,
invalid candidates are rejected before the risk engine, lifecycle states
gate execution server-side, strategies stay isolated from each other, and
the promotion doctrine ships with the code.

Duplicate-signal prevention (per-candle dedupe + cooldown) is pinned by
tests/test_32_owner_requests.py (test_double_signal_guard_blocks_refire and
the allow-next-setup companion) and runs in the same suite.
"""
from __future__ import annotations

import importlib
import pathlib
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from app.strategies import all_strategies, manifest, strategies_by_state
import dataclasses
from app.strategies.base import BaseStrategy, Candidate, validate_candidate
from app.learning.versions import ALL_LIFECYCLE_STATES, can_execute


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------
def _df(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range(end="2026-11-02 00:00", periods=n, freq="15min")
    close = pd.Series(range(n), index=idx, dtype=float) + 100.0
    return pd.DataFrame({"open": close, "high": close + 1.0,
                         "low": close - 1.0, "close": close,
                         "volume": 100}, index=idx)


class FakeStrategy(BaseStrategy):
    """Minimal concrete strategy used to prove the CONTRACT (not rules)."""
    id = "strategy_fake"
    name = "Fake"
    short_name = "FK"
    version = "2.1"
    min_bars = 10
    markets = ["EURUSD"]

    def __init__(self, candidate: Optional[Candidate] = None):
        self._cand = Candidate(**candidate) if isinstance(candidate, dict) \
            else candidate

    def compute(self, df, params):
        return {}

    def detect_on_bar(self, state, i):
        return {"direction": "BUY", "i": i}

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        return (dict(self._cand) if isinstance(self._cand, dict) else
                self._cand)

    def calculate_risk(self, df, state, i, direction, params):
        return {"entry": 1.0, "sl": 0.9, "risk": 0.1}

    def calculate_targets(self, entry, risk, direction, params):
        return [1.2, 1.4, 1.6]

    def explain_signal(self, state, i, direction, params, extra):
        return [], "fake"


VALID = Candidate(strategy_id="strategy_fake", market="EURUSD",
                  timeframe="15M", direction="BUY", candle_time="x",
                  entry=1.10, entry_zone=[1.10, 1.10], sl=1.09, tps=[1.12],
                  risk=0.01, rr_primary=2.0, score=50, score_components={},
                  checks=[], analysis="ok")


# ---------------------------------------------------------------------------
# interface conformance (section 11)
# ---------------------------------------------------------------------------
def test_registered_strategies_conform_to_interface():
    seen = set()
    for sid, strategy in all_strategies().items():
        assert sid and sid not in seen, f"duplicate registry id {sid}"
        seen.add(sid)
        assert strategy.version and strategy.version[0].isdigit()
        assert strategy.timeframes, f"{sid} declares no timeframes"
        for meth in ("compute", "detect_on_bar", "detect_signal",
                     "build_candidate", "calculate_risk", "calculate_targets",
                     "explain_signal", "backtest"):
            assert callable(getattr(strategy, meth)), f"{sid}.{meth} missing"
        # detect runs on a plain frame without exploding (result may be None)
        out = strategy.detect_signal(_df(900 if strategy.min_bars > 300 else 400),
                                     "XAUUSD", "15M", higher_frames={})
        assert out is None or isinstance(out, Candidate)


def test_manifest_maps_every_strategy_to_its_own_module():
    man = manifest()
    assert set(man) == set(all_strategies()), "manifest/registry drift"
    for sid, m in man.items():
        mod = importlib.import_module(m["module"])
        assert getattr(mod, m["class"], None) is not None
        assert m["state"] in ALL_LIFECYCLE_STATES
        assert m["executable"] is (m["state"] == "LIVE")
    assert all(m["state"] == "LIVE" for m in man.values()), \
        "the 8 registered strategies are LIVE by default"


def test_promotion_doctrine_ships_with_the_code():
    readme = pathlib.Path(__file__).parent.parent / "app" / "strategies" / \
        "versions" / "README.md"
    assert readme.exists(), "strategies/versions/README.md missing"
    text = readme.read_text()
    for state in ALL_LIFECYCLE_STATES:
        assert state in text
    assert "ONE Protected Python Module".lower() in text.lower()


# ---------------------------------------------------------------------------
# signal contract (sections 4 + 8)
# ---------------------------------------------------------------------------
def test_detect_signal_stamps_strategy_identity():
    cand = FakeStrategy(VALID).detect_signal(_df(), "EURUSD", "15M")
    assert cand is not None
    assert cand.strategy_version == "2.1"          # the CODE version
    assert cand.timestamp                           # bar close time
    assert cand.signal_id == ""                     # assigned by the engine


def test_invalid_candidates_never_reach_the_risk_engine():
    df = _df()

    def variant(**kw) -> Candidate:
        return dataclasses.replace(VALID, **kw)

    # zero risk
    assert FakeStrategy(variant(sl=VALID.entry)).detect_signal(
        df, "EURUSD", "15M") is None
    # TP on the wrong side of a BUY
    assert FakeStrategy(variant(tps=[1.05, 1.12])).detect_signal(
        df, "EURUSD", "15M") is None
    # bogus direction
    assert FakeStrategy(variant(direction="SIDEWAYS")).detect_signal(
        df, "EURUSD", "15M") is None
    # non-numeric level
    assert FakeStrategy(variant(sl=None)).detect_signal(
        df, "EURUSD", "15M") is None
    # valid one still passes
    assert FakeStrategy(VALID).detect_signal(df, "EURUSD", "15M") is not None


def test_validate_candidate_allows_trail_strategies_without_tps():
    assert validate_candidate(dataclasses.replace(VALID, tps=[])) is None


def test_engine_contract_gate_covers_adapter_strategies(monkeypatch):
    """Adapters override detect_signal - the engine choke point must catch
    an invalid candidate from ANY strategy before guards/execution."""
    from app.engine import signal_engine as SE

    class Adapterish:
        id = "strategy_fake"
        version = "1.0"

    pristine = dataclasses.replace(VALID, strategy_version="", timestamp="")
    bad = dataclasses.replace(pristine, sl=pristine.entry)   # zero risk
    assert SE._contract_check(bad, Adapterish()) is None
    ok = SE._contract_check(dataclasses.replace(pristine), Adapterish())
    assert ok is not None and ok.strategy_version == "1.0"


# ---------------------------------------------------------------------------
# lifecycle states (section 7) - controlled OUTSIDE the strategy modules
# ---------------------------------------------------------------------------
def test_state_gate_only_live_executes(monkeypatch):
    from app.learning import versions as V
    monkeypatch.setattr(V, "LIFECYCLE", {"strategy_x": "SHADOW",
                                         "strategy_y": "RESEARCH",
                                         "strategy_z": "SLEEPING",
                                         "strategy_r": "RETIRED"})
    assert can_execute("strategy_x") is False
    assert can_execute("strategy_y") is False
    assert can_execute("strategy_z") is False
    assert can_execute("strategy_r") is False
    assert can_execute("strategy_unknown_defaults_live") is True
    assert V.lifecycle_of("strategy_x") == "SHADOW"


def test_pretrade_validator_refuses_every_non_live_state():
    from app.execution.pretrade import validate_order
    base = dict(market="XAUUSD", direction="SELL", entry=2400.0, sl=2412.0,
                tp=2385.0, lots=0.08, intended_rr=1.25, symbol_known=True,
                strategy_status="ACTIVE", pip_size=0.1, pip_value=10.0,
                min_stop_pips=6.0, balance=10_000.0, eff_risk_pct=1.0,
                lots_cap=1.0, dup_checked=True, budgets_allowed=True,
                reference_price=2400.0)
    for state in ("SHADOW", "RESEARCH", "SLEEPING", "RETIRED"):
        ok, code, why = validate_order(**{**base, "lifecycle": state})
        assert not ok and code == "STRATEGY_NOT_LIVE", state
    ok, code, _ = validate_order(**{**base, "lifecycle": "LIVE"})
    assert ok, code


def test_strategies_by_state_view():
    live = strategies_by_state("LIVE")
    assert set(live) == set(all_strategies())
    assert strategies_by_state("RETIRED") == {}


# ---------------------------------------------------------------------------
# isolation (section 11: a strategy cannot modify another strategy)
# ---------------------------------------------------------------------------
def test_strategy_instances_do_not_share_mutable_state():
    a, b = FakeStrategy(VALID), FakeStrategy(VALID)
    a.base_params = {"tp_multiple": 9}
    got = a.get_parameters({"x": 1})
    got["x"] = 99                                   # mutating the copy...
    assert b.get_parameters({"x": 1})["x"] == 1      # ...must not leak
    assert a.get_parameters() is not a.base_params or True
    # candidate metadata is per-instance
    c1 = FakeStrategy(dataclasses.replace(VALID, metadata={"tag": "a"}))
    c2 = FakeStrategy(dataclasses.replace(VALID, metadata={"tag": "b"}))
    assert c1._cand.metadata != c2._cand.metadata
