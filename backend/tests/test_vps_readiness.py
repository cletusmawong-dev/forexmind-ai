"""VPS-readiness infrastructure tests (user spec 2026-09-16, §5-§11, §24).

Market-data abstraction honesty, tick normalization, deterministic candle
building, integrity/gap detection, backfill validation, data-gap protection
in the signal engine, and the system status endpoint.
"""
from datetime import datetime, timezone

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


# ---------- tick normalization (§6, §24) ----------

def test_tick_normalization_dedup_order_and_validation():
    from app.market_data.ticks import normalize_ticks
    now = 1_800_000_000
    raw = [
        {"ts": now - 10, "price": 3300.5},
        {"ts": now - 10, "price": 3300.9},              # duplicate ts -> rejected
        {"ts": now - 30, "price": 3300.1},              # out of order -> accepted
        {"ts": now + 999, "price": 3301.0},             # far future -> rejected
        {"ts": now - 20, "price": -1},                  # bad price -> rejected
        {"ts": "not-a-time", "price": 3300.0},          # bad ts -> rejected
        {"ts": now - 5, "price": 3302.0, "volume": 3},
    ]
    accepted, rejected = normalize_ticks(raw, symbol="XAUUSD", now=now)
    assert [t.ts for t in accepted] == sorted(t.ts for t in accepted)
    assert len(accepted) == 3
    assert accepted[0].price == 3300.1                  # out-of-order got sorted in
    assert accepted[-1].volume == 3
    reasons = [r["reason"] for r in rejected]
    assert reasons.count("duplicate") == 1
    assert reasons.count("bad_timestamp") == 2
    assert reasons.count("bad_price") == 1


def test_stale_detection():
    from app.market_data.ticks import Tick, is_stale
    now = 1_800_000_000
    fresh = [Tick(ts=now - 30, price=1.0)]
    old = [Tick(ts=now - 999, price=1.0)]
    assert not is_stale(fresh, now, max_age_s=300)
    assert is_stale(old, now, max_age_s=300)
    assert is_stale([], now)


# ---------- deterministic candle building (§7, §24) ----------

def _tick(ts, price):
    from app.market_data.ticks import Tick
    return Tick(ts=ts, price=price, volume=1.0)


def test_candle_builder_utc_boundaries_and_closed_once():
    from app.market_data.candle_builder import build_candles, bucket_start
    base = bucket_start(1_800_000_000, 15)             # exact 15M boundary
    ticks = [_tick(base + 60 * i, 3300 + i * 0.5) for i in range(30)]  # 30 min
    as_of = base + 15 * 60 - 1                         # just before first close
    assert build_candles(ticks, "15M", as_of=as_of) == []   # forming candle NOT closed
    as_of2 = base + 15 * 60
    c1 = build_candles(ticks, "15M", as_of=as_of2)
    assert len(c1) == 1
    assert c1[0]["open"] == 3300.0 and c1[0]["close"] == 3300 + 0.5 * 14
    assert c1[0]["high"] == 3300 + 0.5 * 14 and c1[0]["low"] == 3300.0
    # emitted exactly once / idempotent: rebuilding yields the same single candle
    c2 = build_candles(ticks, "15M", as_of=as_of2)
    assert c1 == c2
    as_of3 = base + 30 * 60
    c3 = build_candles(ticks, "15M", as_of=as_of3)
    assert len(c3) == 2 and c3[0] == c1[0]             # first candle never rewritten
    # UTC alignment: 1H buckets sit on exact hour epochs
    assert bucket_start(1_800_000_000, 60) % 3600 == 0
    assert bucket_start(1_800_000_000, 1440) % 86400 == 0


def test_candle_builder_all_intervals_and_determinism():
    from app.market_data.candle_builder import INTERVALS, build_candles
    base = 1_800_000_000 - (1_800_000_000 % 60)
    ticks = [_tick(base + i * 60, 100 + (i % 7)) for i in range(2880)]  # 2 days of 1M
    m1 = build_candles(ticks, "1M", as_of=base + 2880 * 60)
    assert len(m1) == 2880
    for iv in ("5M", "15M", "1H", "4H", "1D"):
        cs = build_candles(ticks, iv, as_of=base + 2880 * 60)
        assert len(cs) >= 1
        # every candle boundary is UTC-aligned
        step = INTERVALS[iv] * 60
        assert all(c["ts"] % step == 0 for c in cs)
    # deterministic: identical input -> identical output
    assert build_candles(ticks, "15M", as_of=base + 2880 * 60) == \
           build_candles(list(reversed(ticks)), "15M", as_of=base + 2880 * 60)


# ---------- integrity / gap detection (§9, §24) ----------

def test_gap_detection_ignores_weekend_closures_and_logs_real_gaps(fresh_store):
    from app.market_data.integrity import detect_gaps
    from app.market_data.events import reset_dedupe
    reset_dedupe()
    step = 900
    t0 = 1_800_000_000 - (1_800_000_000 % step)
    clean = [t0 + i * step for i in range(10)]
    assert detect_gaps(clean, "15M") == []
    with_hole = clean[:5] + clean[7:]                  # candles 5 and 6 missing
    gaps = detect_gaps(with_hole, "15M")
    assert len(gaps) == 1
    assert gaps[0]["missing_from"] == clean[4] + step
    assert gaps[0]["missing_to"] == clean[4] + 2 * step
    # honest structured log exists
    acts = fresh_store.list("agent_activity", limit=50)
    events = [a for a in acts if a.get("event_type") == "DATA_GAP_DETECTED"]
    assert events and events[0]["payload"]["timeframe"] == "15M"
    # weekend closure (>4h) is NOT a gap
    weekend = [t0, t0 + 100 * step]
    assert detect_gaps(weekend, "15M") == []


