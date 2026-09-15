"""Signal DNA - the complete market snapshot at the EXACT signal moment.

Stored embedded on the signal doc (atomic, no duplication). The DNA carries
raw indicator values, the regime classification, multi-timeframe alignment,
news proximity and the data source - everything a later forensic analysis
needs to answer "WHY did this setup win or lose?".

Every failure degrades to "dna": None; signal creation is never blocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from ..core.indicators import atr, ema
from ..db.store import get_store


def news_proximity_min(market: str) -> Dict[str, Any]:
    """Minutes to the next high-impact event affecting this market."""
    try:
        from ..market_data.calendar import high_impact
        evs = high_impact(market=market, hours=26)
        if not evs:
            return {"minutes": None, "event": None}
        now = datetime.now(timezone.utc)
        nxt = evs[0]
        mins = int((nxt["ts"] - now).total_seconds() / 60)
        return {"minutes": mins, "event": f"{nxt['country']} {nxt['title']}"}
    except Exception:
        return {"minutes": None, "event": None}


def build(market: str, timeframe: str, df: pd.DataFrame,
          mtf: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
    """Compose the DNA from closed candles + calendar + regime. Never raises."""
    try:
        close = df["close"].astype(float)
        ema9 = float(ema(close, 9).iloc[-1])
        ema21_s = ema(close, 21)
        ema21 = float(ema21_s.iloc[-1])
        atr14 = float(atr(df, 14).iloc[-1])
        px = float(close.iloc[-1])

        from . import regime as regime_mod
        reg = regime_mod.record(market, df) or regime_mod.classify(df)

        from ..config import session_of
        session = session_of(pd.Timestamp(df.index[-1]).hour)

        news = news_proximity_min(market)

        mtf = mtf or {}
        sgn = {1: "BULLISH", -1: "BEARISH", 0: "NEUTRAL"}
        alignment = sum(1 for v in mtf.values()
                        if (v == 1 and ema9 > ema21) or (v == -1 and ema9 < ema21))

        from ..state import State
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": round(px, 6),
            "ema9": round(ema9, 6),
            "ema21": round(ema21, 6),
            "ema21_slope_pct": round((ema21 - float(ema21_s.iloc[-10])) /
                                     float(ema21_s.iloc[-10]) * 100, 3)
                               if len(ema21_s) >= 10 and float(ema21_s.iloc[-10]) else None,
            "atr14": round(atr14, 6),
            "atr_pct_of_price": round(atr14 / px * 100, 3) if px else None,
            "regime": reg.get("regime"),
            "regime_confidence": reg.get("confidence"),
            "volatility": reg.get("volatility"),
            "volatility_rank": reg.get("volatility_rank"),
            "momentum": reg.get("momentum"),
            "mtf": {k: sgn.get(v, "NEUTRAL") for k, v in mtf.items()},
            "mtf_alignment": f"{alignment}/{len(mtf) if mtf else 0}",
            "session": session,
            "news_proximity_min": news["minutes"],
            "news_event": news["event"],
            "data_source": getattr(State.provider, "name", "unknown"),
            "data_demo": bool(getattr(State.provider, "is_demo", False)),
        }
    except Exception:
        return None
