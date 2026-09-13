"""Market data provider abstraction (SPEC §2, §59).

The strategy engine, signal engine, agent, journal and learning system consume
candles ONLY through this interface, so a real feed (broker API, Twelve Data,
Polygon, OANDA...) can be plugged in later without touching any strategy,
signal, AI-agent, journal or learning code.

`is_demo` must be respected by the UI: demo/historical data is never presented
as live data (SPEC §37, §59).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

import pandas as pd

from ..core.indicators import TF_RULES


class MarketDataProvider(ABC):
    name: str = "base"
    is_demo: bool = False

    @abstractmethod
    def get_candles(self, market: str, timeframe: str, limit: int = 600) -> Optional[pd.DataFrame]:
        """Returns a DataFrame indexed by UTC timestamp with columns
        open, high, low, close, volume up to the latest CLOSED candle."""

    @abstractmethod
    def latest_price(self, market: str) -> Optional[float]:
        """Last closed-candle close price."""

    def higher_frames(self, market: str, base_tf: str,
                      frames=("5M", "15M", "1H", "4H", "1D")) -> Dict[str, pd.DataFrame]:
        out = {}
        for tf in frames:
            df = self.get_candles(market, tf, limit=400)
            if df is not None:
                out[tf] = df
        return out

    def data_health(self, market: str) -> dict:
        """SPEC §37: never pretend delayed/unavailable data is live."""
        df = self.get_candles(market, "5M", limit=3)
        if df is None or len(df) == 0:
            return {"status": "unavailable", "message": "Market data unavailable. Signal generation paused."}
        return {"status": "ok", "demo": self.is_demo}
