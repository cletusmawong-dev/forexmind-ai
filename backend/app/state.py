"""Process-wide singletons wired once at startup."""
from __future__ import annotations

from .db.store import get_store
from .config import settings
from .market_data.demo_provider import HistoricalDemoProvider
from .market_data.live_provider import LiveProvider
from .engine.signal_engine import SignalEngine
from .engine.tracker import SignalTracker
from .backtesting.engine import BacktestEngine
from .learning.experiments import ExperimentEngine


class State:
    store = None
    provider = None
    engine: SignalEngine = None
    tracker: SignalTracker = None
    backtester: BacktestEngine = None
    experiments: ExperimentEngine = None
    ready = False


def init_state() -> State:
    if State.ready:
        return State
    State.store = get_store()
    State.provider = LiveProvider() if settings.market_data_provider == "live" else HistoricalDemoProvider(replay=True)
    State.engine = SignalEngine(State.provider)
    State.tracker = SignalTracker(State.provider)
    State.backtester = BacktestEngine(State.provider)
    State.experiments = ExperimentEngine(State.provider)
    State.ready = True
    return State
