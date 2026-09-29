"""SHADOW TRADING (3.0 spec section 32).

Research strategies flagged `shadow: true` run through their REAL deterministic
signal engine over the RECORDED candle store and accumulate HYPOTHETICAL trades
(shadow_trades collection) with assumed execution at candle open + assumed
spread. Outcomes are evaluated first-touch from the same recorded candles.
Nothing is ever sent to the broker. Default: no strategy is flagged shadow, so
this is a no-op in production until an admin explicitly flags one (audited).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _spread_assumed(market: str) -> float:
    try:
        from ..config import settings
        return float(getattr(settings, "shadow_spread_assumed", 0.00020))
    except Exception:
        return 0.00020


def run_shadow_cycle(user_id: str) -> dict:
    """One bounded shadow pass: detect + evaluate. No-op when nothing is in
    shadow mode. Never raises (research-cycle safe)."""
    from ..db.store import get_store
    store = get_store()
    out = {"detected": 0, "evaluated": 0, "strategies": []}
    try:
        from ..strategies import all_strategies
        from ..market_data import candle_store

        docs = store.list("strategies", limit=10)
        shadow_ids = [d["id"] for d in docs if d.get("shadow")]
        if not shadow_ids:
            out["note"] = "no strategies in shadow mode - no-op"
            return out

        for sid in shadow_ids:
            strategy = all_strategies().get(sid)
            if strategy is None:
                continue
            hist = candle_store.history("EURUSD", limit=400)
            if len(hist) < 80:
                continue
            # build a df from the recorded candle store
            import pandas as pd
            rows = [{"open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"]}
                    for c in hist]
            idx = pd.to_datetime([c.get("t") or c.get("time") for c in hist])
            df = pd.DataFrame(rows)
            df.index = idx
            df.index.name = "datetime"

            pending = [t for t in store.list("shadow_trades", filters={"strategy_id": sid},
                                             limit=100) if not t.get("resolved")]

            # evaluate pending against newer candles (first-touch)
            for t in pending:
                res = _first_touch(hist, t)
                if res:
                    store.update("shadow_trades", t["id"], {
                        "resolved": True, "outcome": res,
                        "evaluated_at": datetime.now(timezone.utc).isoformat()})
                    out["evaluated"] += 1

            # detect new hypothetical signal on the latest full bar
            try:
                sig = strategy.detect_signal(df, "EURUSD", "15M")
            except TypeError:
                sig = strategy.detect_signal(df, "EURUSD", "15M", None)
            if sig:
                last_t = df.index[-1].isoformat()
                dup = store.list("shadow_trades",
                                 filters={"strategy_id": sid, "signal_time": last_t},
                                 limit=1)
                if not dup:
                    entry = float(sig.get("entry") or df["close"].iloc[-1])
                    store.create("shadow_trades", {
                        "userId": user_id, "strategy_id": sid,
                        "market": "EURUSD", "direction": sig.get("direction"),
                        "entry": entry,
                        "sl": sig.get("sl"), "tp1": sig.get("tp1"),
                        "tp2": sig.get("tp2"), "tp3": sig.get("tp3"),
                        "signal_time": last_t,
                        "spread_assumed": _spread_assumed("EURUSD"),
                        "execution": "HYPOTHETICAL - never sent to broker",
                        "resolved": False,
                        "createdAt": datetime.now(timezone.utc).isoformat()})
                    out["detected"] += 1
            out["strategies"].append(sid)
        return out
    except Exception as exc:
        out["error"] = type(exc).__name__
        return out


def _first_touch(hist: List[dict], t: dict) -> Optional[str]:
    """First TP/SL touch strictly AFTER the signal time. Deterministic."""
    try:
        start = str(t.get("signal_time") or "")
        sl, tp1 = t.get("sl"), t.get("tp1")
        if sl is None or tp1 is None:
            return None
        buy = (t.get("direction") or "").upper() == "BUY"
        after = False
        for c in hist:
            ts = str(c.get("t") or c.get("time") or "")
            if not after:
                if ts > start:
                    after = True
                else:
                    continue
            hi, lo = float(c["h"]), float(c["l"])
            sl_hit = lo <= float(sl) if buy else hi >= float(sl)
            tp1_hit = hi >= float(tp1) if buy else lo <= float(tp1)
            if sl_hit:
                return "SL"
            if tp1_hit:
                return "TP1+"
        return None
    except Exception:
        return None
