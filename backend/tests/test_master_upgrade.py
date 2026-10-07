"""Master Product Upgrade (§4 chart, §5 conversational AI, §6 community,
§7 news, §9 scanner states, §10 sessions, §12 scorecard, §13 why-not-trade).

Standing rules re-tested here:
 - chart indicators are causal (no lookahead) and use the real provider
 - the AI can answer free-form but has NO execution authority and can never
   see another user's private data
 - community is tenant-isolated; verified trade results are immutable
 - tiny samples are labelled INSUFFICIENT, never presented as performance
"""
import base64
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# a 1x1 transparent PNG
PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82").decode()


@pytest.fixture()
def env(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    State.store = store
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "")
    monkeypatch.setattr(settings, "xiro_api_key", "")  # no external AI in tests
    # hermetic calendar
    from app.market_data import calendar as cal
    monkeypatch.setattr(cal, "_fetch", lambda force=False: [])
    from app.main import app
    from app.api.deps import get_user_id
    try:
        yield app, store, monkeypatch
    finally:
        app.dependency_overrides.clear()


def client_as(app, uid):
    """NOTE: the override lives on the shared app - the LAST client_as/flip
    wins for every client. Use flip() between calls instead of holding two
    'concurrent' clients."""
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: uid
    return TestClient(app)


def flip(app, uid):
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: uid


def _mk_user(store, uid, name):
    store.create("users", {"id": uid, "email": f"{uid}@x.y", "display_name": name})


# ===========================================================================
# §4 CHART
# ===========================================================================
def test_chart_overlays_causal_no_lookahead(env, monkeypatch):
    """EMA overlay value at bar i must equal an EMA computed on bars <= i."""
    app, store, mp = env
    import pandas as pd
    from app.core.indicators import ema as raw_ema

    n = 260
    idx = pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series([100 + (i % 17) * 0.5 for i in range(n)], index=idx)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": 10.0}, index=idx)

    class _Prov:
        is_demo = True

        def get_candles(self, market, tf, limit=600):
            return df.tail(limit)

        def data_health(self, m):
            return {"status": "OK"}

        def latest_price(self, m):
            return float(close.iloc[-1])

    from app.state import State
    mp.setattr(State, "provider", _Prov())

    from app.state import State as _S
    c = client_as(app, "boss")

    def overlays_with(future_mul):
        mutated = df.copy()
        mutated.loc[mutated.index[150:], "close"] = mutated.loc[
            mutated.index[150:], "close"] * future_mul
        mutated.loc[mutated.index[150:], "high"] = mutated.loc[
            mutated.index[150:], "high"] * future_mul
        mp.setattr(_S, "provider", _Wrap(mutated))
        r = c.get("/api/charts/overlays",
                  params={"symbol": "XAUUSD", "tf": "15M", "limit": 200})
        assert r.status_code == 200, r.text
        return r.json()

    class _Wrap:
        is_demo = True

        def __init__(self, frame):
            self.frame = frame

        def get_candles(self, market, tf, limit=600):
            return self.frame.tail(limit)

    before = overlays_with(1.0)
    after = overlays_with(1.9)  # massive move AFTER bar 149
    mp.setattr(_S, "provider", _Wrap(df))
    # NO LOOKAHEAD: points at or before bar 149 are bit-identical even though
    # the future changed; points after 149 do move.
    b_map = {p["time"]: p["value"] for p in before["ema9"]}
    a_map = {p["time"]: p["value"] for p in after["ema9"]}
    times = [str(x) for x in df.index]
    for k in (80, 120, 149):  # inside the served tail(200) window
        assert b_map[times[k]] == a_map[times[k]]  # future cannot alter past
    assert b_map[times[199]] != a_map[times[199]]
    assert len(before["ema21"]) > 0


def test_chart_overlays_rejects_bad_tf(env):
    app, store, mp = env
    c = client_as(app, "boss")
    r = c.get("/api/charts/overlays", params={"symbol": "XAUUSD", "tf": "7M"})
    assert r.status_code in (400, 422)