def test_series_trustworthiness_gate():
    from app.market_data.integrity import series_is_trustworthy
    step = 900
    t0 = 1_800_000_000 - (1_800_000_000 % step)
    clean = [t0 + i * step for i in range(400)]
    assert series_is_trustworthy(clean, "15M", 300)["ok"] is True
    assert series_is_trustworthy(clean[:50], "15M", 300)["reason"] == "insufficient_history"
    dup = clean + [clean[-1]]                          # genuine duplicate timestamp
    assert series_is_trustworthy(dup, "15M", 300)["reason"] == "malformed_series"
    with_hole = clean[:350] + [clean[349] + 4 * step]  # hole right after 349
    gate = series_is_trustworthy(with_hole, "15M", 300)
    assert gate["reason"] == "data_gap"


# ---------- backfill (§10, §24) ----------

def test_backfill_plan_and_merge_validation(fresh_store):
    from app.market_data.backfill import merge_backfill, plan_backfill
    step = 900
    t0 = 1_800_000_000 - (1_800_000_000 % step)
    stamps = [t0 + i * step for i in range(10)]
    now = t0 + 15 * step                               # 5 intervals behind
    plan = plan_backfill(stamps, "15M", now=now)
    assert plan["start"] == t0 + 10 * step and plan["end"] == now - step
    assert plan_backfill(stamps, "15M", now=t0 + step) is None   # current
    assert plan_backfill([], "15M", now=now) is None             # no baseline

    def mk(ts_list):
        return pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0},
            index=[pd.Timestamp(x, unit="s") for x in ts_list])

    ok = merge_backfill(stamps, mk([t0 + 10 * step, t0 + 11 * step, t0 + 12 * step]), "15M")
    assert ok["ok"] is True and ok["count"] == 3
    overlap = merge_backfill(stamps, mk([stamps[0], stamps[1]]), "15M")
    assert overlap["ok"] is False and overlap["reason"] == "overlaps_existing"
    dup = merge_backfill(stamps, mk([t0 + 10 * step, t0 + 10 * step]), "15M")
    assert dup["ok"] is False and dup["reason"] == "duplicate_candles_in_batch"
    empty = merge_backfill(stamps, None, "15M")
    assert empty["ok"] is False and empty["reason"] == "provider_returned_nothing"


def test_provider_capability_honesty(fresh_store):
    """No streaming provider exists -> the app must say so (spec §6, §29)."""
    from app.market_data.live_provider import LiveProvider
    p = LiveProvider()
    assert p.capabilities["streaming"] is False
    assert p.capabilities["ticks"] is False
    assert p.get_latest_tick("XAUUSD") is None         # never fabricated
    assert list(p.stream_ticks("XAUUSD")) == []        # honest empty iterator
    h = p.health()
    assert h["capabilities"]["streaming"] is False
    # without TD configured there is no historical-range backfill either
    p.td_key = ""
    assert p.fetch_historical_candles("XAUUSD", "15M", "2026-09-01", "2026-09-02") is None


# ---------- data-gap protection in the signal engine (§9, §24) ----------

def test_signal_engine_refuses_gapped_data(monkeypatch):
    import os, tempfile
    from app.db.store import LocalStore
    import app.engine.signal_engine as SE

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(SE, "get_store", lambda: store)
    logs = []
    monkeypatch.setattr(SE.SignalEngine, "_log",
                        lambda self, msg, kind="INFO", **k: logs.append((kind, msg)))

    step = 900
    t0 = 1_800_000_000 - (1_800_000_000 % step)
    stamps = [t0 + i * step for i in range(320)]
    stamps = stamps[:310] + stamps[313:]               # hole INSIDE the last-20 window
    idx = pd.to_datetime(stamps, unit="s")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)

    class GapProvider:
        def get_candles(self, market, tf, limit=600):
            return df.copy()

        def higher_frames(self, market, tf):
            return {}

    eng = SE.SignalEngine(GapProvider())
    created = eng.scan("u1", "XAUUSD", "15M")
    assert created == []                               # no signal across a gap
    assert any(k == "DATA" and "data_gap" in m for k, m in logs)

    # clean series -> engine proceeds normally (no integrity refusal)
    clean_idx = pd.to_datetime([t0 + i * step for i in range(400)], unit="s")
    df2 = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
                       index=clean_idx)

    class CleanProvider(GapProvider):
        def get_candles(self, market, tf, limit=600):
            return df2.copy()

    logs.clear()
    eng2 = SE.SignalEngine(CleanProvider())
    eng2.scan("u1", "XAUUSD", "15M")                   # runs strategies; may create nothing
    assert not any("data_gap" in m for _, m in logs)


# ---------- system status endpoint (§15, §24) ----------

def test_system_status_honest_configured_vs_connected(client, auth):
    res = client.get("/api/system/status",
                     headers={"Authorization": f"Bearer {auth['token']}"})
    assert res.status_code == 200
    d = res.json()
    assert d["execution"]["mode"] == "off"             # stays OFF
    assert d["execution"]["kill_switch"] is True       # stays ON
    assert d["market_data"]["stream"]["connected"] is False
    assert d["vps_bridge"]["configured"] is False
    assert "Configured != connected" in d["principle"]
