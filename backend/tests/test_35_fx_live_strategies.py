"""Strategy 3 spec family as LIVE strategies (owner directive 2026-10-10).

The four spec-fixed XAUUSD engines (h1_breakout_v1, opening_range_v1,
liquidity_sweep_v1, crt_4h_15m_v1) registered in the strategy registry so
they appear on the Strategies screen and are scanned like any other
strategy. The OLD strategies stay asleep (PAUSED docs are never touched).

Nothing here modifies the execution core: in the test environment the
executor hook runs and honestly records SKIPPED_ADVISORY_MODE (execution
mode off) - proving the signal path works without placing any order.
"""
import pandas as pd
import pytest


# ----------------------------------------------------------------- helpers
def frame(ts, o, h, l, c):
    idx = pd.to_datetime(ts, utc=True)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def h1_breakout_frame(n_extra: int = 0):
    """310 H1 bars ending Friday 20:00 UTC; last bar breaks the prior 20-bar
    high in-session (spec h1_breakout_v1 trigger)."""
    ts = pd.date_range(end="2026-10-09T20:00", periods=310 + n_extra, freq="1h", tz="UTC")
    ts = [str(t) for t in ts]
    o = [2400.0] * (309 + n_extra)
    h = [2401.0] * (309 + n_extra)
    l = [2399.0] * (309 + n_extra)
    c = [2400.0] * (309 + n_extra)
    o += [2400.0]; h += [2404.0]; l += [2399.5]; c += [2403.5]
    return frame(ts, o, h, l, c)


def flat_m15(n=40, end="2026-10-09T19:45"):
    ts = pd.date_range(end=end, periods=n, freq="15min", tz="UTC")
    return frame([str(t) for t in ts], [2400.0] * n, [2400.6] * n,
                 [2399.8] * n, [2400.2] * n)


# ------------------------------------------------------------ registry
def test_new_strategies_registered():
    from app.strategies import all_strategies
    s = all_strategies()
    for sid in ("strategy_3_h1_breakout", "strategy_3_opening_range",
                "strategy_3_liquidity_sweep", "strategy_3_crt_4h_15m"):
        assert sid in s
        assert s[sid].markets == ["XAUUSD"]
        assert "XAUUSD" in str(s[sid].name) or "XAUUSD" in str(s[sid].description)
    # spec-fixed: no tunable parameters, no experiment variables
    for sid in ("strategy_3_h1_breakout", "strategy_3_opening_range",
                "strategy_3_liquidity_sweep", "strategy_3_crt_4h_15m"):
        assert s[sid].base_params == {}
        assert s[sid].experiment_variables == {}


def test_adapters_import_no_execution():
    import pathlib
    pkg = pathlib.Path(__file__).resolve().parents[1] / "app" / "strategies" / "strategy_3_fx_specs"
    for f in pkg.glob("*.py"):
        src = f.read_text()
        assert "app.execution" not in src and "from ...execution" not in src, f
        assert "bridge" not in src.lower() or f.name == "adapter.py"


# ------------------------------------------------------------ adapters
def test_h1_breakout_adapter_fires_on_scanned_base_tf():
    from app.strategies import get_strategy
    df = h1_breakout_frame()
    cand = get_strategy("strategy_3_h1_breakout").detect_signal(
        df, "XAUUSD", "1H", score_context={"session": "ny"})
    assert cand is not None
    assert cand.direction == "BUY"
    assert cand.timeframe == "1H"                       # BASE tf, not scan tf
    assert cand.candle_time == str(df.index[-1])        # stable for dedupe
    assert cand.sl == 2399.0                            # prior 10-bar low
    assert cand.tps and abs(cand.tps[0] - cand.entry) == pytest.approx(
        1.25 * (cand.entry - cand.sl))
    assert cand.extra["spec_id"] == "h1_breakout_v1"
    assert cand.extra["session"] == "ny"


def test_h1_breakout_adapter_fires_from_higher_frames():
    """Scanned on 15M: the engine reads the 1H frame from higher_frames and
    the candidate still carries the BASE timeframe (dedupe collapses)."""
    from app.strategies import get_strategy
    cand = get_strategy("strategy_3_h1_breakout").detect_signal(
        flat_m15(), "XAUUSD", "15M",
        higher_frames={"1H": h1_breakout_frame(), "5M": flat_m15()},
        score_context={})
    assert cand is not None and cand.direction == "BUY" and cand.timeframe == "1H"


def test_wrong_market_never_signals():
    from app.strategies import get_strategy
    assert get_strategy("strategy_3_h1_breakout").detect_signal(
        h1_breakout_frame(), "EURUSD", "1H") is None


