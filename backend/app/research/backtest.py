"""DETERMINISTIC RULE BACKTEST (owner brief 2026-10-08, sections 5-8).

Replays an IMPLEMENTED RULE program (reconstruct.py) over real candles:

  - closed bars only, entry on the NEXT bar's open (no lookahead)
  - SL/TP first-touch intrabar with SL priority (conservative)
  - explicit costs: spread + commission + slippage, expressed in R
  - every trade records MFE/MAE in R (research metrics, never decisions)

Output metrics: trade count, win rate, avg R, expectancy, profit factor,
net R, max drawdown (R), longest losing/winning streaks. The source's
claimed performance is carried alongside as SOURCE CLAIM - never merged in.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..core.indicators import atr, ema, highest


def lowest(series, length: int):
    """Rolling min (companion of core.indicators.highest)."""
    return series.rolling(int(length), min_periods=int(length)).min()


def sma(series, length: int):
    """Simple MA (not in core.indicators - defined here for the interpreter)."""
    return series.rolling(int(length), min_periods=int(length)).mean()


def rsi(series, length: int = 14):
    """Wilder RSI via RMA (not in core.indicators - defined here)."""
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    rs = up.ewm(alpha=1.0 / int(length), adjust=False).mean() / \
        down.ewm(alpha=1.0 / int(length), adjust=False).mean()
    out = 100 - 100 / (1 + rs)
    out[up.ewm(alpha=1.0 / int(length), adjust=False).mean() == 0] = 100.0
    return out


# ---------------------------------------------------------------------------
# indicator resolution (deterministic, causal)
# ---------------------------------------------------------------------------
def _series(df: pd.DataFrame, spec: Dict[str, Any]) -> pd.Series:
    name = spec["indicator"]
    p = spec.get("params") or {}
    if name == "close":
        return df["close"]
    if name == "open":
        return df["open"]
    if name == "high":
        return df["high"]
    if name == "low":
        return df["low"]
    if name == "body":
        return (df["close"] - df["open"]).abs()
    if name == "range":
        return df["high"] - df["low"]
    if name == "ema":
        return ema(df["close"], int(p.get("period", 20)))
    if name == "sma":
        return sma(df["close"], int(p.get("period", 20)))
    if name == "rsi":
        return rsi(df["close"], int(p.get("period", 14)))
    if name == "atr":
        return atr(df, int(p.get("period", 14)))
    if name == "highest":
        return highest(df["high"], int(p.get("period", 20)))
    if name == "lowest":
        return lowest(df["low"], int(p.get("period", 20)))
    if name == "macd":
        # Appel's classic 12/26 MACD line (documented defaults)
        fast = ema(df["close"], int(p.get("fast", 12)))
        slow = ema(df["close"], int(p.get("slow", 26)))
        return fast - slow
    if name == "macd_signal":
        macd_line = ema(df["close"], int(p.get("fast", 12))) \
            - ema(df["close"], int(p.get("slow", 26)))
        return ema(macd_line, int(p.get("period", 9)))
    if name == "highest_prev":
        # channel EXCLUDING the current bar - a breakout means trading
        # above the PRIOR N-bar channel (Donchian semantics). Without the
        # shift, close > highest(..., N) is unsatisfiable whenever the
        # current bar sets the channel high, so the rule never fires.
        return highest(df["high"], int(p.get("period", 20))).shift(1)
    if name == "lowest_prev":
        return lowest(df["low"], int(p.get("period", 20))).shift(1)
    raise KeyError(f"unsupported indicator {name!r}")


def _cond_value(df: pd.DataFrame, spec: Dict[str, Any], i: int) -> Optional[float]:
    try:
        if "value" in spec:
            return float(spec["value"])
        s = _series(df, spec)
        v = s.iloc[i]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


def _cross_side(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Channel indicators (highest/lowest) inside a CROSS compare against
    the PRIOR window - documented interpreter semantics for breakouts."""
    if (spec or {}).get("indicator") in ("highest", "lowest"):
        out = dict(spec)
        out["indicator"] = out["indicator"] + "_prev"
        return out
    return spec


