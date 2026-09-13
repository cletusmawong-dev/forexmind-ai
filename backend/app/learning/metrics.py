"""Experiment / journal metrics (SPEC §22, §32).

Computed strictly from simulated or recorded trade lists - nothing invented.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..config import session_of


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", ""))


def compute_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rs = [float(t.get("r_multiple", 0.0)) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # drawdown on cumulative R curve
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # consecutive streaks
    cw = cl = mw = ml = 0
    for r in rs:
        if r > 0:
            cw += 1; cl = 0
        elif r < 0:
            cl += 1; cw = 0
        else:
            cw = cl = 0
        mw = max(mw, cw); ml = max(ml, cl)

    durations = [int(t.get("duration_bars", 0)) for t in trades]

    def hit_rate(status_prefix: str) -> float:
        hits = sum(1 for t in trades if t.get("tp_hits", 0) >= int(status_prefix))
        return round(100.0 * hits / n, 1)

    by_market: Dict[str, dict] = {}
    by_tf: Dict[str, dict] = {}
    by_session: Dict[str, dict] = {}
    for t in trades:
        for bucket, key in ((by_market, t.get("market", "?")),
                            (by_tf, t.get("timeframe", "?")),
                            (by_session, t.get("session", "?"))):
            b = bucket.setdefault(key, {"trades": 0, "wins": 0, "sum_r": 0.0})
            b["trades"] += 1
            b["sum_r"] += float(t.get("r_multiple", 0.0))
            if float(t.get("r_multiple", 0.0)) > 0:
                b["wins"] += 1
    for d in (by_market, by_tf, by_session):
        for k, b in d.items():
            b["win_rate"] = round(100.0 * b["wins"] / b["trades"], 1) if b["trades"] else 0.0
            b["expectancy"] = round(b["sum_r"] / b["trades"], 3) if b["trades"] else 0.0
            del b["wins"]

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "avg_r": round(sum(rs) / n, 3),
        "expectancy": round(sum(rs) / n, 3),
        "total_r": round(sum(rs), 2),
        "max_drawdown_r": round(max_dd, 2),
        "max_consecutive_wins": mw,
        "max_consecutive_losses": ml,
        "tp1_hit_rate": hit_rate(1),
        "tp2_hit_rate": hit_rate(2),
        "tp3_hit_rate": hit_rate(3),
        "sl_rate": round(100.0 * sum(1 for t in trades if t.get("status") == "SL_HIT" and not t.get("tp_hits")) / n, 1),
        "avg_duration_bars": round(sum(durations) / n, 1),
        "by_market": by_market,
        "by_timeframe": by_tf,
        "by_session": by_session,
    }


def equity_curve(trades: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    """Cumulative R over trade sequence (chronological)."""
    out = []
    cum = 0.0
    for i, t in enumerate(sorted(trades, key=lambda x: x.get("entry_time", ""))):
        cum += float(t.get("r_multiple", 0.0))
        out.append({"i": i + 1, "time": t.get("entry_time", ""), "cum_r": round(cum, 3)})
    return out


def classify_experiment(orig: Dict[str, Any], exp: Dict[str, Any],
                        min_trades: int) -> Dict[str, Any]:
    """SPEC §23: IMPROVED / NO SIGNIFICANT CHANGE / WORSE / INSUFFICIENT DATA.

    The AI must not claim improvement unless evidence supports it.
    """
    n_o = orig.get("trades", 0); n_e = exp.get("trades", 0)
    if n_o < min_trades or n_e < min_trades:
        return {
            "result": "INSUFFICIENT_DATA",
            "conclusion": (f"Insufficient data: original has {n_o} trades, experimental "
                           f"{n_e}; at least {min_trades} trades per version are required "
                           "before any conclusion can be drawn."),
            "recommend_approval": False,
        }
    d_wr = round(exp["win_rate"] - orig["win_rate"], 1)
    d_ex = round(exp["expectancy"] - orig["expectancy"], 3)
    if d_ex > 0.05 and d_wr >= -3.0:
        result = "IMPROVED"
        conclusion = (f"On the same dataset the experimental version shows expectancy "
                      f"{exp['expectancy']}R vs {orig['expectancy']}R ({d_ex:+}R) and win rate "
                      f"{exp['win_rate']}% vs {orig['win_rate']}% ({d_wr:+.1f}pp). Evidence "
                      "supports a possible improvement.")
    elif d_ex < -0.05:
        result = "WORSE"
        conclusion = (f"On the same dataset the experimental version performs worse "
                      f"(expectancy {exp['expectancy']}R vs {orig['expectancy']}R, {d_ex:+}R). "
                      "The change is not recommended.")
    else:
        result = "NO_SIGNIFICANT_CHANGE"
        conclusion = (f"On the same dataset no material difference was measured "
                      f"(expectancy {orig['expectancy']}R vs {exp['expectancy']}R). "
                      "There is not enough evidence to justify the change.")
    return {
        "result": result,
        "conclusion": conclusion,
        "delta_win_rate": d_wr,
        "delta_expectancy": d_ex,
        "recommend_approval": result == "IMPROVED",
    }