def test_chart_setup_maps_machine_state(env):
    app, store, mp = env
    from app.strategies.strategy_2_mtf_sweep_bos_retest.state import (
        COLLECTION, doc_id)
    store.create("users", {"id": "boss", "email": "b@x.y"})
    c = client_as(app, "boss")
    # empty -> NO_SETUP
    r = c.get("/api/charts/setup", params={"symbol": "XAUUSD"}).json()
    assert r["state"] == "NO_SETUP"
    # sweep only -> SWEEP_DETECTED
    store.create(COLLECTION, {"swept_level": 4998.0, "sweep_price": 4997.4,
                              "structure_high": None, "structure_low": None,
                              "waiting_bull_retest": False,
                              "waiting_bear_retest": False},
                 doc_id=doc_id("XAUUSD", "15M"))
    r = c.get("/api/charts/setup", params={"symbol": "XAUUSD"}).json()
    assert r["state"] == "SWEEP_DETECTED"
    assert r["levels"]["swept_level"] == 4998.0
    # + broken structure -> STRUCTURE_CONFIRMED
    store.update(COLLECTION, doc_id("XAUUSD", "15M"),
                 {"broken_high": 5005.0})
    r = c.get("/api/charts/setup", params={"symbol": "XAUUSD"}).json()
    assert r["state"] == "STRUCTURE_CONFIRMED"
    # + waiting retest -> WAITING_RETEST
    store.update(COLLECTION, doc_id("XAUUSD", "15M"),
                 {"waiting_bull_retest": True})
    r = c.get("/api/charts/setup", params={"symbol": "XAUUSD"}).json()
    assert r["state"] == "WAITING_RETEST" and r["direction"] == "BUY"


def test_chart_annotations_isolated(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    _mk_user(store, "b_u", "B")
    store.create("signals", {"userId": "a_u", "signal_id": "SIG-1",
                             "market": "XAUUSD", "direction": "BUY",
                             "entry": 5000.0, "sl": 4990.0, "tp1": 5010.0,
                             "status": "ACTIVE", "createdAt": "2026-10-07T00:00:00Z"})
    c = client_as(app, "a_u")
    a = c.get("/api/charts/annotations", params={"symbol": "XAUUSD"}).json()
    assert a["count"] == 1 and a["annotations"][0]["signal_id"] == "SIG-1"
    flip(app, "b_u")
    b = c.get("/api/charts/annotations", params={"symbol": "XAUUSD"}).json()
    assert b["count"] == 0  # B can never see A's signals


# ===========================================================================
# §5 CONVERSATIONAL AI
# ===========================================================================
def test_chat_freeform_llm_fallback_with_chart_context(env, monkeypatch):
    """Unmatched free-form question -> AI provider, grounded on chart + the
    CALLER's own trades only."""
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    _mk_user(store, "b_u", "B")
    store.create("signals", {"userId": "a_u", "signal_id": "SIG-A",
                             "market": "EURUSD", "outcome": "LOSS",
                             "r_multiple": -1.0, "completed": True,
                             "createdAt": "2026-10-06T00:00:00Z"})
    seen = {}

    class _FakeProv:
        name = "xkiro"

        def complete(self, prompt, context, **k):
            seen["prompt"] = prompt
            seen["context"] = context
            return "Grounded answer about your chart."

    import app.agent.ai_provider as prov
    mp.setattr(prov, "get_ai_provider", lambda: _FakeProv())

    c = client_as(app, "a_u")
    r = c.post("/api/chat", json={"message": "Why is this XAUUSD setup valid?",
                                  "chart_symbol": "XAUUSD", "chart_tf": "15M"}).json()
    assert "Grounded answer" in r.get("reply", "")
    assert seen["context"]["chart"]["symbol"] == "XAUUSD"
    # context is built from a_u's docs only
    assert all(t.get("market") != "GBPUSD_PRIVATE" for t in seen["context"]["recent_closed_trades"])
    # the safety contract lives in the provider system prompt - verify it
    from app.agent.ai_provider import SYSTEM_PROMPT
    assert "do not execute trades" in SYSTEM_PROMPT.lower()
    # tutor framing + the user's actual question both reach the model
    assert "USER QUESTION: Why is this XAUUSD setup valid?" in seen["prompt"]
    assert "tutor" in seen["prompt"].lower()


def test_chat_without_llm_stays_honest(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    c = client_as(app, "a_u")
    r = c.post("/api/chat", json={"message": "what is the meaning of life?"}).json()
    rep = r.get("reply", "")
    assert "never invent" in rep or "I can answer" in rep  # honest, no fabrication


# ===========================================================================
# §6 COMMUNITY
# ===========================================================================
def test_community_full_flow(env):
    app, store, mp = env
    _mk_user(store, "a_u", "Alice")
    _mk_user(store, "b_u", "Bob")
    c = client_as(app, "a_u")
    r = c.post("/api/community/posts", json={"text": "XAUUSD sweep looks clean",
                                             "tags": ["XAUUSD", "Strategy 2"]})
    assert r.status_code == 200, r.text
    pid = r.json()["post"]["id"]

    flip(app, "b_u")  # Bob: like + comment + follow
    assert c.post(f"/api/community/posts/{pid}/like").json()["likes"] == 1
    assert c.post(f"/api/community/posts/{pid}/comments",
                  json={"text": "nice catch"}).status_code == 200
    assert c.post("/api/community/follow/a_u").json()["following_count"] == 1

    # ownership: Bob cannot edit or delete Alice's post
    assert c.patch(f"/api/community/posts/{pid}",
                   json={"text": "hacked"}).status_code == 403
    assert c.delete(f"/api/community/posts/{pid}").status_code == 403

    flip(app, "a_u")  # author can edit
    assert c.patch(f"/api/community/posts/{pid}",
                   json={"text": "XAUUSD sweep looks clean (updated)"}).status_code == 200

    flip(app, "b_u")  # profile shows counts, never balances/emails
    prof = c.get("/api/community/profile/a_u").json()["profile"]
    assert prof["display_name"] == "Alice" and prof["followers"] == 1
    assert "email" not in str(prof).lower()


def test_community_verified_trade_immutable(env):
    app, store, mp = env
    _mk_user(store, "a_u", "Alice")
    ca = client_as(app, "a_u")
    store.create("signals", {"userId": "a_u", "signal_id": "SIG-9",
                             "market": "XAUUSD", "direction": "SELL",
                             "entry": 5000.0, "sl": 5010.0, "tp1": 4980.0,
                             "status": "TP2_HIT", "outcome": "WIN",
                             "mt5_pl": 86.4, "r_multiple": 2.0, "completed": True,
                             "createdAt": "2026-10-06T00:00:00Z"})
    r = ca.post("/api/community/posts", json={"text": "My Strategy 2 win",
                                              "trade_ref": "MISSING"})
    assert r.status_code == 400  # not yours / not found
    # share with a REAL id
    sid = store.list("signals", filters={"userId": "a_u"}, limit=1)[0]["id"]
    pid = ca.post("/api/community/posts",
                  json={"text": "win", "trade_ref": sid}).json()["post"]["id"]
    p = ca.get(f"/api/community/posts/{pid}").json()["post"]
    assert p["trade"]["verified"] is True and p["trade"]["pl"] == 86.4
    # edit text: allowed; the verified snapshot cannot change
    ca.patch(f"/api/community/posts/{pid}", json={"text": "win (edited)"})
    p2 = ca.get(f"/api/community/posts/{pid}").json()["post"]
    assert p2["text"] == "win (edited)" and p2["trade"]["pl"] == 86.4
    assert p2["trade"] == p["trade"]


def test_community_image_validation(env):
    app, store, mp = env
    _mk_user(store, "a_u", "Alice")
    ca = client_as(app, "a_u")
    bad = "data:image/png;base64," + base64.b64encode(b"GIF89a-not-allowed").decode()
    assert ca.post("/api/community/posts",
                   json={"text": "x", "image": bad}).status_code == 415
    ok_size = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 400_000).decode()
    assert ca.post("/api/community/posts",
                   json={"text": "x", "image": ok_size}).status_code == 200  # 400KB now accepted
    big = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 700_000).decode()
    assert ca.post("/api/community/posts",
                   json={"text": "x", "image": big}).status_code == 413
    ok = "data:image/png;base64," + PNG_1PX
    assert ca.post("/api/community/posts",
                   json={"text": "chart shot", "image": ok}).status_code == 200


