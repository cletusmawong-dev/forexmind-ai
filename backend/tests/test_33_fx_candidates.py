"""FX research candidate engines (owner handoff 2026-10-09).

Rule-level tests for the four XAUUSD RESEARCH ONLY engines, plus the
additive endpoint: exact rule outcomes, session windows, sizing fail-closed,
malformed inputs, and proof that evaluation NEVER invokes execution.
"""
from __future__ import annotations

import pandas as pd
import pytest


class Bars:
    """Accumulates completed candles - no manual array counting."""

    def __init__(self):
        self.t, self.o, self.h, self.l, self.c = [], [], [], [], []

    def add(self, ts, o, h, l, c):
        self.t.append(ts)
        self.o.append(o)
        self.h.append(h)
        self.l.append(l)
        self.c.append(c)

    def flat(self, times, o=2400.0, h=2401.0, l=2399.0, c=2400.5):
        for t in times:
            self.add(t, o, h, l, c)
        return self

    def df(self):
        df = pd.DataFrame({"open": self.o, "high": self.h, "low": self.l,
                           "close": self.c},
                          index=pd.to_datetime(self.t, utc=True))
        df.index.name = "datetime"
        return df


HOURS = lambda day, hours, fmt="%Y-10-%02dT%02d:00:00": [
    fmt % (day, h) for h in hours]


# ------------------------------------------------------------- h1_breakout_v1
def test_h1_breakout_full_rule():
    from app.research.fx_engines import h1_breakout
    b = Bars()
    b.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    b.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    b.add("2026-10-09T18:00:00", 2400.6, 2404.0, 2400.2, 2403.5)  # breakout
    r = h1_breakout(b.df())
    assert r["status"] == "RESEARCH_CANDIDATE"
    assert r["direction"] == "LONG" and r["executionEnabled"] is False
    # stop = lower of prior 10-bar low (2399.0) and entry - ATR -> 2399.0
    assert r["sl"] == 2399.0
    assert r["tp"] == round(r["entry"] + 1.25 * (r["entry"] - r["sl"]), 5)
    assert r["break_even_marker_r"] == 0.5
    assert r["r_multiple"] == 1.25
    assert r["sizing"]["lot"] >= 0.01
    assert r["signal_time"].startswith("2026-10-09T18:00")


def test_h1_breakout_session_window():
    from app.research.fx_engines import h1_breakout
    # identical data but the breakout bar closes 15:00 UTC -> NO_SIGNAL
    b = Bars()
    b.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    b.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    b.add("2026-10-09T15:00:00", 2400.6, 2404.0, 2400.2, 2403.5)
    assert h1_breakout(b.df())["status"] == "NO_SIGNAL"


def test_h1_breakout_insufficient_and_channel_miss():
    from app.research.fx_engines import h1_breakout
    b = Bars()
    b.flat([f"2026-10-08T{h:02d}:00:00" for h in range(12)])   # 12 < 22 bars
    r = h1_breakout(b.df())
    assert r["status"] == "INSUFFICIENT_DATA"
    b2 = Bars()
    b2.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    b2.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    b2.add("2026-10-09T18:00:00", 2400.6, 2404.0, 2400.2, 2400.9)  # not above 2401
    r2 = h1_breakout(b2.df())
    assert r2["status"] == "NO_SIGNAL"


# --------------------------------------------------------- opening_range_v1
def test_opening_range_full_rule():
    from app.research.fx_engines import opening_range
    b = Bars()
    for m in (30, 35, 40, 45, 50, 55):                    # six OR bars
        b.add(f"2026-10-09T13:{m:02d}", 2403.0, 2403.5, 2402.5, 2403.2)
    for m in range(0, 60, 5):                             # drift after OR
        b.add(f"2026-10-09T14:{m:02d}", 2403.4, 2403.8, 2403.0, 2403.6)
    b.add("2026-10-09T15:00", 2403.8, 2406.0, 2403.4, 2405.5)  # breakout
    r = opening_range(b.df())
    assert r["status"] == "RESEARCH_CANDIDATE"
    assert r["sl"] == 2402.5                              # OR low
    assert r["tp"] == round(r["entry"] + 1.5 * (r["entry"] - r["sl"]), 5)
    assert r["break_even_marker_r"] == 1.0
    assert r["direction"] == "LONG"


