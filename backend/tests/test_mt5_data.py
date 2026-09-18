"""MT5-bridge-as-data-source: when the VPS is connected, candles come from the
terminal (broker ground truth); Twelve Data/Yahoo stay as honest fallback."""
import time

import pytest

from app.market_data import live_provider as lp


@pytest.fixture()
def prov():
    return lp.LiveProvider()


def _bars(n=100, base=1.1):
    return [{"ts": 1789700000 + i * 900, "o": base, "h": base + 0.1,
             "l": base - 0.1, "c": base + 0.05, "v": 10} for i in range(n)]


def test_fetch_mt5_builds_td_schema(prov, monkeypatch):
    import app.execution.mt5 as mt5x
    monkeypatch.setattr(mt5x, "bridge_get",
                        lambda path, timeout=8: {"ok": True, "symbol": "XAUUSDm",
                                                 "tf": "M15", "bars": _bars()})
    df = prov._fetch_mt5("XAUUSD", "15M", 300)
    assert df is not None and len(df) == 100
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.tz is None                    # naive-UTC like the TD path
    from app.config import settings
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700", raising=False)
    prov._mt5_ok_ts = time.time()
    assert prov.data_source() == "mt5_vps"


def test_fetch_mt5_failure_is_honest(prov, monkeypatch):
    import app.execution.mt5 as mt5x
    monkeypatch.setattr(mt5x, "bridge_get", lambda path, timeout=8: None)
    assert prov._fetch_mt5("XAUUSD", "15M", 300) is None
    assert prov.data_source() == "twelvedata"


def test_cached_prefers_mt5_when_bridge_configured(prov, monkeypatch):
    import app.execution.mt5 as mt5x
    monkeypatch.setattr(mt5x, "bridge_get",
                        lambda path, timeout=8: {"ok": True, "bars": _bars(120, 2.0)})
    from app.config import settings
    monkeypatch.setattr(settings, "bridge_url", "http://fake-bridge:8700", raising=False)
    df = prov.get_candles("XAUUSD", "15M", limit=100)
    assert df is not None and len(df) == 100
    assert float(df["close"].iloc[-1]) == pytest.approx(2.05)
