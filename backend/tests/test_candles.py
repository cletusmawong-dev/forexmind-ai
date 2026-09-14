"""Candle storage: real rows only, dedup by timestamp, honest stats."""
import os
import tempfile

import pandas as pd
import pytest

from app.market_data import candle_store
from app.db.store import LocalStore


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    candle_store._mem.clear()
    candle_store._hydrated.clear()
    tmp = os.path.join(tempfile.mkdtemp(), "db.json")
    monkeypatch.setattr(candle_store, "get_store", lambda: LocalStore(path=tmp))


def _df(ts_list, base=1.1000):
    idx = pd.to_datetime(ts_list, unit="s", utc=True).tz_localize(None)
    n = len(ts_list)
    return pd.DataFrame(
        {"open": [base] * n, "high": [base + 0.001] * n,
         "low": [base - 0.001] * n, "close": [base + 0.0005] * n}, index=idx)


def test_record_dedup_and_history():
    candle_store._mem.clear(); candle_store._hydrated.clear()
    t = 1700000000 - (1700000000 % 900)
    ts = [t + i * 900 for i in range(10)]
    assert candle_store.record("EURUSD", _df(ts)) == 10
    # same data again -> nothing new
    assert candle_store.record("EURUSD", _df(ts)) == 0
    # one new candle
    assert candle_store.record("EURUSD", _df(ts + [ts[-1] + 900])) == 1
    h = candle_store.history("EURUSD", limit=5)
    assert len(h) == 5 and h[0]["ts"] == ts[6] and h[-1]["ts"] == ts[-1] + 900
    assert set(h[0].keys()) == {"ts", "open", "high", "low", "close"}


def test_unknown_market_ignored():
    candle_store._mem.clear(); candle_store._hydrated.clear()
    t = 1700000000 - (1700000000 % 900)
    assert candle_store.record("BTCUSD", _df([t])) == 0
    assert candle_store.history("BTCUSD") == []


def test_stats_shape():
    candle_store._mem.clear(); candle_store._hydrated.clear()
    t = 1700000000 - (1700000000 % 900)
    candle_store.record("GBPUSD", _df([t, t + 900]))
    st = candle_store.stats()
    assert st["total"] == 2 and st["markets"]["GBPUSD"]["count"] == 2
    assert st["markets"]["EURUSD"]["count"] == 0
    assert st["tf"] == "15M"
