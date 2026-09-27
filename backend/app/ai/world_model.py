"""World model builder (Stage 2) - the brain's ONE evidence source.

A structured, freshness-audited snapshot of everything relevant to managing
one open trade (or watching one market):

    market      M15/H4/D1 structure (EMA alignment, swings, sweeps, BOS),
                ATR, spread (when the bridge reports one), news next 24h
    trade       broker position + signal TPs, r_multiple, MFE/MAE (in R,
                computed from M15 candles since entry), TPs reached
    account     balance/equity (broker truth), daily walls, open exposure
    historical  strategy track record for this market (sample size, win
                rate, avg R) + recent AI-management outcomes
    freshness   per-component data ages -> STALE verdict the brain must honor

NOTHING is fabricated: any component that cannot be read is None/missing
with an honest note. This module never talks to the AI and never trades.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import session_of, settings
from ..core.indicators import atr, ema
from ..db.store import get_store

TF_MINUTES = {"15M": 15, "4H": 240, "1D": 1440}


# ---------------------------------------------------------------------------
# timeframe pack: structure + momentum + volatility + freshness
# ---------------------------------------------------------------------------
def _pack_tf(df: Optional[pd.DataFrame], tf: str, now_ts: float) -> Optional[dict]:
    if df is None or len(df) < 30:
        return None
    close, high, low = df["close"], df["high"], df["low"]
    a = atr(df, 14)
    e9, e21 = ema(close, 9), ema(close, 21)
    last = float(close.iloc[-1])
    swing_hi = float(high.tail(10).max())
    swing_lo = float(low.tail(10).min())

    # sweep: wick beyond the prior 10-bar extreme, close back inside (liquidity
    # grab). BOS: close BEYOND the prior 10-bar extreme (structure break).
    sweep, bos = None, None
    for i in range(len(df) - 1, max(len(df) - 21, 0), -1):
        if i < 11:
            break
        h, l, c = float(high.iloc[i]), float(low.iloc[i]), float(close.iloc[i])
        ph = float(high.iloc[i - 10:i].max())
        pl = float(low.iloc[i - 10:i].min())
        if sweep is None:
            if h > ph and c < ph:
                sweep = "bearish_liquidity_sweep"
            elif l < pl and c > pl:
                sweep = "bullish_liquidity_sweep"
        if bos is None:
            if c > ph:
                bos = "bullish_bos"
            elif c < pl:
                bos = "bearish_bos"
        if sweep and bos:
            break

    last_dt = df.index[-1]
    try:
        last_ts = float(pd.Timestamp(last_dt).timestamp())
    except Exception:
        last_ts = None
    age_min = round((now_ts - last_ts) / 60.0, 1) if last_ts else None
    stale = bool(age_min is not None and
                 age_min > settings.brain_stale_tf_multiplier * TF_MINUTES[tf])

    return {
        "last_close": round(last, 6),
        "ema9": round(float(e9.iloc[-1]), 6),
        "ema21": round(float(e21.iloc[-1]), 6),
        "ema9_vs_21": "above" if e9.iloc[-1] > e21.iloc[-1] else "below",
        "atr14": round(float(a.iloc[-1]), 6),
        "swing_high_20": round(float(high.tail(20).max()), 6),
        "swing_low_20": round(float(low.tail(20).min()), 6),
        "liquidity_sweep": sweep,
        "bos": bos,
        "freshness": {"last_candle_age_minutes": age_min, "stale": stale,
                      "bars": int(len(df))},
    }


def _spread(market: str, user_id: str) -> Optional[dict]:
    """Spread from the bridge when reachable - never invented."""
    try:
        from ..execution.mt5 import bridge_get, user_mode
        if user_mode(user_id) != "vps":
            return None
        data = bridge_get("/rates", timeout=5) or {}
        row = (data.get("rates") or data.get("bars") or {}).get(market) or \
              (data.get(market) if isinstance(data.get(market), dict) else None)
        if row and row.get("spread") is not None:
            return {"spread": float(row["spread"])}
    except Exception:
        pass
    return None


def _news(market: str) -> List[dict]:
    try:
        from ..market_data.calendar import high_impact
        return [{"title": e.get("title"), "time": e.get("time"),
                 "impact": e.get("impact")}
                for e in high_impact(market=market, hours=24)[:4]]
    except Exception:
        return [{"error": "calendar unavailable"}]


# ---------------------------------------------------------------------------
# trade section: MFE/MAE in R from M15 candles since entry
# ---------------------------------------------------------------------------
def _mfe_mae(position: dict, signal: Optional[dict], provider, now_ts: float):
    """Best/worst excursion since entry, in R (risk = |entry - SL|).
    Returns (mfe_r, mae_r, tps_reached) with None when data is missing."""
    if provider is None or not signal or not signal.get("sl"):
        return None, None, None
    entry = float(position.get("price_open") or 0)
    sl = float(signal["sl"])
    risk = abs(entry - sl)
    if entry <= 0 or risk <= 0:
        return None, None, None
    opened = position.get("time")
    try:
        since = float(opened)
    except Exception:
        return None, None, None
    try:
        df = provider.get_candles(position.get("app_market") or
                                  position.get("symbol", ""), "15M", limit=400)
    except Exception:
        df = None
    if df is None or len(df) < 2:
        return None, None, None
    try:
        idx = [pd.Timestamp(t).timestamp() for t in df.index]
    except Exception:
        return None, None, None
    mask = [t >= since - 60 for t in idx]      # 1 min slack on entry time
    if not any(mask):
        mask = [True] * len(df)                 # old position: use whole window
    sub = df[mask]
    if len(sub) == 0:
        return None, None, None
    is_buy = str(position.get("type", "")).upper() == "BUY"
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    cur = float(position.get("price_current") or entry)
    best = max(hi, cur) if is_buy else min(lo, cur)
    worst = min(lo, cur) if is_buy else max(hi, cur)
    mfe_r = round((best - entry) / risk, 2) if is_buy else round((entry - best) / risk, 2)
    mae_r = round(abs(worst - entry) / risk, 2)   # positive magnitude of adverse move
    tps = 0
    for i in (1, 2, 3):
        tp = signal.get(f"tp{i}")
        if tp and ((best >= float(tp)) if is_buy else (best <= float(tp))):
            tps = i
    return mfe_r, mae_r, tps


# Firestore-read guard: history sections are cached 10 min per store/user/
# strategy. Reviews fire on triggers, but a burst (multi-position, restart)
# must not multiply quota reads. Tests call clear_history_cache() per case.
_CACHE: Dict[tuple, tuple] = {}
_CACHE_TTL_S = 600


def clear_history_cache() -> None:
    _CACHE.clear()


def _cached(store, key_suffix: tuple, fn):
    try:
        key = (id(store),) + key_suffix
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL_S:
            return hit[1]
        val = fn()
        _CACHE[key] = (time.time(), val)
        return val
    except Exception:
        return fn()


def _strategy_stats(user_id: str, signal: Optional[dict]) -> Optional[dict]:
    if not signal or not signal.get("strategy_id"):
        return None
    try:
        store = get_store()

        def _compute():
            sigs = store.list("signals", filters={"userId": user_id,
                                                  "strategy_id": signal["strategy_id"],
                                                  "market": signal.get("market")},
                              limit=500)
            closed = [x for x in sigs if x.get("status") in ("WIN", "LOSS", "CLOSED")
                      and x.get("mt5_pl") is not None]
            if not closed:
                return {"sample_size": 0}
            pls = [float(x["mt5_pl"]) for x in closed]
            wins = [p for p in pls if p > 0]
            return {"sample_size": len(closed),
                    "win_rate": round(len(wins) / len(closed), 2),
                    "avg_pl_usd": round(sum(pls) / len(closed), 2)}

        return _cached(store, ("stats", user_id, signal["strategy_id"],
                               signal.get("market")), _compute)
    except Exception:
        return None


def _recent_management(user_id: str, market: str, k: int = 5) -> List[dict]:
    try:
        store = get_store()

        def _compute():
            docs = store.list("ai_decisions", filters={"userId": user_id},
                              order_by="createdAt", desc=True, limit=30)
            return [{"trigger": d.get("trigger"),
                     "action": (d.get("decision") or {}).get("action"),
                     "gate": d.get("gate_verdict"),
                     "createdAt": d.get("createdAt")}
                    for d in docs if d.get("symbol") == market][:k]

        return _cached(store, ("mgmt", user_id, market), _compute)
    except Exception:
        return []


# ---------------------------------------------------------------------------
def build_world_model(user_id: str, market: str, position: Optional[dict] = None,
                      signal: Optional[dict] = None, daily: Optional[dict] = None,
                      provider=None, now: Optional[float] = None) -> Dict[str, Any]:
    now_ts = float(now or time.time())
    tf_pack: Dict[str, Any] = {}
    if provider is not None:
        for tf in ("15M", "4H", "1D"):
            try:
                tf_pack[tf] = _pack_tf(provider.get_candles(market, tf, limit=300),
                                       tf, now_ts)
            except Exception:
                tf_pack[tf] = None

    trade: Dict[str, Any] = None
    if position:
        mfe, mae, tps = _mfe_mae(position, signal, provider, now_ts)
        entry = float(position.get("price_open") or 0)
        cur = float(position.get("price_current") or 0)
        risk = abs(entry - float(signal["sl"])) if (signal and signal.get("sl")) else None
        trade = {
            "ticket": position.get("ticket"),
            "symbol": market,
            "direction": str(position.get("type", "")).upper(),
            "entry": entry, "current": cur,
            "volume": position.get("volume"),
            "sl": position.get("sl"), "broker_tp": position.get("tp"),
            "tp1": signal.get("tp1") if signal else None,
            "tp2": signal.get("tp2") if signal else None,
            "tp3": signal.get("tp3") if signal else None,
            "r_multiple_now": (round((cur - entry) / risk, 2)
                               if (risk and trade_dir_buy(position))
                               else round((entry - cur) / risk, 2)
                               if (risk and risk > 0) else None),
            "mfe_r": mfe, "mae_r": mae, "tps_reached": tps,
            "floating_usd": round(float(position.get("profit") or 0), 2),
            "hours_open": (round((now_ts - float(position.get("time"))) / 3600.0, 2)
                           if position.get("time") else None),
            "session": session_of(pd.Timestamp.utcnow().hour),
            "signal_id": (signal or {}).get("signal_id"),
            "strategy": (signal or {}).get("strategy_name"),
            "strategy_id": (signal or {}).get("strategy_id"),
        }

    account: Dict[str, Any] = {"balance_usd": None, "equity_usd": None}
    try:
        from ..execution.mt5 import bridge_get, user_mode
        if user_mode(user_id) == "vps":
            acc = bridge_get("/account", timeout=6) or {}
            account["balance_usd"] = acc.get("balance")
            account["equity_usd"] = acc.get("equity")
            pos_list = (bridge_get("/positions", timeout=6) or {}).get("positions") or []
            mine = [p for p in pos_list if p.get("ticket") != (position or {}).get("ticket")]
            account["open_exposure"] = {
                "other_positions": len(mine),
                "symbols": sorted({p.get("symbol") for p in mine}),
                "floating_usd": round(sum(float(p.get("profit") or 0) for p in mine), 2),
            }
    except Exception:
        account["note"] = "broker account unavailable"
    if daily:
        for k in ("realized_usd", "floating_usd", "total_usd",
                  "daily_profit_target_usd", "daily_loss_limit_usd",
                  "remaining_target_usd", "remaining_loss_usd",
                  "hit_target", "hit_loss"):
            if k in daily:
                account[k] = daily[k]

    fresh = [p["freshness"] for p in tf_pack.values()
             if p and p.get("freshness")]
    fresh_scores = [0.0 if f["stale"] else 1.0 for f in fresh]
    freshness = {
        "components": {tf: (tf_pack[tf] or {}).get("freshness") for tf in tf_pack},
        "score": round(sum(fresh_scores) / len(fresh_scores), 2) if fresh_scores else 0.0,
        "verdict": ("STALE" if fresh_scores and min(fresh_scores) == 0.0
                    else "OK" if fresh_scores else "NO_DATA"),
    }

    return {
        "version": 2,
        "market": market,
        "timeframes": tf_pack,
        "spread": _spread(market, user_id),
        "news_next_24h": _news(market),
        "trade": trade,
        "account": account,
        "historical": {
            "strategy_stats": _strategy_stats(user_id, signal),
            "recent_management": _recent_management(user_id, market),
        },
        "freshness": freshness,
        "built_at": now_ts,
    }


def trade_dir_buy(position: dict) -> bool:
    return str(position.get("type", "")).upper() == "BUY"