def test_opening_range_needs_six_bars_and_window():
    from app.research.fx_engines import opening_range
    b = Bars()
    for m in (30, 35, 40, 45):                            # only 4 OR bars
        b.add(f"2026-10-09T13:{m:02d}", 2400.0, 2401.0, 2399.5, 2400.5)
    for m in range(0, 60, 5):
        b.add(f"2026-10-09T14:{m:02d}", 2400.8, 2401.2, 2400.5, 2401.0)
    b.add("2026-10-09T15:00", 2401.2, 2406.0, 2401.0, 2405.5)
    assert opening_range(b.df())["status"] == "INSUFFICIENT_DATA"
    # breakout inside the OR window (13:50) -> outside entry window
    b2 = Bars()
    for m in (0, 5, 10, 15, 20):                          # earlier day bars
        b2.add(f"2026-10-09T13:{m:02d}", 2400.0, 2401.0, 2399.5, 2400.5)
    for m in (30, 35, 40, 45, 50, 55):
        b2.add(f"2026-10-09T13:{m:02d}", 2400.0, 2401.0, 2399.5, 2400.5)
    b2.add("2026-10-09T13:50", 2400.0, 2406.0, 2399.9, 2405.5)
    assert opening_range(b2.df())["status"] == "NO_SIGNAL"


# ------------------------------------------------------ liquidity_sweep_v1
def test_liquidity_sweep_long_and_short():
    from app.research.fx_engines import liquidity_sweep
    b = Bars()
    b.flat([f"2026-10-09T{h:02d}:{m:02d}" for h in range(10, 20)
            for m in (0, 15, 30, 45)])
    b.add("2026-10-09T20:00", 2400.0, 2400.4, 2396.0, 2400.3)  # long sweep
    r = liquidity_sweep(b.df())
    assert r["status"] == "RESEARCH_CANDIDATE"
    assert r["direction"] == "LONG" and r["sl"] == 2396.0  # sweep extreme
    assert r["tp"] == round(r["entry"] + 0.75 * (r["entry"] - r["sl"]), 5)
    assert r["break_even_marker_r"] is None                # spec: none
    b2 = Bars()
    b2.flat([f"2026-10-09T{h:02d}:{m:02d}" for h in range(10, 20)
             for m in (0, 15, 30, 45)])
    b2.add("2026-10-09T20:00", 2400.0, 2404.0, 2399.9, 2399.7)  # short sweep
    r2 = liquidity_sweep(b2.df())
    assert r2["status"] == "RESEARCH_CANDIDATE"
    assert r2["direction"] == "SHORT" and r2["sl"] == 2404.0
    assert r2["tp"] == round(r2["entry"] - 0.75 * (r2["sl"] - r2["entry"]), 5)


def test_liquidity_sweep_trending_market_filtered():
    """A strong directional run keeps ADX(14) well above 20 -> NO_SIGNAL,
    even when the latest candle forms a sweep shape."""
    from app.research.fx_engines import liquidity_sweep
    b = Bars()
    t = [f"2026-10-09T{h:02d}:{m:02d}" for h in range(10, 20)
         for m in (0, 15, 30, 45)]
    for k, ts in enumerate(t):
        if k < 15:
            b.add(ts, 2400.0, 2400.6, 2399.8, 2400.2)          # flat open
        else:
            px = 2401 + 2.5 * (k - 15)                          # 25 strong up bars
            b.add(ts, px, px + 2.4, px - 0.4, px + 2.0)
    b.add(t[-1], 2460.0, 2462.0, 2440.0, 2461.5)                # sweep-shaped bar
    r = liquidity_sweep(b.df())
    assert r["status"] == "NO_SIGNAL"
    assert "ADX" in r["reason"]


