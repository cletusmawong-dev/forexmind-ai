"""Missed-signal root-cause regression tests (2026-09-16).

Production bug this pins: the provider cache keyed (market, tf) WITHOUT the
limit let a small-limit caller (tracker limit=3, chart limit=140) overwrite
the slot on TTL expiry. The next scan then received len(df) < 300, bailed
silently (log_activity=False), and Strategy 2 fired ~0 signals despite
dozens of valid EMA crosses. Fix: cache always stores a full history
(floor 300 bars); small callers are served via tail().
"""
import pandas as pd
import pytest


@pytest.fixture()
def provider(monkeypatch):
    from app.market_data.live_provider import LiveProvider
    p = LiveProvider()
    p.td_key = ""       # force deterministic Yahoo-stub path
    p.oanda_token = ""

    calls = []

    def fake_fetch(market, tf, limit):
        calls.append(int(limit))
        n = max(int(limit), 10)
        idx = pd.date_range("2026-08-01", periods=min(n, 500), freq="15min")  # past: nothing looks "forming"
        return pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1},
                            index=idx)

    monkeypatch.setattr(p, "_fetch_oanda", lambda m, tf, lim: None)
    monkeypatch.setattr(p, "_fetch_yahoo", fake_fetch)
    p._test_calls = calls
    return p


def test_small_limit_call_never_poisons_cache(provider):
    """THE regression: tracker-style limit=3 call, then a scan-style
    limit=1600 call within the TTL - the scan must still see >= 300 bars."""
    small = provider.get_candles("XAUUSD", "15M", limit=3)
    assert len(small) == 3
    big = provider.get_candles("XAUUSD", "15M", limit=1600)
    assert big is not None and len(big) >= 300, (
        "small-limit caller shrank the shared cache - scan would starve again")


def test_refetch_always_uses_full_floor(provider):
    """Whatever triggers the refetch, the FETCH size is floored at 300 bars."""
    provider.get_candles("EURUSD", "15M", limit=3)   # refetch happens here
    assert provider._test_calls[-1] >= 300
    provider._cache.clear()                          # force another refetch
    provider.get_candles("GBPUSD", "1H", limit=50)
    assert provider._test_calls[-1] >= 300


def test_starved_scan_is_now_visible_in_activity(monkeypatch):
    """scan() with insufficient data must log a DATA entry even when the
    caller passes log_activity=False (the silence hid the bug for a day)."""
    import os, tempfile
    from app.db.store import LocalStore
    import app.engine.signal_engine as SE

    store = LocalStore(path=os.path.join(tempfile.mkdtemp(), "db.json"))
    monkeypatch.setattr(SE, "get_store", lambda: store)
    logs = []
    monkeypatch.setattr(SE.SignalEngine, "_log",
                        lambda self, msg, kind="INFO", **k: logs.append((kind, msg)))

    class TinyProvider:
        def get_candles(self, market, tf, limit=600):
            idx = pd.date_range("2026-09-15", periods=50, freq="15min")
            return pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": 1}, index=idx)

        def higher_frames(self, market, tf):
            return {}

    eng = SE.SignalEngine(TinyProvider())
    created = eng.scan("u1", "XAUUSD", "15M", log_activity=False)
    assert created == []
    assert any(k == "DATA" and "unavailable" in m for k, m in logs), (
        "data starvation must be visible in agent activity")