def test_no_signal_status_maps_to_none():
    """Weekend bar far outside any session window -> NO_SIGNAL -> None."""
    from app.strategies import get_strategy
    df = h1_breakout_frame()
    df.index = pd.to_datetime([f"2026-10-10T{s:02d}:00" for s in range(0, 310)], utc=True) \
        if False else df.index  # keep fixture; instead shift hours out of session
    df = df.copy()
    idx = pd.date_range(end="2026-10-10T04:00", periods=310, freq="1h", tz="UTC")
    df.index = idx   # Saturday, off-session
    assert get_strategy("strategy_3_h1_breakout").detect_signal(
        df, "XAUUSD", "1H") is None


def test_sweep_adapter_short_mapping_and_skip_risk():
    from app.strategies import get_strategy
    strat = get_strategy("strategy_3_liquidity_sweep")
    n = 40
    ts = [str(t) for t in pd.date_range(end="2026-10-09T19:45", periods=n, freq="15min", tz="UTC")]
    b = frame(ts, [2400.0] * n, [2400.6] * n, [2399.8] * n, [2400.2] * n)
    # append a short sweep candle: high 2404 breaks the 20-bar high, closes back inside

    ts2 = ts + [str(pd.Timestamp("2026-10-09T20:00", tz="UTC"))]
    b2 = frame(ts2, [2400.0] * n + [2400.0], [2400.6] * n + [2404.0],
               [2399.8] * n + [2399.9], [2400.2] * n + [2399.7])
    cand = strat.detect_signal(b2, "XAUUSD", "15M", score_context={})
    assert cand is not None
    assert cand.direction == "SELL"                     # SHORT -> SELL mapping
    assert cand.sl == 2404.0                            # sweep extreme
    assert cand.tps[0] == pytest.approx(cand.entry - 0.75 * (cand.sl - cand.entry))
    # oversized sweep extreme -> SKIP_RISK (fail-closed sizing) -> no signal
    ts3 = ts + [str(pd.Timestamp("2026-10-09T20:00", tz="UTC"))]
    b3 = frame(ts3, [2400.0] * n + [2400.0], [2400.6] * n + [2410.0],
               [2399.8] * n + [2399.9], [2400.2] * n + [2399.7])
    assert strat.detect_signal(b3, "XAUUSD", "15M") is None


def test_orb_adapter_reads_5m_from_higher_frames():
    from app.strategies import get_strategy
    ts = [f"2026-10-09T13:{m:02d}" for m in (30, 35, 40, 45, 50, 55)]
    o = [2403.0] * 6; h = [2403.5] * 6; l = [2402.5] * 6; c = [2403.2] * 6
    ts += [f"2026-10-09T14:{m:02d}" for m in range(0, 60, 5)]
    o += [2403.4] * 12; h += [2403.8] * 12; l += [2403.0] * 12; c += [2403.6] * 12
    ts += ["2026-10-09T15:00"]
    o += [2403.8]; h += [2406.0]; l += [2403.4]; c += [2405.5]
    m5 = frame(ts, o, h, l, c)
    cand = get_strategy("strategy_3_opening_range").detect_signal(
        flat_m15(), "XAUUSD", "15M", higher_frames={"5M": m5}, score_context={})
    assert cand is not None and cand.direction == "BUY" and cand.timeframe == "5M"
    assert cand.sl == 2402.5


def test_crt_adapter_missing_h4_frame_is_none():
    from app.strategies import get_strategy
    h4 = frame(["2026-10-09T04:00", "2026-10-09T08:00", "2026-10-09T12:00",
                "2026-10-09T16:00"],
               [2400.0, 2398.0, 2396.5, 2396.0], [2402.0, 2400.0, 2398.5, 2401.0],
               [2397.5, 2395.5, 2395.0, 2394.5], [2398.0, 2396.5, 2397.0, 2397.0])
    n = 23
    ts = [str(t) for t in pd.date_range(end="2026-10-09T18:00", periods=n, freq="15min", tz="UTC")]
    m15 = frame(ts, [2396.5] * n, [2397.0] * n, [2396.0] * n, [2396.8] * n)
    m15.loc[m15.index[-1], "close"] = 2399.5
    m15.loc[m15.index[-1], "high"] = 2400.0
    strat = get_strategy("strategy_3_crt_4h_15m")
    # scanned on 1H: h4 from higher_frames, m15 from higher_frames too
    cand = strat.detect_signal(
        h1_breakout_frame(), "XAUUSD", "1H",
        higher_frames={"4H": h4, "15M": m15}, score_context={})
    assert cand is not None and cand.direction == "BUY" and cand.timeframe == "15M"
    assert cand.sl == 2394.5
    # missing 4H frame -> INSUFFICIENT_DATA -> None (never invent data)
    assert strat.detect_signal(h1_breakout_frame(), "XAUUSD", "1H",
                               higher_frames={"15M": m15}) is None