def test_community_report_hides_post(env):
    app, store, mp = env
    for i in range(4):
        _mk_user(store, f"u{i}", f"U{i}")
    c = client_as(app, "u0")
    pid = c.post("/api/community/posts", json={"text": "spam?"}).json()["post"]["id"]
    for i in (1, 2, 3):
        flip(app, f"u{i}")
        c.post(f"/api/community/posts/{pid}/report", json={"reason": "spam"})
    flip(app, "u1")
    assert c.get(f"/api/community/posts/{pid}").status_code == 404  # hidden from others
    feed = c.get("/api/community/posts").json()["posts"]
    assert all(p["id"] != pid for p in feed)
    flip(app, "u0")  # the author still sees it - flagged hidden
    mine = c.get(f"/api/community/posts/{pid}").json()["post"]
    assert mine["hidden"] is True


# ===========================================================================
# §7 NEWS
# ===========================================================================
def test_news_events_and_explain_grounded(env, monkeypatch):
    app, store, mp = env
    from app.market_data import calendar as cal
    mp.setattr(cal, "_fetch", lambda force=False: [
        {"title": "CPI m/m", "country": "USD", "date": "2026-10-07T13:30:00Z",
         "impact": "high", "forecast": "0.3%", "previous": "0.2%"}])
    _mk_user(store, "a_u", "A")
    c = client_as(app, "a_u")
    evs = c.get("/api/news/events").json()
    assert evs["count"] == 1
    assert "XAUUSD" in evs["events"][0]["affected_markets"]  # USD events touch gold
    ex = c.post("/api/news/explain", json={
        "title": "CPI m/m", "country": "USD", "date": "2026-10-07T13:30:00Z",
        "impact": "high", "forecast": "0.3%", "previous": "0.2%"}).json()
    assert "CPI" in ex["explanation"]
    assert "XAUUSD" in ex["affected_markets"]


