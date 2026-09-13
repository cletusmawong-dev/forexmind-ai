"""HistoricalDemoProvider - replay of realistic stored OHLC datasets.

DEVELOPMENT MODE ONLY (SPEC §59). Candles come from generated-but-realistic
historical datasets stored under backend/data/demo/*.csv. They are replayed
as a slow-motion market so every layer (strategies, signals, agent, learning)
runs end-to-end. The provider is always marked `is_demo = True` and every API
response carries `"demo": true` so the UI can badge DEMO / HISTORICAL DATA.

A real provider implements the same MarketDataProvider interface; nothing else
in the system changes.
"""
from __future__ import annotations

import os
import threading
from typing import Dict, Optional

import pandas as pd

from ..config import DATA_DIR
from ..core.indicators import TF_RULES, resample_ohlcv
from .base import MarketDataProvider

DEMO_DIR = os.path.join(DATA_DIR, "demo")


class HistoricalDemoProvider(MarketDataProvider):
    name = "historical_demo"
    is_demo = True

    def __init__(self, replay: bool = True):
        self._lock = threading.RLock()
        self.replay = replay
        self._base: Dict[str, pd.DataFrame] = {}
        self._cursor: Dict[str, int] = {}
        self._load_all()

    # ------------------------------------------------------------------
    def _load_all(self):
        if not os.path.isdir(DEMO_DIR):
            return
        for fn in sorted(os.listdir(DEMO_DIR)):
            if not fn.endswith("_5M.csv"):
                continue
            sym = fn.replace("_5M.csv", "")
            df = pd.read_csv(os.path.join(DEMO_DIR, fn), parse_dates=["timestamp"])
            df = df.set_index("timestamp").sort_index()
            self._base[sym] = df
            # replay cursor: everything before it is "history"; start with a
            # large warmup already revealed
            self._cursor[sym] = max(0, len(df) - 4000)

    def step(self, bars: int = 1):
        """Advance the replay clock by N base (5M) bars for all markets."""
        with self._lock:
            for sym, df in self._base.items():
                self._cursor[sym] = min(len(df), self._cursor.get(sym, 0) + bars)

    def at_end(self, market: str) -> bool:
        with self._lock:
            df = self._base.get(market)
            if df is None:
                return True
            return self._cursor.get(market, 0) >= len(df)

    def markets(self):
        return list(self._base.keys())

    # ------------------------------------------------------------------
    def _visible(self, market: str) -> Optional[pd.DataFrame]:
        with self._lock:
            df = self._base.get(market)
            if df is None:
                return None
            end = self._cursor.get(market, len(df))
            return df.iloc[:end]

    def get_candles(self, market: str, timeframe: str, limit: int = 600) -> Optional[pd.DataFrame]:
        base = self._visible(market)
        if base is None or len(base) < 50:
            return None
        tf = timeframe.upper()
        if tf in ("5M", "5MIN"):
            out = base
        else:
            rule = TF_RULES.get(tf)
            if rule is None:
                return None
            out = resample_ohlcv(base, rule)
        return out.tail(limit).copy()

    def latest_price(self, market: str) -> Optional[float]:
        base = self._visible(market)
        if base is None or len(base) == 0:
            return None
        return float(base["close"].iloc[-1])

    def replay_progress(self, market: str) -> float:
        with self._lock:
            df = self._base.get(market)
            if df is None:
                return 0.0
            return round(100.0 * self._cursor.get(market, 0) / max(len(df), 1), 2)
