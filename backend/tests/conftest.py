import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# allow tests to import sibling test modules regardless of the pytest cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

@pytest.fixture(autouse=True)
def _isolate_regime_cache():
    """The live loop (started by every TestClient) classifies markets into the
    shared regime cache; synthetic test data can look extreme-vol. Clear it
    before every test so guards and dashboards never inherit another file's
    classifications."""
    try:
        from app.learning import regime as _regime
        _regime.clear_cache()
    except Exception:
        pass
    yield
    try:
        from app.learning import regime as _regime
        _regime.clear_cache()
    except Exception:
        pass


import pytest as _pytest


@_pytest.fixture()
def with_strategies(monkeypatch):
    """Re-register the kept-for-history strategy modules for tests that
    exercise STRATEGY behavior. Production scanning uses OWNER_STRATEGY_IDS
    only; these tests verify module logic, not the live registry. Use via
    `pytestmark = pytest.mark.usefixtures("with_strategies")`.

    Additions go through the same monkeypatch stack as the tests. A test
    that monkeypatch.delitem's one of these keys (chart overlays when S/D
    is unregistered) used to have its undo run AFTER this fixture restored
    the dict, which put strategy_2_supply_demand_fvg back for every later
    test. setitem/delitem undo in reverse call order, so the key is gone
    again when the test ends."""
    import app.strategies as reg
    from app.strategies.strategy_1_zero_lag.strategy import ZeroLagStrategy
    from app.strategies.strategy_1_vp_pivots.strategy import VPPivotsStrategy
    from app.strategies.strategy_2_ema_atr.strategy import EmaAtrStrategy
    from app.strategies.strategy_2_mtf_sweep_bos_retest.strategy import MtfSweepBosRetestStrategy
    from app.strategies.strategy_2_supply_demand_fvg.strategy import SupplyDemandFvgStrategy
    for sid, cls in (
        (ZeroLagStrategy.id, ZeroLagStrategy),
        (VPPivotsStrategy.id, VPPivotsStrategy),
        (EmaAtrStrategy.id, EmaAtrStrategy),
        (MtfSweepBosRetestStrategy.id, MtfSweepBosRetestStrategy),
        (SupplyDemandFvgStrategy.id, SupplyDemandFvgStrategy),
    ):
        monkeypatch.setitem(reg.STRATEGY_CLASSES, sid, cls)
    reg._INSTANCES.clear()
    yield reg.STRATEGY_CLASSES
    reg._INSTANCES.clear()

@pytest.fixture(autouse=True)
def _table_pip_values(monkeypatch):
    """Default test seam: pip values come from the static table so legacy
    sizing expectations stay valid without a live /symbol_info bridge.
    Tests that verify broker-truth sizing (test_54) restore the real
    bridge-backed lookup explicitly."""
    from app.execution import mt5 as _X
    monkeypatch.setattr(_X, "_pip_value_usd",
                        lambda market: _X.PIP_VALUE.get(market, 10.0))