# ----------------------------------------------------------- crt_4h_15m_v1
def _crt_bars(confirm=True):
    h4 = Bars()
    for k, ts in enumerate(["2026-10-09T00:00", "2026-10-09T04:00",
                            "2026-10-09T08:00", "2026-10-09T12:00"]):
        h4.add(ts, 2400.0, 2405.0, 2395.0, 2402.0)
    h4.add("2026-10-09T16:00", 2396.0, 2401.0, 2394.5, 2397.0)  # sweep+reclaim
    m15 = Bars()
    for h in (12, 13, 14, 15, 16, 17):          # >= 22 bars of M15 history
        for m in (0, 15, 30, 45):
            m15.add(f"2026-10-09T{h:02d}:{m:02d}", 2396.5, 2397.0, 2396.0, 2396.8)
    if confirm:
        m15.add("2026-10-09T18:00", 2397.0, 2400.0, 2396.8, 2399.5)
    else:
        m15.add("2026-10-09T18:00", 2396.8, 2397.0, 2396.2, 2396.6)
    return h4.df(), m15.df()


def test_crt_bullish_confirmed():
    from app.research.fx_engines import crt_4h_15m
    h4, m15 = _crt_bars(confirm=True)
    r = crt_4h_15m(h4, m15)
    assert r["status"] == "RESEARCH_CANDIDATE"
    assert r["direction"] == "LONG"
    assert r["sl"] == 2394.5                       # H4 sweep extreme
    assert r["tp"] == round(r["entry"] + (r["entry"] - r["sl"]), 5)   # 1R
    assert r["break_even_marker_r"] == 0.5


def test_crt_awaiting_m15_confirmation():
    from app.research.fx_engines import crt_4h_15m
    h4, m15 = _crt_bars(confirm=False)
    r = crt_4h_15m(h4, m15)
    assert r["status"] == "NO_SIGNAL"
    assert "M15" in r["reason"]


def test_crt_key_level_proxy():
    """Sweep extreme too far from the prior H4 low -> proxy fails."""
    from app.research.fx_engines import crt_4h_15m
    h4, m15 = _crt_bars(confirm=True)
    h4.iloc[-1, h4.columns.get_loc("low")] = 2380.0   # sweep extreme 14.5 from boundary
    r = crt_4h_15m(h4, m15)
    assert r["status"] == "NO_SIGNAL"
    assert "proxy fails" in r["reason"]


# ------------------------------------------------- shared behaviors
def test_sizing_fails_closed(monkeypatch):
    """min lot 0.01 x contract 100 x $6 stop = $6 risk > $5 budget -> SKIP_RISK."""
    from app.research.fx_engines import h1_breakout
    b = Bars()
    b.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)], o=100.0, h=100.4,
           l=99.6, c=100.2)
    b.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)], o=100.0, h=100.4,
           l=99.6, c=100.2)
    b.add("2026-10-09T18:00:00", 100.3, 108.0, 100.2, 107.5)  # huge range -> wide stop
    r = h1_breakout(b.df())
    assert r["status"] == "SKIP_RISK"
    assert "risk" in r["reason"].lower()


def test_malformed_inputs_never_raise():
    from app.research.fx_engines import (crt_4h_15m, h1_breakout,
                                         liquidity_sweep, opening_range)
    for bad in (None, pd.DataFrame(), pd.DataFrame({"open": [1]}), "junk", 42):
        assert h1_breakout(bad)["status"] == "INSUFFICIENT_DATA"
        assert opening_range(bad)["status"] == "INSUFFICIENT_DATA"
        assert liquidity_sweep(bad)["status"] == "INSUFFICIENT_DATA"
        assert crt_4h_15m(bad, bad)["status"] == "INSUFFICIENT_DATA"
    # duplicated + unsorted candles are normalized, not trusted
    b = Bars()
    b.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    b.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    b.add("2026-10-09T18:00:00", 2400.6, 2404.0, 2400.2, 2403.5)
    good = b.df()
    scrambled = pd.concat([good, good.iloc[[-1]]]).sample(frac=1.0, random_state=1)
    assert h1_breakout(scrambled)["status"] == "RESEARCH_CANDIDATE"


