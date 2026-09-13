"""LiveProvider - real market data via the MarketDataProvider interface.

Data sources (SPEC §59 - pluggable, honest, no fake data):

* Twelve Data (https://twelvedata.com) - activated for FX/metals when
  TWELVEDATA_API_KEY is configured. Free tier: 8 req/min, 800/day.
* Yahoo Finance chart API - keyless fallback/fill for every market,
  including NAS100 (as the ^NDX index) which the free Twelve Data
  tier does not cover.

Only CLOSED candles are ever returned: a candle whose time bucket has not
finished is dropped, so strategies never see a forming bar. Responses are
cached per (market, timeframe) with a TTL derived from the timeframe to
stay inside free-tier rate limits. `is_demo` is always False; the UI shows
live badges based on this.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import requests

from ..core.indicators import TF_RULES, resample_ohlcv
from ..config import settings
from .base import MarketDataProvider

# app symbol -> (twelvedata symbol, yahoo symbol, yahoo fallback, oanda instrument)
SYMBOL_MAP = {
    "EURUSD": ("EUR/USD", "EURUSD=X", None, "EUR_USD"),
    "GBPUSD": ("GBP/USD", "GBPUSD=X", None, "GBP_USD"),
    "USDJPY": ("USD/JPY", "USDJPY=X", None, "USD_JPY"),
    "XAUUSD": ("XAU/USD", "XAUUSD=X", "GC=F", "XAU_USD"),
    # NAS100: free tier has no index feed; QQQ (Nasdaq-100 ETF) is the proxy.
    "NAS100": ("QQQ", "^NDX", "NQ=F", "NAS100_USD"),
}

OANDA_GRAN = {"5M": "M5", "15M": "M15", "30M": "M30", "1H": "H1", "4H": "H4", "1D": "D"}

TF_MINUTES = {"5M": 5, "15M": 15, "30M": 30, "1H": 60, "4H": 240, "1D": 1440}

# refresh cadence per timeframe (seconds) - tuned so a full 5-market scan
# cycle stays comfortably inside Twelve Data's free tier (8 req/min, 800/day)
TTL = {"5M": 120, "15M": 600, "30M": 600, "1H": 1200, "4H": 3600, "1D": 7200}

# minimum spacing between Twelve Data HTTP calls (free tier: 8/min)
TD_MIN_GAP = 8.0

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


class LiveProvider(MarketDataProvider):
    name = "live"
    is_demo = False

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Dict[Tuple[str, str], Tuple[float, Optional[pd.DataFrame]]] = {}
        self.td_key = settings.twelvedata_api_key
        self._td_last_call = 0.0
        self.oanda_token = settings.oanda_api_token
        self.oanda_base = ("https://api-fxtrade.oanda.com" if settings.oanda_env == "live"
                           else "https://api-fxpractice.oanda.com")
        self._oanda_last_call = 0.0

    def _oanda_throttle(self):
        with self._lock:
            wait = self._oanda_last_call + 1.0 - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 5))
            self._oanda_last_call = time.monotonic()

    def _td_throttle(self):
        """Space Twelve Data calls >= TD_MIN_GAP apart (free-tier friendly)."""
        with self._lock:
            wait = self._td_last_call + TD_MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 10))
            self._td_last_call = time.monotonic()

    # ------------------------------------------------------------------
    def _td_symbol(self, market: str) -> Optional[str]:
        return SYMBOL_MAP.get(market, (None, None, None, None))[0]

    # ------------------------------------------------------------------
    # OANDA v20 (practice or live) - broker-grade candles, complete-flagged
    # ------------------------------------------------------------------
    def _fetch_oanda(self, market: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
        if not self.oanda_token:
            return None
        inst = SYMBOL_MAP.get(market, (None, None, None, None))[3]
        gran = OANDA_GRAN.get(tf)
        if not inst or not gran:
            return None
        try:
            self._oanda_throttle()
            r = requests.get(
                f"{self.oanda_base}/v3/instruments/{inst}/candles",
                params={"granularity": gran, "count": min(limit, 500), "price": "M"},
                headers={"Authorization": f"Bearer {self.oanda_token}"},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            candles = [c for c in r.json().get("candles", []) if c.get("complete")]
            if not candles:
                return None
            idx = pd.to_datetime([c["time"] for c in candles]).tz_localize(None) \
                if not pd.to_datetime(candles[0]["time"]).tzinfo else \
                pd.to_datetime([c["time"] for c in candles]).tz_convert("UTC").tz_localize(None)
            df = pd.DataFrame(
                {
                    "open": [float(c["mid"]["o"]) for c in candles],
                    "high": [float(c["mid"]["h"]) for c in candles],
                    "low": [float(c["mid"]["l"]) for c in candles],
                    "close": [float(c["mid"]["c"]) for c in candles],
                    "volume": [float(c.get("volume", 0)) for c in candles],
                },
                index=idx,
            )
            return df
        except Exception:
            return None

    def _yahoo_symbol(self, market: str) -> str:
        _, y, fb, _o = SYMBOL_MAP.get(market, (None, market, None, None))
        return y or market

    # ------------------------------------------------------------------
    # Twelve Data
    # ------------------------------------------------------------------
    def _fetch_td(self, market: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
        sym = self._td_symbol(market)
        if not sym or not self.td_key:
            return None
        interval = {"5M": "5min", "15M": "15min", "30M": "30min", "1H": "1h", "4H": "4h", "1D": "1day"}.get(tf)
        if not interval:
            return None
        try:
            self._td_throttle()
            r = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": sym, "interval": interval, "outputsize": min(limit, 500), "timezone": "UTC", "apikey": self.td_key},
                timeout=15,
            )
            d = r.json()
            values = d.get("values")
            if not values:
                return None
            df = pd.DataFrame(values)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index()
            df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close"})[["open", "high", "low", "close"]]
            df = df.apply(pd.to_numeric, errors="coerce")
            df["volume"] = 0.0
            return df
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Yahoo Finance (keyless)
    # ------------------------------------------------------------------
    def _fetch_yahoo(self, market: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
        sym = self._yahoo_symbol(market)
        minutes = TF_MINUTES.get(tf)
        if not minutes:
            return None
        interval = {5: "5m", 15: "15m", 30: "30m", 60: "60m", 240: "60m", 1440: "1d"}[minutes]
        rng = "1mo" if minutes < 1440 else "6mo"
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"interval": interval, "range": rng, "includePrePost": "false"},
                headers=UA,
                timeout=15,
            )
            if r.status_code != 200:
                return None
            res = r.json().get("chart", {}).get("result")
            if not res:
                return None
            ts = res[0].get("timestamp") or []
            q = (res[0].get("indicators") or {}).get("quote", [{}])[0]
            if not ts:
                return None
            df = pd.DataFrame(
                {
                    "open": q.get("open", []),
                    "high": q.get("high", []),
                    "low": q.get("low", []),
                    "close": q.get("close", []),
                    "volume": q.get("volume", []),
                },
                index=pd.to_datetime(ts, unit="s", utc=True).tz_convert("UTC").tz_localize(None),
            )
            df = df.dropna(subset=["open", "high", "low", "close"])
            if tf == "4H":  # built from 60m
                df = resample_ohlcv(df, TF_RULES["4H"])
            return df
        except Exception:
            return None

    # ------------------------------------------------------------------
    def _aligned(self, market: str, tf: str, df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """Drop the still-forming candle so only CLOSED bars feed strategies."""
        if df is None or len(df) == 0:
            return None
        minutes = TF_MINUTES.get(tf, 5)
        try:
            last = df.index[-1].to_pydatetime()
            bucket_end = last + pd.Timedelta(minutes=minutes)
            import datetime as _dt
            if bucket_end > _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc).replace(tzinfo=None):
                df = df.iloc[:-1]
        except Exception:
            pass
        return df if len(df) else None

    def _cached(self, market: str, tf: str, limit: int = 600) -> Optional[pd.DataFrame]:
        key = (market, tf)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and (now - hit[0]) < TTL.get(tf, 60):
                df = hit[1]
                return df.tail(limit).copy() if df is not None and len(df) else None

        df = self._aligned(market, tf, self._fetch_oanda(market, tf, limit))
        if df is None and self.td_key:
            df = self._aligned(market, tf, self._fetch_td(market, tf, limit))
        if df is None:
            df = self._aligned(market, tf, self._fetch_yahoo(market, tf, limit))
        if df is None and self.td_key:  # TD symbol missing on Yahoo-free markets? try Yahoo fallback symbol
            _, _, fb, _o = SYMBOL_MAP.get(market, (None, None, None, None))
            if fb:
                sym_backup = self._yahoo_symbol(market)
                try:
                    SYMBOL_MAP[market] = (self._td_symbol(market), fb, None, _o)
                    df = self._aligned(market, tf, self._fetch_yahoo(market, tf, limit))
                finally:
                    SYMBOL_MAP[market] = (self._td_symbol(market), sym_backup, fb, _o)
        with self._lock:
            self._cache[key] = (now, df if df is not None and len(df) else None)
        return df.tail(limit).copy() if df is not None and len(df) else None

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------
    def get_candles(self, market: str, timeframe: str, limit: int = 600) -> Optional[pd.DataFrame]:
        return self._cached(market, timeframe.upper(), limit)

    def latest_price(self, market: str) -> Optional[float]:
        df = self._cached(market, "5M", limit=3)
        if df is None or len(df) == 0:
            df = self._cached(market, "15M", limit=3)
        if df is None or len(df) == 0:
            return None
        return float(df["close"].iloc[-1])