# ------------------------------------------------------------ docs & sleep
def _local_store(tmp_path):
    from app.db.store import LocalStore
    return LocalStore(str(tmp_path / "fx.json"))


def test_ensure_docs_creates_new_active_and_respects_paused(tmp_path, monkeypatch):
    from app.db.store import LocalStore
    from app.learning import versions as vc
    store = _local_store(tmp_path)
    # old strategies asleep: pre-existing PAUSED docs (plus the prod-style
    # stale version row that used to starve reconciliation)
    store.create("strategies", {"status": "PAUSED"}, doc_id="strategy_1_vp_pivots")
    store.create("strategy_versions",
                 {"strategy_id": "strategy_1_vp_pivots", "version": "1.0",
                  "active": False, "params": {}, "changes": []},
                 doc_id="strategy_1_vp_pivots-v1.0")
    monkeypatch.setattr(vc, "get_store", lambda: store)
    vc.ensure_strategy_docs()
    for sid in ("strategy_3_h1_breakout", "strategy_3_opening_range",
                "strategy_3_liquidity_sweep", "strategy_3_crt_4h_15m"):
        doc = store.list("strategies", filters={"id": sid}, limit=1)
        assert doc and doc[0]["status"] == "ACTIVE", sid
    assert store.list("strategies", filters={"id": "strategy_1_vp_pivots"})[0]["status"] == "PAUSED"


# ------------------------------------------------------------ full pipeline
def test_scan_creates_real_signal_executor_stays_honest(tmp_path, monkeypatch):
    """End to end WITHOUT touching execution: engine.scan -> Candidate ->
    signal doc -> executor hook runs and records SKIPPED_ADVISORY_MODE
    (execution mode 'off' in the test env). No order can be placed."""
    from app.db.store import LocalStore
    from app.engine import signal_engine as se_mod
    from app.engine.signal_engine import SignalEngine
    from app.learning import versions as vc

    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(se_mod, "get_store", lambda: store)
    monkeypatch.setattr(vc, "get_store", lambda: store)
    # the executor (app.execution.mt5) has its OWN get_store binding
    from app.execution import mt5 as mt5_mod
    monkeypatch.setattr(mt5_mod, "get_store", lambda: store)
    vc.ensure_strategy_docs()                      # new docs ACTIVE, like prod boot

    class StubProvider:
        is_demo = True
        def get_candles(self, market, tf, limit=600):
            return h1_breakout_frame() if tf == "1H" else flat_m15()
        def higher_frames(self, market, base_tf):
            return {"5M": flat_m15(), "15M": flat_m15(), "1H": h1_breakout_frame()}

    engine = SignalEngine(StubProvider())
    created = engine.scan("cletusmawa", "XAUUSD", "1H", log_activity=False)
    mine = [c for c in created if c["strategy_id"] == "strategy_3_h1_breakout"]
    assert mine and mine[0]["direction"] == "BUY"

    doc = store.list("signals", filters={"strategy_id": "strategy_3_h1_breakout"},
                     limit=1)[0]
    assert doc["market"] == "XAUUSD" and doc["timeframe"] == "1H"
    assert doc.get("execution_status") == "SKIPPED_ADVISORY_MODE"

    # second scan on a different tf sees the SAME setup -> dedupe collapses
    assert engine.scan("cletusmawa", "XAUUSD", "15M", log_activity=False) == []
    n = len(store.list("signals", filters={"strategy_id": "strategy_3_h1_breakout"}))
    assert n == 1


def test_paused_old_strategy_never_scans(tmp_path, monkeypatch):
    """The sleeping old ones must stay silent while the new ones are live."""
    from app.db.store import LocalStore
    from app.engine import signal_engine as se_mod
    from app.engine.signal_engine import SignalEngine
    from app.learning import versions as vc

    store = LocalStore(str(tmp_path / "db.json"))
    monkeypatch.setattr(se_mod, "get_store", lambda: store)
    monkeypatch.setattr(vc, "get_store", lambda: store)
    from app.execution import mt5 as mt5_mod
    monkeypatch.setattr(mt5_mod, "get_store", lambda: store)
    store.create("strategies", {"status": "PAUSED"},
                 doc_id="strategy_2_supply_demand_fvg")
    vc.ensure_strategy_docs()

    class StubProvider:
        is_demo = True
        def get_candles(self, market, tf, limit=600):
            return flat_m15(400) if tf == "15M" else None
        def higher_frames(self, market, base_tf):
            return {"5M": flat_m15(), "15M": flat_m15(400)}

    engine = SignalEngine(StubProvider())
    created = engine.scan("cletusmawa", "XAUUSD", "15M", log_activity=False)
    ids = {c["strategy_id"] for c in created}
    assert "strategy_2_supply_demand_fvg" not in ids      # PAUSED -> skipped
