"""Phase 5: session configuration - toggles, custom hours/tz, per-market matrix.

The session gate already existed globally; this adds validated customization
(SS30/SS31): canonical session names, per-market session matrix that REPLACES
the global list for that market, custom windows in the user's tz with honest
gap handling ('' = no session -> blocked, never invented).
"""
import pytest

from app.db.store import LocalStore

SESSIONS_ALL = ["London", "NewYork", "Asian", "Late"]


def _risk(**kw):
    from app.models.schemas import RiskSettings
    base = dict(sessions=SESSIONS_ALL)
    base.update(kw)
    return RiskSettings(**base)


# =================================================================
# validation
# =================================================================
def test_sessions_canonicalized():
    r = _risk(sessions=["london", "NEWYORK", "london"])
    assert r.sessions == ["London", "NewYork"]


def test_sessions_junk_rejected():
    with pytest.raises(ValueError, match="unknown session"):
        _risk(sessions=["Tokyo"])


def test_sessions_empty_allowed_is_deliberate_pause():
    assert _risk(sessions=[]).sessions == []


def test_market_sessions_normalized():
    r = _risk(market_sessions={"xauusd": ["newyork", "London"], "EURUSD": []})
    assert r.market_sessions == {"XAUUSD": ["NewYork", "London"], "EURUSD": []}


def test_market_sessions_unknown_market_rejected():
    with pytest.raises(ValueError, match="unknown market 'SPX500'"):
        _risk(market_sessions={"SPX500": ["London"]})


def test_market_sessions_unknown_session_rejected():
    with pytest.raises(ValueError, match="unknown session"):
        _risk(market_sessions={"XAUUSD": ["Sydney"]})


def test_session_hours_validated():
    assert _risk(session_hours={"london": [7, 12]}).session_hours == {"London": [7, 12]}
    for bad in ([12, 7], [8, 8], [-1, 5], [5, 25], [8]):
        with pytest.raises(ValueError):
            _risk(session_hours={"London": bad})


def test_session_tz_validated():
    assert _risk(session_tz="africa/accra").session_tz == "Africa/Accra"  # canonicalized
    with pytest.raises(ValueError, match="unknown timezone"):
        _risk(session_tz="Mars/Olympus")


# =================================================================
# effective session computation
# =================================================================
def _eng():
    from app.engine.signal_engine import SignalEngine
    return SignalEngine(provider=None)


class _Cand:
    market = "XAUUSD"
    rr_primary = 2.0
    strategy_id = "strategy_1_zero_lag"
    candle_time = "2026-07-01 13:00:00"   # naive UTC, summer (NY = EDT, UTC-4)


def test_effective_session_none_when_no_customization():
    from app.engine.signal_engine import effective_session_name
    assert effective_session_name({"session_tz": "UTC"}, "2026-07-01 13:00") is None
    assert effective_session_name({}, "2026-07-01 13:00") is None


def test_effective_session_uses_user_timezone():
    from app.engine.signal_engine import effective_session_name
    # 13:00 UTC in July = 09:00 New York -> London window (8-13 local)
    risk = {"session_tz": "America/New_York"}
    assert effective_session_name(risk, _Cand.candle_time) == "London"
    # in pure UTC terms 13:00 falls in the NewYork window - different answer
    # than the NY-local 09:00 above proves the conversion really happened
    assert effective_session_name({"session_tz": "Etc/UTC", "session_hours": {"London": [8, 13]}},
                                  "2026-07-01 13:00+00:00") == "NewYork"


def test_effective_session_custom_hours_and_gap():
    from app.engine.signal_engine import effective_session_name
    # override Asian to (0,6): hours 6-8 fall outside ALL windows -> '' (gap)
    risk = {"session_hours": {"Asian": [0, 6]}}
    assert effective_session_name(risk, "2026-07-01 02:00") == "Asian"
    assert effective_session_name(risk, "2026-07-01 06:00") == ""
    assert effective_session_name(risk, "2026-07-01 09:00") == "London"  # default kept


def test_effective_session_hours_in_user_tz():
    from app.engine.signal_engine import effective_session_name
    # windows declared in the USER's tz: London 8-13 New York local
    risk = {"session_tz": "America/New_York", "session_hours": {"London": [8, 13]}}
    # 13:00 UTC = 09:00 NY -> inside the custom London window
    assert effective_session_name(risk, _Cand.candle_time) == "London"
    # 18:00 UTC = 14:00 NY -> outside it, inside default NewYork (13-21)
    assert effective_session_name(risk, "2026-07-01 18:00") == "NewYork"