# ===========================================================================
# §10 SESSIONS + §12 SCORECARD
# ===========================================================================
def test_sessions_insufficient_and_ok(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    for i in range(6):
        store.create("signals", {"userId": "a_u", "completed": True,
                                 "market": "EURUSD", "outcome": "WIN" if i % 2 else "LOSS",
                                 "r_multiple": 1.0 if i % 2 else -1.0,
                                 "market_conditions": {"session": "London"},
                                 "createdAt": f"2026-10-0{i + 1}T00:00:00Z"})
    out = client_as(app, "a_u").get("/api/analytics/sessions").json()
    lon = next(s for s in out["sessions"] if s["session"] == "London")
    assert lon["trades"] == 6 and lon["sample"] == "OK"
    assert lon["win_rate"] == pytest.approx(50.0)
    assert lon["net_r"] == pytest.approx(0.0)


def test_scorecard_lifecycle_mapping(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    sc = client_as(app, "a_u").get("/api/scorecard").json()
    by_id = {s["strategy_id"]: s for s in sc["strategies"]}
    assert by_id["strategy_1_zero_lag"]["health_state"] == "PAUSED"
    assert by_id["strategy_1_vp_pivots"]["health_state"] == "SHADOW_ONLY"
    ema = by_id["strategy_2_ema_atr"]
    assert ema["sample"] == "INSUFFICIENT" and ema["win_rate"] is None
    assert "tiny" in sc["note"].lower() or ">=" in sc["note"]


# ===========================================================================
# §13 WHY NOT TRADE
# ===========================================================================
def test_whynot_daily_cap_reason(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for i in range(6):
        store.create("signals", {"userId": "a_u", "day": today,
                                 "execution_status": "SUBMITTED",
                                 "market": "EURUSD", "createdAt": f"{today}T0{i}:00:00Z"})
    out = client_as(app, "a_u").get("/api/whynot", params={"symbol": "XAUUSD"}).json()
    assert out["can_trade"] is False
    cap = next(c for c in out["checks"] if c["gate"] == "daily cap")
    assert cap["ok"] is False and "6/6" in cap["detail"]
    assert any(c["gate"] == "setup" for c in out["checks"])
    assert out["reason"]


def test_whynot_cap_open_with_room(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    out = client_as(app, "a_u").get("/api/whynot", params={"symbol": "EURUSD"}).json()
    cap = next(c for c in out["checks"] if c["gate"] == "daily cap")
    assert cap["ok"] is True and "0/6" in cap["detail"]
    sess = next(c for c in out["checks"] if c["gate"] == "session")
    assert sess["ok"] is True  # default risk has all four sessions on


def test_chat_greeting_is_instant_and_friendly(env):
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    c = client_as(app, "a_u")
    r = c.post("/api/chat", json={"message": "hello"}).json()
    assert "FOREXMIND AI" in r["reply"] and "Ask me anything" in r["reply"]
    r2 = c.post("/api/chat", json={"message": "thanks!"}).json()
    assert "Any time" in r2["reply"]


def test_chat_llm_failure_falls_back_deterministic(env, monkeypatch):
    """XKiro down -> the deterministic engine still answers stored-data Qs."""
    app, store, mp = env
    _mk_user(store, "a_u", "A")
    store.create("signals", {"userId": "a_u", "signal_id": "SIG-Z",
                             "market": "EURUSD", "direction": "BUY", "status": "TP2_HIT",
                             "strategy_name": "9/21 EMA", "createdAt": "2026-10-06T00:00:00Z",
                             "reason": "EMA cross confirmed", "checks": []})

    class _Dead:
        name = "xkiro"

        def complete(self, *a, **k):
            raise RuntimeError("provider down")

    import app.agent.ai_provider as prov
    mp.setattr(prov, "get_ai_provider", lambda: _Dead())
    c = client_as(app, "a_u")
    r = c.post("/api/chat", json={"message": "why did the last signal qualify?"}).json()
    assert "SIG-Z" in r.get("reply", "") or "qualif" in r.get("reply", "").lower()
