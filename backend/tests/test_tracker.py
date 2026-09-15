

def test_unconfirmed_signal_still_tracked_to_outcome(monkeypatch):
    """User demand: a signal with NO user confirmation must still be recorded
    and tracked to TP/SL exactly like a confirmed one."""
    import os, tempfile
    import pandas as pd
    from app.db.store import LocalStore
    from app.engine import tracker as T

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(T, "get_store", lambda: store)
    notifs = []
    monkeypatch.setattr(T, "notify", lambda uid, typ, title, body, **k: notifs.append(title))

    class P:  # minimal provider stub: one 15M candle that touches TP2
        def get_candles(self, market, tf, limit=3):
            ts = pd.Timestamp("2026-09-14 12:00")
            return pd.DataFrame(
                {"open": [1.1000], "high": [1.1120], "low": [1.0990], "close": [1.1110]},
                index=[ts])

    store.create("signals", {
        "id": "sigU", "userId": "u1", "signal_id": "SIG-TEST-U", "market": "EURUSD",
        "timeframe": "15M", "direction": "BUY", "entry": 1.1000, "sl": 1.0950,
        "tp1": 1.1050, "tp2": 1.1100, "tp3": None, "risk": 0.0050,
        "strategy_name": "Zero Lag Trend", "strategy_id": "strategy_1_zero_lag",
        "status": "ACTIVE", "completed": False, "user_action": None,   # NEVER confirmed
        "candle_time": "2026-09-14 11:45:00",
    })
    t = T.SignalTracker(P())
    t.update_market("EURUSD")   # tick 1: touches TP1 (one event per candle, by design)
    assert store.get("signals", "sigU")["status"] == "TP1_HIT"
    t.update_market("EURUSD")   # tick 2: continues tracking to TP2 and completes

    doc = store.get("signals", "sigU")
    assert doc["completed"] is True                       # recorded to outcome
    assert doc["status"] == "TP2_HIT" and doc["outcome"] == "WIN"
    assert doc["r_multiple"] == 2.0                       # 100 pips risk -> 2R at TP2
    # the result was announced even though the user never confirmed the signal
    assert any("WIN" in t for t in notifs)