def _op_result(df: pd.DataFrame, c: Dict[str, Any], i: int, prev_i: int) -> Optional[bool]:
    left = c["left"]
    op = c["op"]
    if op in ("crosses_above", "crosses_below"):
        c = {**c, "left": _cross_side(left),
             "right": _cross_side(c.get("right") or {})}
    try:
        ls = _series(df, c["left"])
        l_now = float(ls.iloc[i]) if pd.notna(ls.iloc[i]) else None
    except Exception:
        l_now = None
    if l_now is None:
        return None
    if op == "rising" or op == "falling":
        lb = int(c.get("lookback", 1))
        j = i - max(1, lb)
        if j < 0:
            return None
        l_prev = float(ls.iloc[j]) if pd.notna(ls.iloc[j]) else None
        if l_prev is None:
            return None
        return l_now > l_prev if op == "rising" else l_now < l_prev
    r_now = _cond_value(df, c.get("right") or {}, i)
    if r_now is None:
        return None
    if op == "greater":
        return l_now > r_now
    if op == "less":
        return l_now < r_now
    # crosses need the previous bar's relation
    if prev_i < 0:
        return None
    try:
        l_prev = float(ls.iloc[prev_i]) if pd.notna(ls.iloc[prev_i]) else None
    except Exception:
        l_prev = None
    if l_prev is None:
        return None
    r_prev = _cond_value(df, c.get("right") or {}, prev_i)
    if r_prev is None:
        return None
    if op == "crosses_above":
        return l_prev <= r_prev and l_now > r_now
    if op == "crosses_below":
        return l_prev >= r_prev and l_now < r_now
    return None


def _block_true(df: pd.DataFrame, conds: List[Dict[str, Any]], i: int,
                all_mode: bool = True) -> bool:
    results = []
    for c in conds:
        r = _op_result(df, c, i, i - 1)
        results.append(r)
    if all_mode:
        return all(r is True for r in results)
    return any(r is True for r in results)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
PIP = {"XAUUSD": 0.1, "XAGUSD": 0.01, "US30": 1.0, "NAS100": 1.0, "GER40": 1.0}
DEFAULT_PIP = 0.0001            # FX majors/crosses


def pip_size(market: str) -> float:
    return PIP.get(str(market).upper(), DEFAULT_PIP)


def _costs_r(market: str, sl_distance: float, costs: Dict[str, Any]) -> float:
    """Spread+slippage (round trip) + commission expressed in R."""
    ps = pip_size(market)
    spread = float(costs.get("spread_pips") or 1.0) * ps
    slip = float(costs.get("slippage_pips") or 0.3) * ps
    comm = float(costs.get("commission_per_lot") or 7.0)
    # commission approximated in price terms: $7/lot round-turn on ~100k notional
    comm_price = comm / 100000.0
    total = 2 * (spread + slip) + comm_price      # pay spread+slip in and out
    return total / sl_distance if sl_distance > 0 else 0.0


