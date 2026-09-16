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
from typing import Any, Dict, List, Optional

import pandas as pd

from ..core.indicators import TF_RULES


class MarketDataProvider(ABC):
    name: str = "base"
    is_demo: bool = False

    # Honest capability map - the UI/status must never claim a capability
    # the concrete provider does not have (config != connected).
    capabilities: Dict[str, bool] = {
        "candles": True,
        "ticks": False,            # real-time tick feed
        "streaming": False,        # websocket/streaming feed
        "historical_range": False, # arbitrary start/end fetch (backfill)
    }

    @abstractmethod
    def get_candles(self, market: str, timeframe: str, limit: int = 600) -> Optional[pd.DataFrame]:
        """Returns a DataFrame indexed by UTC timestamp with columns
        open, high, low, close, volume up to the latest CLOSED candle."""

    @abstractmethod
    def latest_price(self, market: str) -> Optional[float]:
        """Last closed-candle close price."""

    # ---- VPS-readiness interface (default implementations are honest
    # no-ops; concrete providers override what they truly support) ----

    def get_latest_tick(self, market: str) -> Optional[Dict[str, Any]]:
        """Most recent real-time tick, or None when the provider has no
        tick feed. NEVER fabricate a tick from candle data."""
        return None

    def get_ticks(self, market: str, start: Any, end: Any) -> Optional[List[Dict[str, Any]]]:
        """Historical ticks in [start, end], or None when unsupported."""
        return None

    def stream_ticks(self, market: str):
        """Iterator over a live tick stream. Default: unsupported - the
        method exists so a future streaming provider can plug in without
        touching consumers. Yields nothing by default (no fake stream)."""
        return iter(())

    def fetch_historical_candles(self, market: str, timeframe: str,
                                 start: Any, end: Any) -> Optional[pd.DataFrame]:
        """Closed candles for an arbitrary UTC range (backfill), or None
        when the provider cannot serve ranges."""
        return None

    def health(self) -> Dict[str, Any]:
        """Connection-level health. 'configured' and 'connected' are
        reported separately - configured is not connected."""
        return {"provider": self.name, "demo": self.is_demo,
                "capabilities": dict(self.capabilities),
                "configured": True, "connected": True}

    def reconnect(self) -> bool:
        """Attempt to re-establish the feed; returns success honestly."""
        return False

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
