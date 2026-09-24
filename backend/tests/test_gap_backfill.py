"""Gap auto-backfill: splice missing candles from the alternate feed.

Incident 2026-09-23/24: an unrepaired 45-min hole in the XAUUSD feed kept
the integrity gate pausing ALL XAUUSD signal generation for ~5 hours -
every 9/21 EMA crossover inside the gap was silently lost (user report:
"the app is missing signs formed by the EMA strategy"). The engine now
repairs gaps from the second feed at detection time.
"""
import pandas as pd
import pytest

from app.market_data.gap_repair import backfill_gaps
from app.market_data.integrity import series_is_trustworthy


def _mk(ts: int, close: float) -> dict:
    return {"open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 100}


def _series(stamps, closes):
    idx = pd.DatetimeIndex([pd.Timestamp(t, unit="s") for t in stamps])
    return pd.DataFrame({"open": closes, "high": [c + 1 for c in closes],
                         "low": [c - 1 for c in closes], "close": closes,
                         "volume": [100] * len(closes)}, index=idx)


def test_gap_is_detected_and_series_untrustworthy():
    stamps = [i * 900 for i in range(50)]
    del stamps[30:33]                      # hole: 3 missing 15M bars
    closes = [100 + i * 0.1 for i in range(len(stamps))]
    df = _series(stamps, closes)
    check = series_is_trustworthy(stamps, "15M", 10)
    assert not check["ok"] and check["reason"] == "data_gap"
    repaired, added = backfill_gaps(
        df, "XAUUSD", "15M",
        fetch_alt=lambda m, tf, n: _series([t for t in range(stamps[0], stamps[0] + 50 * 900, 900)],
                                           [100 + (t - stamps[0]) / 900 * 0.1 for t in range(50)]))
    assert added == 3
    assert series_is_trustworthy([int(pd.Timestamp(x).timestamp()) for x in repaired.index],
                                 "15M", 10)["ok"]


def test_no_gap_is_a_noop():
    stamps = [i * 900 for i in range(40)]
    df = _series(stamps, [100 + i for i in range(40)])
    called = []
    repaired, added = backfill_gaps(df, "EURUSD", "15M",
                                    fetch_alt=lambda *a: called.append(1))
    assert added == 0 and not called and repaired is df


def test_alternate_feed_unavailable_pauses_honestly():
    stamps = [i * 900 for i in range(50)]
    del stamps[30:33]
    df = _series(stamps, [100 + i * 0.1 for i in range(len(stamps))])
    repaired, added = backfill_gaps(df, "XAUUSD", "15M", fetch_alt=lambda *a: None)
    assert added == 0 and repaired is df     # engine keeps pausing loudly


def test_backfill_never_fabricates():
    """Bars that the alternate feed does NOT have are NOT invented."""
    stamps = [i * 900 for i in range(50)]
    del stamps[30:33]
    df = _series(stamps, [100 + i * 0.1 for i in range(len(stamps))])
    # alternate feed has only the FIRST of the 3 missing bars
    have_ts = stamps[29] + 900
    alt = _series([have_ts], [110.0])
    repaired, added = backfill_gaps(df, "XAUUSD", "15M", fetch_alt=lambda *a: alt)
    assert added == 1
    assert pd.Timestamp(have_ts, unit="s") in repaired.index
    assert pd.Timestamp(stamps[29] + 1800, unit="s") not in repaired.index


def test_engine_scan_recovers_after_backfill():
    """scan() on a gapped series with a working alternate feed must NOT log
    the pause - the gap is repaired and scanning continues to strategies."""
    import tempfile
    from app.db.store import LocalStore
    from app.db import store as sm
    store = LocalStore(path=tempfile.mktemp())
    sm._store = store
    store.create("settings", {"userId": "u1", "kind": "risk"})
    from app.engine.signal_engine import SignalEngine

    stamps = [i * 900 for i in range(400)]
    del stamps[350:352]
    full = _series([t for t in range(0, 400 * 900, 900)], [100 + t / 900 * 0.1 for t in range(400)])
    gapped = full.loc[[pd.Timestamp(t, unit="s") for t in stamps]]

    class FakeProvider:
        def get_candles(self, market, tf, limit=600):
            return gapped
        def higher_frames(self, market, tf):
            return {}

    eng = SignalEngine(FakeProvider())
    eng._log = lambda *a, **k: None
    created = eng.scan("u1", "XAUUSD", "15M")     # must not raise/pause
    assert created == []                          # no crossover in flat data - fine
