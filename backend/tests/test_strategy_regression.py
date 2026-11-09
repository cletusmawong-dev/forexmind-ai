"""Behaviour regression anchor (owner spec 2026-11-02, section 5).

"The refactored strategy must produce the same signals as the original
implementation... If a difference appears, stop and investigate it."

The fixture (tests/fixtures/strategy_regression_snapshot.json) was captured
from the pre-upgrade code on deterministic synthetic frames (see
strategy_harness.py). This test rebuilds the same frames and demands
IDENTICAL rule outputs from every registered + dormant-EMA strategy.

A failure here is a STOP: either the change is a bug (fix the change) or it
is an explicitly documented improvement (re-baseline the fixture in the same
commit, with the owner's approval and the evidence attached).
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strategy_harness import build_snapshot, snapshot_hash  # noqa: E402

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "strategy_regression_snapshot.json")


@pytest.mark.skipif(not FIXTURE.exists(), reason="baseline fixture missing")
def test_strategy_behaviour_unchanged_since_baseline():
    baseline = json.loads(FIXTURE.read_text())["snapshot"]
    live = build_snapshot()
    assert set(live) == set(baseline), \
        "registry changed since the baseline - re-baseline consciously"
    changed = {sid: (baseline[sid]["hash"][:12], live[sid]["hash"][:12])
               for sid in live
               if live[sid]["hash"] != baseline[sid]["hash"]}
    assert not changed, (
        "TRADING BEHAVIOUR CHANGED since the architecture baseline - "
        f"investigate, never silently accept: {changed}")


@pytest.mark.skipif(not FIXTURE.exists(), reason="baseline fixture missing")
def test_baseline_fixture_is_self_consistent():
    doc = json.loads(FIXTURE.read_text())
    assert snapshot_hash(doc["snapshot"]).startswith(
        "c3ec82a3b54671d3"), "fixture was re-baselined - update this pin"