def run(df: pd.DataFrame, market: str, implemented: Dict[str, Any],
        costs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Replay the program. Returns {status, metrics, trades, source_claim_note}."""
    costs = {**(implemented.get("costs") or {}), **(costs or {})}
    n = len(df)
    if n < 60:
        return {"status": "INSUFFICIENT_DATA", "note": "need >= 60 candles",
                "metrics": {}, "trades": []}
    entry = implemented["entry"]
    sl_rule = implemented["sl"]
    tp_rule = implemented.get("tp")
    exit_when = implemented.get("exit_when") or []
    all_mode = entry.get("all", True)
    side0 = entry.get("side", "BUY")
    mirror = "mirror_side" in entry and entry.get("mirror_side") == "SELL"

    atr_s = atr(df, 14)
    trades: List[Dict[str, Any]] = []
    pos = None                                   # open position state

    for i in range(2, n):
        if pos is not None:
            hi = float(df["high"].iloc[i]); lo = float(df["low"].iloc[i])
            sl_hit = lo <= pos["sl"] if pos["dir"] == 1 else hi >= pos["sl"]
            tp_hit = (tp_rule is not None and
                      (hi >= pos["tp"] if pos["dir"] == 1 else lo <= pos["tp"]))
            exit_rule = (_block_true(df, exit_when, i, True) if exit_when else False)
            if sl_hit:                            # SL priority (conservative)
                _close(pos, i, pos["sl"], "SL", trades, df)
                pos = None
            elif tp_hit and tp_rule is not None:
                _close(pos, i, pos["tp"], "TP", trades, df)
                pos = None
            elif exit_rule:
                _close(pos, i, float(df["close"].iloc[i]), "RULE", trades, df)
                pos = None
            else:
                fav = (hi - pos["entry"]) if pos["dir"] == 1 else (pos["entry"] - lo)
                adv = (pos["entry"] - lo) if pos["dir"] == 1 else (hi - pos["entry"])
                pos["mfe"] = max(pos["mfe"], fav / pos["risk"])
                pos["mae"] = min(pos["mae"], -adv / pos["risk"])
            continue

        side = side0
        if mirror:
            bull = _block_true(df, entry["when"], i, all_mode)
            if not bull:
                continue
            side = "BUY"                          # mirrored entry: bull leg first
        else:
            if side0 == "BUY":
                if not _block_true(df, entry["when"], i, all_mode):
                    continue
            else:
                inv = [{"op": {"greater": "less", "less": "greater",
                               "crosses_above": "crosses_below",
                               "crosses_below": "crosses_above"}.get(c["op"], c["op"]),
                        "left": c["left"],
                        **({"right": c["right"]} if "right" in c else {})}
                       for c in entry["when"]]
                if not _block_true(df, inv, i, all_mode):
                    continue

        # ---- arm at NEXT bar open (no lookahead) --------------------------
        j = i + 1
        if j >= n:
            break
        px = float(df["open"].iloc[j])
        if not px or px <= 0:
            continue
        try:
            a_now = float(atr_s.iloc[i])
        except Exception:
            a_now = 0.0
        if sl_rule["type"] == "fixed_pips":
            dist = sl_rule["pips"] * pip_size(market)
        elif sl_rule["type"] == "atr_mult":
            dist = sl_rule["mult"] * (a_now if a_now > 0 else 0)
            if dist <= 0:
                continue
        else:                                     # structure_level
            lb = int(sl_rule.get("lookback", 10))
            if side == "BUY":
                dist = px - float(lowest(df["low"], lb).iloc[i])
            else:
                dist = float(highest(df["high"], lb).iloc[i]) - px
            if dist <= 0:
                continue
        risk = dist
        if risk <= 0:
            continue
        sl = px - dist if side == "BUY" else px + dist
        if tp_rule is None:
            tp = None
        elif tp_rule["type"] == "rr_multiple":
            tp = px + tp_rule["value"] * dist if side == "BUY" else px - tp_rule["value"] * dist
        elif tp_rule["type"] == "fixed_pips":
            tp = px + tp_rule["pips"] * pip_size(market) if side == "BUY" else px - tp_rule["pips"] * pip_size(market)
        else:                                     # atr_mult target
            tp = px + tp_rule["mult"] * a_now if side == "BUY" else px - tp_rule["mult"] * a_now
        cost_r = _costs_r(market, risk, costs)
        pos = {"dir": 1 if side == "BUY" else -1, "entry": px, "sl": sl, "tp": tp,
               "risk": risk, "entry_i": j, "signal_i": i, "mfe": 0.0, "mae": 0.0,
               "cost_r": cost_r}

    if pos is not None:
        _close(pos, n - 1, float(df["close"].iloc[n - 1]), "END", trades, df)

    metrics = _metrics(trades)
    return {"status": "OK", "metrics": metrics, "trades": trades,
            "note": "FOREXMIND VERIFIED RESULT - independent reconstruction; "
                    "source performance was NOT used"}


def _close(pos: dict, i: int, px: float, why: str, trades: list, df) -> None:
    move = (px - pos["entry"]) * pos["dir"]
    r = move / pos["risk"] - pos["cost_r"]
    trades.append({
        "signal_i": pos["signal_i"], "entry_i": pos["entry_i"], "exit_i": i,
        "entry_time": str(df.index[pos["entry_i"]]), "exit_time": str(df.index[i]),
        "entry": round(pos["entry"], 7), "exit": round(px, 7),
        "side": "BUY" if pos["dir"] == 1 else "SELL",
        "exit_reason": why, "r": round(r, 4),
        "mfe_r": round(pos["mfe"], 3), "mae_r": round(pos["mae"], 3),
    })


def _metrics(trades: List[dict]) -> Dict[str, Any]:
    if not trades:
        return {"trade_count": 0, "note": "no trades - rules never fired"}
    rs = [t["r"] for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_w = sum(wins); gross_l = abs(sum(losses))
    cum = peak = 0.0; max_dd = 0.0; streak = 0; worst_loss_streak = 0
    best_win_streak = cur_win = 0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
        if r <= 0:
            streak += 1; cur_win = 0
            worst_loss_streak = max(worst_loss_streak, streak)
        else:
            streak = 0; cur_win += 1
            best_win_streak = max(best_win_streak, cur_win)
    mfe = [t["mfe_r"] for t in trades]; mae = [t["mae_r"] for t in trades]
    return {
        "trade_count": n,
        "win_rate": round(100 * len(wins) / n, 2),
        "avg_r": round(sum(rs) / n, 4),
        "expectancy_r": round(sum(rs) / n, 4),
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else None,
        "net_r": round(sum(rs), 3),
        "max_drawdown_r": round(max_dd, 3),
        "max_losing_streak": worst_loss_streak,
        "max_winning_streak": best_win_streak,
        "avg_mfe_r": round(sum(mfe) / n, 3),
        "avg_mae_r": round(sum(mae) / n, 3),
        "first_trade": trades[0]["entry_time"],
        "last_trade": trades[-1]["exit_time"],
    }