# ------------------------------------------------- endpoint integration
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


from fastapi.testclient import TestClient  # noqa: E402


def test_fx_endpoint_shape_and_research_only(env, monkeypatch):
    c, store, mp, settings = env
    bars = Bars()
    bars.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    bars.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    bars.add("2026-10-09T18:00:00", 2400.6, 2404.0, 2400.2, 2403.5)
    df = bars.df()

    class StubProvider:
        def get_candles(self, market, tf, limit=600):
            return df

    from app.state import State
    mp.setattr(State, "provider", StubProvider(), raising=False)
    r = c.get("/api/research/candidates/fx")
    assert r.status_code == 200
    body = r.json()
    assert body["executionEnabled"] is False and body["label"] == "RESEARCH ONLY"
    ids = {x["id"] for x in body["candidates"]}
    assert ids == {"h1_breakout_v1", "opening_range_v1",
                   "liquidity_sweep_v1", "crt_4h_15m_v1"}
    for cand in body["candidates"]:
        assert cand["executionEnabled"] is False
        assert cand["status"] in ("NO_SIGNAL", "INSUFFICIENT_DATA",
                                  "RESEARCH_CANDIDATE", "SKIP_RISK")
        assert cand["reason"]
    h1 = next(x for x in body["candidates"] if x["id"] == "h1_breakout_v1")
    assert h1["status"] == "RESEARCH_CANDIDATE"


def test_fx_endpoint_missing_candles(env, monkeypatch):
    c, store, mp, settings = env
    class NoDataProvider:
        def get_candles(self, market, tf, limit=600):
            return None

    from app.state import State
    mp.setattr(State, "provider", NoDataProvider(), raising=False)
    r = c.get("/api/research/candidates/fx")
    assert r.status_code == 200
    assert all(x["status"] == "INSUFFICIENT_DATA" for x in r.json()["candidates"])


def test_fx_endpoint_never_calls_execution(env, monkeypatch):
    """Evaluation must not touch the MT5 bridge or any execution function."""
    c, store, mp, settings = env
    import app.execution.mt5 as mt5
    calls = []

    def _trip(*a, **k):
        calls.append((a, k))
        raise AssertionError("execution function invoked from research path")

    for fn in ("bridge_get", "bridge_post", "bridge_request", "place_order",
               "execute_signal"):
        if hasattr(mt5, fn):
            monkeypatch.setattr(mt5, fn, _trip)
    bars = Bars()
    bars.flat([f"2026-10-08T{h:02d}:00:00" for h in range(24)])
    bars.flat([f"2026-10-09T{h:02d}:00:00" for h in range(17)])
    bars.add("2026-10-09T18:00:00", 2400.6, 2404.0, 2400.2, 2403.5)
    class StubProvider:
        def get_candles(self, market, tf, limit=600):
            return bars.df()

    from app.state import State
    mp.setattr(State, "provider", StubProvider(), raising=False)
    r = c.get("/api/research/candidates/fx")
    assert r.status_code == 200
    assert calls == []


def test_existing_routes_preserved(env):
    """Integration is additive - the established endpoints still exist."""
    c, store, mp, settings = env
    for path in ("/api/research/engine/sources", "/api/research/engine/candidates",
                 "/api/research/engine/memory", "/api/strategies",
                 "/api/research/shadow", "/api/journal/entries"):
        r = c.get(path)
        assert r.status_code == 200, path
