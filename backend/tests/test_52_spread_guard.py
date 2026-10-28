"""ALWAYS-ENTER + RE-ANCHOR execution (owner directive 2026-10-28).

"Once the signal is generated it should be entered. The app should just make
sure to calculate the SL and TP the right way." Strategies fire rarely - a
generated signal is never refused and never cancelled for market movement:

  1. lot size is computed from the LIVE ask/bid (the price the order will
     actually fill at) - the $ risk stays exactly the configured risk-%;
  2. SL/TP are re-anchored to that live price with the strategy's stop and
     TP distances preserved EXACTLY (structure + R:R untouched);
  3. a slipped fill is recorded honestly and the trade STAYS OPEN - no
     auto-close, no cancellation after entry;
  4. no quote available -> signal's own levels, old behaviour;
  5. big market drift does NOT refuse the order anymore (ENTRY_STALE is
     superseded by re-anchoring - the 2026-10-28 EURUSD/NAS100 refusals).
"""
import pytest

REF = {"price": 2400.0}


@pytest.fixture()
def exec_env(fresh_store, monkeypatch):
    """Same harness family as test_44_execution_audit, plus a controllable /quote."""
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

    state = {"quote": None, "fill_price": None, "calls": []}

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
                    "volume": payload["lots"], "price": state.get("fill_price") or REF["price"],
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


def _orders(state):
    return [p for pth, p in state["calls"] if pth == "/execute"]


def test_sizing_uses_the_live_ask(exec_env, fresh_store):
    """BUY sized from the ask: wider quote -> smaller lot, same $ risk."""
    m5, state, uid = exec_env
    # ask +1.0 on a 10.0 stop -> risk distance grows 10%, lot must shrink ~10%.
    state["quote"] = {"bid": 2399.0, "ask": 2401.0}
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    lots_at_quote = _orders(state)[-1]["lots"]
    state["quote"] = None                       # no quote -> signal-price sizing
    sig2 = _signal_doc(fresh_store, signal_id="SIG-20261028-901",
                       candle_time="2026-10-28T10:10:00+00:00")
    m5.execute_signal(sig2, uid)
    lots_at_entry = _orders(state)[-1]["lots"]
    assert lots_at_quote < lots_at_entry, "ask-based sizing did not shrink the lot"
    assert lots_at_entry * 10.0 == pytest.approx(lots_at_quote * 11.0, rel=0.02), \
        "risk is not conserved at ~$risk across the spread"


def test_sl_tp_reanchored_to_the_live_price(exec_env, fresh_store):
    """Spread/movement never skips the entry: SL/TP shift by the same delta
    as the anchor - distances, sides and R:R are preserved exactly."""
    m5, state, uid = exec_env
    # entry 2400, SL 2390 (10.0 stop), TP 2415 (15.0 target, 1.5R).
    # Live ask 2406.5 (a gold-spike style print): the old code refused here.
    state["quote"] = {"bid": 2405.5, "ask": 2406.5}
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    orders = _orders(state)
    assert orders, f"order not sent: {_doc(fresh_store, sig).get('execution_status')}"
    p = orders[-1]
    assert p["sl"] == pytest.approx(2396.5)     # 2390 + 6.5
    assert p["tp"] == pytest.approx(2421.5)     # 2415 + 6.5
    assert (p["tp"] - 2406.5) / (2406.5 - p["sl"]) == pytest.approx(1.5), \
        "re-anchoring changed the strategy R:R"
    doc = _doc(fresh_store, sig)
    assert doc["execution_status"] == "SUBMITTED"
    assert "re-anchored" in (doc.get("mt5_note") or "")


def test_far_drift_still_enters_reanchored(exec_env, fresh_store):
    """The ENTRY_STALE refusals from 2026-10-28 (EURUSD 08:01, NAS100 12:03)
    must never happen again: the market running away re-anchors, not refuses."""
    m5, state, uid = exec_env
    state["quote"] = {"bid": 2429.0, "ask": 2430.0}     # +30 = 3x the stop distance
    REF["price"] = 2430.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    p = _orders(state)[-1]
    assert p["sl"] == pytest.approx(2420.0)     # stop distance preserved
    assert p["tp"] == pytest.approx(2445.0)     # TP distance preserved
    assert _doc(fresh_store, sig)["execution_status"] == "SUBMITTED"


def test_slipped_fill_stays_open_no_autoclose(exec_env, fresh_store):
    """A fill beyond the quote is recorded honestly; the trade is NEVER
    auto-closed (owner: no cancellation once the signal enters)."""
    m5, state, uid = exec_env
    state["quote"] = {"bid": 2399.2, "ask": 2400.2}     # re-anchor: SL 2390.2, TP 2415.2
    state["fill_price"] = 2409.2                        # broker filled 9.0 above the quote
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    paths = [pth for pth, _ in state["calls"]]
    assert "/execute" in paths and "/close" not in paths, paths
    doc = _doc(fresh_store, sig)
    assert doc["execution_status"] == "SUBMITTED"
    assert doc["mt5_ticket"] == 900001
    assert doc["executed_rr"] is not None               # honest record


def test_no_quote_keeps_signal_levels(exec_env, fresh_store):
    """Bridge without /quote (pre-update) must NOT block the flow and keeps
    the signal's own levels exactly."""
    m5, state, uid = exec_env
    state["quote"] = None
    REF["price"] = 2400.0
    sig = _signal_doc(fresh_store)
    m5.execute_signal(sig, uid)
    p = _orders(state)[-1]
    assert p["sl"] == 2390.0 and p["tp"] == 2415.0
    assert _doc(fresh_store, sig)["execution_status"] == "SUBMITTED"
