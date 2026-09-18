"""Evidence pack builder (SS11, SS12, SS32-SS36).

One pure function per position -> a compact, truthful context dict the AI
must reason over. Data comes ONLY from recorded/real sources (provider
candles, bridge position, calendar, daily module). Nothing is fabricated:
missing pieces are marked missing and the reason codes can say so.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..config import session_of
from ..core.indicators import atr, ema
from ..db.store import get_store


def _pack_tf(df: Optional[pd.DataFrame], spans=(20, 50)) -> Optional[dict]:
    """Structure/momentum/volatility summary for one timeframe."""
    if df is None or len(df) < max(spans) + 5:
        return None
    close = df["close"]
    a = atr(df, 14)
    e9, e21 = ema(close, 9), ema(close, 21)
    last = float(close.iloc[-1])
    return {
        "last_close": round(last, 6),
        "ema9": round(float(e9.iloc[-1]), 6),
        "ema21": round(float(e21.iloc[-1]), 6),
        "ema9_vs_21": "above" if e9.iloc[-1] > e21.iloc[-1] else "below",
        "atr14": round(float(a.iloc[-1]), 6),
        "recent_high": round(float(df["high"].tail(spans[0]).max()), 6),
        "recent_low": round(float(df["low"].tail(spans[0]).min()), 6),
        "swing_high": round(float(df["high"].tail(spans[1]).max()), 6),
        "swing_low": round(float(df["low"].tail(spans[1]).min()), 6),
        "bars": int(len(df)),
    }


def build_context(user_id: str, position: dict, signal: Optional[dict],
                  daily: Optional[dict] = None,
                  provider=None) -> Dict[str, Any]:
    """Full evidence pack for one open position (SS11)."""
    store = get_store()
    market = position.get("app_market") or position.get("symbol", "")
    entry = float(position.get("price_open") or 0.0)
    current = float(position.get("price_current") or 0.0)
    direction = str(position.get("type") or "").upper()
    sl = position.get("sl")
    tp = position.get("tp")
    sig_tps = [signal.get(f"tp{i}") if signal else None for i in (1, 2, 3)]

    # position economics (per the position itself - broker truth)
    floating = float(position.get("profit") or 0.0)
    opened = position.get("time")
    try:
        hours_open = round((pd.Timestamp.utcnow().timestamp() - float(opened)) / 3600.0, 2)
    except Exception:
        hours_open = None

    # candles: M15 detail + H1/H4 context (provider = the ONE data path)
    tf_pack: Dict[str, Any] = {}
    if provider is not None:
        for tf in ("15M", "1H", "4H"):
            try:
                tf_pack[tf] = _pack_tf(provider.get_candles(market, tf, limit=300))
            except Exception:
                tf_pack[tf] = None

    # session + news (calendar is the configured news source; never invented)
    utc_hour = pd.Timestamp.utcnow().hour
    news = []
    try:
        from ..market_data.calendar import high_impact
        for ev in high_impact(market=market, hours=24)[:4]:
            news.append({"title": ev.get("title"), "time": ev.get("time"),
                         "impact": ev.get("impact")})
    except Exception:
        news = [{"error": "calendar unavailable"}]

    # other open positions -> same-factor exposure note (SS34)
    others = []
    try:
        from ..execution.mt5 import bridge_get, MAGIC, user_mode
        if user_mode(user_id) == "vps":
            for p in (bridge_get("/positions", timeout=6) or {}).get("positions") or []:
                if int(p.get("magic") or 0) == MAGIC and \
                        p.get("ticket") != position.get("ticket"):
                    others.append({"symbol": p.get("symbol"),
                                   "type": p.get("type"),
                                   "profit": round(float(p.get("profit") or 0), 2)})
    except Exception:
        pass

    goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
    balance = float((goals[0].get("mt5_account") or {}).get("balance")
                    or goals[0].get("account_balance") or 0.0) if goals else 0.0

    risk_pct = 1.0
    rdoc = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
    if rdoc:
        risk_pct = float(rdoc[0].get("risk_per_trade_pct", 1.0) or 1.0)

    return {
        "position": {
            "ticket": position.get("ticket"),
            "symbol": market,
            "direction": direction,
            "entry": entry,
            "current": current,
            "volume": position.get("volume"),
            "sl": sl,
            "broker_tp": tp,
            "tp1": sig_tps[0], "tp2": sig_tps[1], "tp3": sig_tps[2],
            "floating_usd": round(floating, 2),
            "r_multiple_now": (round((current - entry) /
                                     max(1e-9, abs(entry - float(signal["sl"]))) , 2)
                               if signal and signal.get("sl") and direction in ("BUY", "SELL")
                               and abs(entry - float(signal["sl"])) > 0 else None),
            "hours_open": hours_open,
            "session": session_of(utc_hour),
            "signal_id": (signal or {}).get("signal_id"),
            "strategy": (signal or {}).get("strategy_name"),
        },
        "account": {
            "balance_usd": balance,
            "risk_pct_per_trade": risk_pct,
            "realized_today_usd": (daily or {}).get("realized_usd"),
            "floating_today_usd": (daily or {}).get("floating_usd"),
            "total_today_usd": (daily or {}).get("total_usd"),
            "daily_profit_target_usd": (daily or {}).get("daily_profit_target_usd"),
            "daily_loss_limit_usd": (daily or {}).get("daily_loss_limit_usd"),
            "remaining_target_usd": (daily or {}).get("remaining_target_usd"),
            "remaining_loss_usd": (daily or {}).get("remaining_loss_usd"),
            "open_positions_count": 1 + len(others),
        },
        "other_positions": others,
        "market": {k: v for k, v in tf_pack.items()},
        "news_next_24h": news,
        # verbatim contract for the model (SS18/SS36/SS37)
        "instructions": (
            "Assess whether this OPEN trade still has evidence to continue toward "
            "its next target. You manage an existing position; you NEVER open, "
            "reverse or resize entries. Reply with ONLY a JSON object: "
            '{"action": "HOLD|PROTECT|PARTIAL_PROFIT|EXIT", '
            '"continuation_assessment": "STRONG|WEAKENING|REVERSING|UNCERTAIN", '
            '"next_target": "TP1|TP2|TP3|null", "confidence": 0..1, '
            '"reason_codes": ["UPPERCASE_REASON", ...], '
            '"risk_state": "CONTROLLED|ELEVATED|CRITICAL", '
            '"recommended_sl": number_or_null, '
            '"partial_fraction": 0.x_or_null, "escalation_required": bool}. '
            "confidence is an evidence score, NOT a probability of success. "
            "PROTECT may set recommended_sl (tighter only). Base every reason on "
            "the provided data; say UNCERTAIN when evidence is thin."),
    }
