"""SPREAD GUARD (owner brief 2026-10-28, after SIG-20261028-014 filled
1.57 above the signal entry on gold and the ticket rode a 0.66 R:R):

  1. lot size is computed from the LIVE ask/bid (the price the order will
     actually fill at), not the signal price;
  2. a live spread that already squeezes the structural R:R below
     EXECUTION_MIN_FILL_RR refuses the order BEFORE submission;
  3. a fill that slips past the floor is auto-closed immediately;
  4. no quote available -> old behaviour (never block the flow);
  5. SL/TP levels are NEVER moved (reject, don't repair).
"""
import pytest

REF = {"price": 2400.0}


@pytest.fixture()
def exec_env(fresh_store, monkeypatch):
    """Same harness as test_44_execution_audit, plus a controllable /quote."""
    from app.execution import mt5 as m5
    uid = "u1"
    monkeypatch.setattr(m5, "user_mode", lambda u: "vps")
    monkeypatch.setattr(m5, "execution_enabled", lambda u: True)
    monkeypatch.setattr(m5, "_goals_doc", lambda u: {"risk_per_trade_pct": 1.0})
    monkeypatch.setattr(m5, "_executed_today", lambda u: 0)
    monkeypatch.setattr(m5, "_lot_mode", lambda u: "medium")
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "owner_user_id", uid, raising=False)
    import app.api.routes_accounts as ra
    monkeypatch.setattr(ra, "active_account", lambda u: None)
    import app.learning.versions as lv
    monkeypatch.setattr(lv, "lifecycle_of", lambda sid: "LIVE")
    import app.engine.daily as daily
    monkeypatch.setattr(daily, "is_entry_blocked", lambda u: (False, None, {}))
    monkeypatch.setattr(daily, "daily_state", lambda u: {"total_usd": 0.0,
                                                         "consecutive_losses": 0})
    import app.risk_checks as rc
    monkeypatch.setattr(rc, "check_entry", lambda *a, **k: (True, None, None))

    state = {"quote": None, "calls": []}

    def fake_bridge_get(path, timeout=8):
        if path == "/account":
            return {"balance": 10_000.0, "currency": "USD"}
        if path.startswith("/rates"):
            return {"bars": [{"c": REF["price"]}]}
        if path.startswith("/quote"):
            return state.get("quote")
        return None

    def fake_bridge_post(path, payload, timeout=15):
        state["calls"].append((path, dict(payload)))
        if path == "/execute":
            return {"ok": True, "ticket": 900001, "position_id": 900001,
                    "volume": payload["lots"], "price": state.get("fill_price", REF["price"]),
                    "symbol": "BROKERSYMBOL.m"}
        if path == "/close":
            return {"ok": True}
        return None

    monkeypatch.setattr(m5, "bridge_get", fake_bridge_get)
    monkeypatch.setattr(m5, "bridge_post", fake_bridge_post)
    return m5, state, uid


def _signal_doc(fresh_store, **kw):
    doc = {"signal_id": "SIG-20261028-900", "userId": "u1", "day": "2026-10-28",
           "strategy_id": "strategy_gold_orb", "strategy_name": "Gold ORB",
           "market": "XAUUSD", "timeframe": "5M", "direction": "BUY",
           "entry": 2400.0, "entry_zone": [2400.0, 2400.0], "sl": 2390.0,
           "tp1": 2415.0, "tp2": None, "tp3": None, "risk": 10.0,
           "rr_primary": 1.5, "score": 60, "candle_time": "2026-10-28T10:05:00+00:00",
           "status": "ACTIVE", "completed": False}
    doc.update(kw)
    return fresh_store.create("signals", doc)


def _doc(store, sig):
    return store.list("signals", filters={"id": sig["id"]}, limit=1)[0]


def test_sizing_uses_the_live_ask(exec_env, fresh_store):
    """BUY sized from the ask: wider quote -> smaller lot, same $ risk."""
    m5, state, uid = exec_env
    # ask 1.0 above entry on a 10.0 stop -> risk distance grows 10%, so the
    # lot must shrink ~10% vs sizing at the signal price.
    state["quote"] = {"bid": 2399.0, "ask": 2401.0}
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    sent = [p for pth, p in state["calls"] if pth == "/execute"]
    assert sent, f"order not sent: {_doc(fresh_store, sig).get('execution_status')}"
    lots_at_quote = sent[0]["lots"]
    state["quote"] = None                       # no quote -> old behaviour
    sig2 = _signal_doc(fresh_store, signal_id="SIG-20261028-901",
                       candle_time="2026-10-28T10:10:00+00:00")
    m5.execute_signal(sig2, uid)
    sent2 = [p for pth, p in state["calls"] if pth == "/execute"]
    lots_at_entry = sent2[-1]["lots"]
    assert lots_at_quote < lots_at_entry, "ask-based sizing did not shrink the lot"
    assert lots_at_entry * 10.0 == pytest.approx(lots_at_quote * 11.0, rel=0.02), \
        "risk is not conserved at ~$risk across the spread"


def test_spread_broken_rr_refused_before_broker(exec_env, fresh_store):
    """Quote sits so close to the SL that R:R from the live price < floor:
    no order reaches the broker; status SKIPPED_SPREAD_RR; levels untouched."""
    m5, state, uid = exec_env
    # entry 2400, SL 2390 (10R dist), TP 2415 (15 up). Ask 2406.5 ->
    # risk 16.5, reward 8.5 -> RR 0.51 < 0.80.
    state["quote"] = {"bid": 2405.5, "ask": 2406.5}
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    assert not [p for pth, _ in state["calls"] if pth == "/execute"], \
        "a spread-broken order reached the broker"
    doc = _doc(fresh_store, sig)
    assert doc["execution_status"] == "SKIPPED_SPREAD_RR"
    assert "spread guard" in (doc.get("mt5_note") or "")
    assert doc["sl"] == 2390.0 and doc["tp1"] == 2415.0   # never re-anchored


def test_slipped_fill_autocloses(exec_env, fresh_store):
    """The order passes the pre-trade quote but FILLS past the floor:
    the position is closed immediately, status EXECUTION_FAILED_SLIPPAGE."""
    m5, state, uid = exec_env
    state["quote"] = {"bid": 2399.2, "ask": 2400.2}       # fine -> RR ~1.43
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    # the broker fills 2409.2: risk 19.2, reward 5.8 -> executed RR 0.30
    state["fill_price"] = 2409.2
    state["calls"] = []
    m5.execute_signal(sig, uid)
    paths = [pth for pth, _ in state["calls"]]
    assert "/execute" in paths and "/close" in paths, paths
    doc = _doc(fresh_store, sig)
    assert doc["execution_status"] == "EXECUTION_FAILED_SLIPPAGE"
    assert "auto-closed" in (doc.get("mt5_note") or "")
    closed_payload = [p for pth, p in state["calls"] if pth == "/close"][-1]
    assert closed_payload["ticket"] == 900001


def test_no_quote_falls_back_to_old_behaviour(exec_env, fresh_store):
    """Bridge without /quote (pre-update) must NOT block the flow."""
    m5, state, uid = exec_env
    state["quote"] = None
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    assert [p for pth, p in state["calls"] if pth == "/execute"]
    assert _doc(fresh_store, sig)["execution_status"] == "SUBMITTED"
