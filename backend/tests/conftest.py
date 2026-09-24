import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.indicators import resample_ohlcv  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "demo")


def load_base(symbol="XAUUSD"):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{symbol}_5M.csv"), parse_dates=["timestamp"])
    return df.set_index("timestamp").sort_index()


@pytest.fixture(scope="session")
def xau_15m():
    return resample_ohlcv(load_base(), "15min").dropna()


@pytest.fixture(scope="session")
def xau_1h():
    return resample_ohlcv(load_base(), "1h").dropna()


@pytest.fixture(autouse=True)
def _test_owner(monkeypatch):
    """Execution tests default to owner user 'u1'. Production default is
    'cletusmawa' (settings.owner_user_id). Individual tests may override."""
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "u1", raising=False)


@pytest.fixture()
def fresh_store(tmp_path, monkeypatch):
    """Isolated LocalStore per test - never touches the real db.json."""
    from app.db import store as store_mod
    test_db = str(tmp_path / "test-db.json")
    s = store_mod.LocalStore(path=test_db)
    monkeypatch.setattr(store_mod, "_store", s)
    from app.state import State
    _prev = State.store
    State.store = s
    yield s
    State.store = _prev  # restore - never leak the test store into other files
