"""Journal + analytics routes (SPEC §31, §32)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends

from ..config import session_of
from ..learning.metrics import compute_metrics, equity_curve
from ..state import State
from ..strategies import all_strategies
from .deps import get_user_id

router = APIRouter(tags=["journal"])


def _completed_signals(user_id: str):
    return State.store.list("signals", filters={"userId": user_id, "completed": True},
                            limit=2000)


@router.get("/journal/mt5-trades")
def mt5_trades(user_id: str = Depends(get_user_id)):
    """Real-money log: every MT5-executed trade with its DOLLAR result.

    Open trades show live floating P/L from the bridge (magic-filtered);
    closed trades show the broker-confirmed result (profit+commission+swap)
    - the 'TP +$100 / SL -$20' record the user expects in the journal."""
    store = State.store
    from ..config import settings as _settings
    sigs = [s for s in store.list("signals", filters={"userId": user_id}, limit=3000)
            if s.get("mt5_ticket")]
    sigs.sort(key=lambda x: str(x.get("createdAt")), reverse=True)

    floating = {}
    mode = "off"
    try:
        from ..execution.mt5 import user_mode, bridge_get, MAGIC
        mode = user_mode(user_id)
        if mode == "vps" and _settings.bridge_url:
            data = bridge_get("/positions", timeout=8) or {}
            for pos in data.get("positions") or []:
                if int(pos.get("magic") or 0) == MAGIC:
                    try:
                        floating[int(pos.get("ticket"))] = float(pos.get("profit") or 0)
                    except Exception:
                        pass
    except Exception:
        pass

    out = []
    for s in sigs[:50]:
        try:
            tk = int(s.get("mt5_ticket"))
        except Exception:
            tk = None
        live = floating.get(tk) if tk else None
        if live is not None:
            state, pl = "LIVE", round(live, 2)
        elif s.get("mt5_confirmed"):
            state, pl = "CLOSED", s.get("mt5_pl")
        else:
            state, pl = ("SUBMITTED", None) if not s.get("completed") else ("CLOSED", s.get("mt5_pl"))
        out.append({
            "id": s.get("id"), "signal_id": s.get("signal_id"),
            "market": s.get("market"), "direction": s.get("direction"),
            "timeframe": s.get("timeframe"),
            "strategy_name": s.get("strategy_name"),
            "ticket": tk, "volume": s.get("mt5_volume"),
            "open_price": s.get("mt5_open_price") or s.get("entry"),
            "close_price": s.get("mt5_close_price"),
            "sl": s.get("sl"), "tp1": s.get("tp1"), "tp2": s.get("tp2"), "tp3": s.get("tp3"),
            "state": state, "pl_usd": pl,
            "outcome": s.get("outcome"),
            "r_multiple": s.get("r_multiple"),
            "opened_at": s.get("createdAt"), "closed_at": s.get("mt5_closed_at"),
        })
    return {"trades": out, "count": len(out), "mode": mode,
            "demo": State.provider.is_demo}


@router.get("/journal/stats")
def journal_stats(user_id: str = Depends(get_user_id)):
    store = State.store
    signals = store.list("signals", filters={"userId": user_id}, limit=3000)
    completed = [s for s in signals if s.get("completed")]
    taken = [s for s in signals if s.get("user_action") == "entered"]
    skipped = [s for s in signals if s.get("user_action") == "skipped"]

    metrics = compute_metrics([
        {"r_multiple": s.get("r_multiple", 0.0), "status": s.get("status"),
         "tp_hits": s.get("tp_hits", 0), "duration_bars": s.get("duration_bars", 0),
         "entry_time": s.get("candle_time", ""), "market": s.get("market"),
         "timeframe": s.get("timeframe"), "session": (s.get("market_conditions") or {}).get("session")}
        for s in completed
    ])

    now = pd.Timestamp.utcnow()
    daily, weekly, monthly = defaultdict(float), defaultdict(float), defaultdict(float)
    for s in completed:
        # candle_time can be missing (e.g. MT5-synced closes) - fall back to any
        # real timestamp so one malformed doc can never 500 the whole journal
        ct = s.get("candle_time") or s.get("completed_at") or s.get("createdAt") or ""
        if not ct:
            continue
        day = str(ct)[:10]
        daily[day] += s.get("r_multiple", 0.0) or 0.0
        try:
            ts = pd.Timestamp(ct)
        except (ValueError, TypeError):
            continue
        iso = ts.isocalendar()
        weekly[f"{iso.year}-W{iso.week:02d}"] += s.get("r_multiple", 0.0) or 0.0
        monthly[day[:7]] += s.get("r_multiple", 0.0) or 0.0

    by_strategy = {}
    for sid, strat in all_strategies().items():
        sub = [s for s in completed if s.get("strategy_id") == sid]
        by_strategy[strat.short_name] = {
            "signals": len(sub),
            "win_rate": round(100 * sum(1 for s in sub if s.get("outcome") == "WIN")
                              / len(sub), 1) if sub else 0.0,
            "avg_r": round(sum(s.get("r_multiple", 0) for s in sub) / len(sub), 3) if sub else 0.0,
        }

    return {
        "total_signals": len(signals),
        "signals_taken": len(taken),
        "signals_skipped": len(skipped),
        "completed": len(completed),
        "metrics": metrics,
        "daily": dict(sorted(daily.items())[-21:]),
        "weekly": dict(sorted(weekly.items())[-12:]),
        "monthly": dict(sorted(monthly.items())[-6:]),
        "by_strategy": by_strategy,
        "demo": State.provider.is_demo,
    }


@router.get("/analytics/summary")
def analytics(user_id: str = Depends(get_user_id)):
    completed = _completed_signals(user_id)
    trades = [{"r_multiple": s.get("r_multiple", 0.0), "status": s.get("status"),
               "tp_hits": s.get("tp_hits", 0), "duration_bars": s.get("duration_bars", 0),
               "entry_time": s.get("candle_time", ""), "market": s.get("market"),
               "timeframe": s.get("timeframe"),
               "session": (s.get("market_conditions") or {}).get("session"),
               "strategy_id": s.get("strategy_id")}
              for s in completed]

    r_values = [t["r_multiple"] for t in trades]
    buckets = {"<=-1R": 0, "-1..0": 0, "0..1R": 0, "1..2R": 0, "2..3R": 0, ">3R": 0}
    for r in r_values:
        if r <= -1:
            buckets["<=-1R"] += 1
        elif r < 0:
            buckets["-1..0"] += 1
        elif r < 1:
            buckets["0..1R"] += 1
        elif r < 2:
            buckets["1..2R"] += 1
        elif r < 3:
            buckets["2..3R"] += 1
        else:
            buckets[">3R"] += 1

    per_strategy = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        b = per_strategy[t.get("strategy_id", "?")]
        b[0] += 1
        if t["r_multiple"] > 0:
            b[1] += 1
        b[2] += t["r_multiple"]
    per_market = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        b = per_market[t.get("market", "?")]
        b[0] += 1
        if t["r_multiple"] > 0:
            b[1] += 1
        b[2] += t["r_multiple"]

    experiments = State.store.list("experiments", filters={"userId": user_id}, limit=20)

    return {
        "equity_curve": equity_curve(trades),
        "metrics": compute_metrics(trades),
        "r_distribution": buckets,
        "strategy_comparison": {k: {"trades": v[0], "wins": v[1],
                                    "win_rate": round(100 * v[1] / v[0], 1) if v[0] else 0,
                                    "total_r": round(v[2], 2)}
                                for k, v in per_strategy.items()},
        "market_comparison": {k: {"trades": v[0], "wins": v[1],
                                  "win_rate": round(100 * v[1] / v[0], 1) if v[0] else 0,
                                  "total_r": round(v[2], 2)}
                              for k, v in per_market.items()},
        "experiments": [{"hypothesis_id": e.get("hypothesis_id"),
                         "variable": e["variable"],
                         "old": e["old_value"], "new": e["new_value"],
                         "result": e["result"],
                         "orig_wr": e["original_metrics"].get("win_rate"),
                         "exp_wr": e["experimental_metrics"].get("win_rate")}
                        for e in experiments],
        "demo": State.provider.is_demo,
    }