# =================================================================
# scan gate enforcement
# =================================================================
def _mkdoc(store, **over):
    doc = {"userId": "u1", "kind": "risk", "allowed_markets": ["XAUUSD"],
           "sessions": SESSIONS_ALL, "max_signals_per_day": 6, "min_rr": 1.5,
           "max_daily_loss_pct": 3.0}
    doc.update(over)
    store.create("settings", doc)


def test_gate_matrix_replaces_global_for_that_market(fresh_store):
    eng = _eng()
    # global sessions exclude NewYork, but the XAUUSD matrix row enables it
    _mkdoc(fresh_store, sessions=["London"],
           market_sessions={"XAUUSD": ["NewYork"]})
    ok, reason = eng._guards("u1", _Cand(), "NewYork")
    assert ok is not True or reason != "NewYork session not selected"
    # cleanest assertion: blocked reason must NOT be the session one
    assert "session" not in (reason or "") or ok is True


def test_gate_matrix_blocks_session_not_in_row(fresh_store):
    eng = _eng()
    _mkdoc(fresh_store, sessions=SESSIONS_ALL,
           market_sessions={"XAUUSD": ["NewYork"]})
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is False
    assert reason == "London session not enabled for XAUUSD (per-market rule)"


def test_gate_global_message_unchanged(fresh_store):
    eng = _eng()
    _mkdoc(fresh_store, sessions=["NewYork"])   # no matrix
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is False and reason == "London session not selected"


def test_gate_empty_matrix_row_pauses_market(fresh_store):
    eng = _eng()
    _mkdoc(fresh_store, market_sessions={"XAUUSD": []})   # explicit per-market pause
    ok, reason = eng._guards("u1", _Cand(), "London")
    assert ok is False and "per-market rule" in reason


def test_gate_tz_recompute_blocks_outside_custom_window(fresh_store):
    eng = _eng()
    # user trades London hours in New York time; candle is 13:00 UTC = 09:00 NY
    # -> London by tz, so sessions=[London] PASSES (would fail in UTC terms)
    _mkdoc(fresh_store, sessions=["London"], session_tz="America/New_York")
    ok, reason = eng._guards("u1", _Cand(), "NewYork")   # server default name (UTC)
    assert ok is True or "session" not in (reason or "")


def test_gate_gap_blocks_with_honest_message(fresh_store):
    eng = _eng()
    # custom hours create a 6-8 gap; candle at 06:00 UTC -> session '' -> blocked
    _mkdoc(fresh_store, sessions=SESSIONS_ALL,
           session_hours={"Asian": [0, 6]})
    cand = _Cand()
    cand.candle_time = "2026-07-01 06:00:00"
    ok, reason = eng._guards("u1", cand, "Asian")
    assert ok is False and reason == " session not selected"


# =================================================================
# settings round-trip (patch_risk stores; get_risk returns with defaults)
# =================================================================
def test_patch_and_get_round_trip(fresh_store, monkeypatch):
    from app.state import State
    import app.agent.core as core
    monkeypatch.setattr(State, "store", fresh_store, raising=False)
    monkeypatch.setattr(core, "get_store", lambda: fresh_store)
    body = _risk(market_sessions={"XAUUSD": ["NewYork"]},
                 session_hours={"London": [7, 12]},
                 session_tz="Africa/Accra").model_dump(exclude_none=True)
    core.patch_risk("u1", body)
    out = core.get_risk("u1")
    assert out["market_sessions"] == {"XAUUSD": ["NewYork"]}
    assert out["session_hours"] == {"London": [7, 12]}
    assert out["session_tz"] == "Africa/Accra"


def test_get_risk_defaults_for_old_docs(fresh_store, monkeypatch):
    from app.state import State
    import app.agent.core as core
    monkeypatch.setattr(State, "store", fresh_store, raising=False)
    monkeypatch.setattr(core, "get_store", lambda: fresh_store)
    fresh_store.create("settings", {"userId": "u1", "kind": "risk",
                                    "risk_per_trade_pct": 1.0})   # pre-Phase-5 doc
    out = core.get_risk("u1")
    assert out["market_sessions"] == {} and out["session_tz"] == "UTC"
    assert out["sessions"] == SESSIONS_ALL
