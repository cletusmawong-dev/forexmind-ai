"""Backtesting engine (SPEC §38, §39).

Runs a strategy module over ACTUAL candles from the MarketDataProvider (in
development: the labeled historical demo dataset). Every backtest records its
dataset, range, parameters, trade count and metrics. Results are never
fabricated: if there is no data, the backtest returns an explicit error state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import session_of, settings
from ..db.store import get_store
from ..learning.metrics import compute_metrics, equity_curve
from ..strategies import get_strategy


class BacktestEngine:
    def __init__(self, provider):
        self.provider = provider

    def run(self, user_id: str, strategy_id: str, market: str, timeframe: str,
            params: Optional[Dict[str, Any]] = None, max_hold_bars: int = 200,
            save: bool = True, label: str = "") -> Dict[str, Any]:
        strategy = get_strategy(strategy_id)
        df = self.provider.get_candles(market, timeframe, limit=4000)
        if df is None or len(df) < strategy.min_bars:
            return {"ok": False, "error": "Market data unavailable. Backtest paused (SPEC §37)."}

        params = strategy.get_parameters(params)
        trades = strategy.backtest(df, params, max_hold_bars=max_hold_bars)
        for t in trades:
            t["market"] = market
            t["timeframe"] = timeframe
            try:
                t["session"] = session_of(pd.Timestamp(t["entry_time"]).hour)
            except Exception:
                t["session"] = "?"
        metrics = compute_metrics(trades)
        result = {
            "ok": True,
            "strategy_id": strategy_id,
            "strategy_version": str(params.get("_version", strategy.version)),
            "params": params,
            "market": market,
            "timeframe": timeframe,
            "dataset": {
                "provider": self.provider.name,
                "demo": self.provider.is_demo,
                "start": str(df.index[0]), "end": str(df.index[-1]),
                "bars": int(len(df)),
            },
            "metrics": metrics,
            "equity_curve": equity_curve(trades),
            "trades": trades,                # full list (not persisted)
            "trades_preview": trades[-25:],
        }
        if save:
            store = get_store()
            store.create("backtests", {
                "userId": user_id,
                "strategy_id": strategy_id,
                "params": params,
                "market": market, "timeframe": timeframe,
                "dataset": result["dataset"],
                "metrics": metrics,
                "label": label,
            })
        return result
