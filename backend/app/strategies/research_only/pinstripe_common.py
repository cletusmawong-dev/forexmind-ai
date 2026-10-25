"""PINSTRIPE V6 - common CRT detection (owner spec, locked rules 2025).

Project: ForexMind Institutional System. This module holds ONLY the shared
CRT-candle definition used by both PINSTRIPE strategies (the spec defines
it identically in Strategy 1.A and Strategy 2.A). The strategies themselves
are separate logic blocks (hybrid_crt_tbs.py / fade_crt_extension.py) and
are never merged.

Locked values (do not modify): 0.65, 0.85, 0.75, 0.58, 0.55, 0.15.
Risk per trade is NOT hardcoded - ForexMind's risk-% engine owns sizing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

COMMON: Dict[str, Any] = {
    "name": "PINSTRIPE V6",
    "htf": "4H",
    "ltf": "1H",
    "entry_tf": "1H",
    "partial_fraction": 0.5,          # exactly 50% off at TP1
    "breakeven_after_partial": True,  # SL := entry after the partial
    # CRT candle definition (identical in both strategies)
    "crt_body_min": 0.65,
    "crt_range_mult": 0.85,
    "crt_avg_len": 20,
    # Strategy 1 validity filter
    "strong_body": 0.75,
    "validity_lookback": 5,
    # Strategy 1 entry
    "m1_body_min": 0.58,
    # Strategy 2 confirmation
    "conf_body_min": 0.55,
    # Strategy 2 extension
    "extension_frac": 0.15,
    # swing pivot half-width for the 1H TBS sweep (minimal definition)
    "swing_k": 2,
}

SIGNAL = "SIGNAL"
NO_SIGNAL = "NO_SIGNAL"
INSUFFICIENT = "INSUFFICIENT_DATA"


def _frame(candles, name: str) -> pd.DataFrame:
    if candles is None:
        raise ValueError(f"{name} candle data is missing (None)")
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(list(candles))
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{name} frame missing column '{col}'")
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index
    elif "ts" in df.columns:
        idx = pd.to_datetime(df["ts"], unit="s", utc=True)
    elif "time" in df.columns:
        idx = pd.to_datetime(df["time"], utc=True)
    else:
        raise ValueError(f"{name} frame needs a UTC datetime index or ts/time column")
    idx = pd.DatetimeIndex(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def body_ratio(open_: float, high: float, low: float, close: float) -> float:
    """abs(Close - Open) / (High - Low); a zero-range bar has no body -> 0.0."""
    rng = high - low
    if rng <= 0:
        return 0.0
    return abs(close - open_) / rng


def crt_table(h4: pd.DataFrame) -> pd.DataFrame:
    """Vectorized CRT flags over completed H4 bars (spec 1.A = 2.A):
    body ratio >= 0.65 AND (High - Low) > 0.85 * 20-period average range.
    The average is the 20-period SMA of the bar ranges INCLUDING the current
    bar (ta.sma semantics). direction: +1 bullish (close > open), -1 bearish.
    """
    rng = h4["high"] - h4["low"]
    avg = rng.rolling(COMMON["crt_avg_len"], min_periods=COMMON["crt_avg_len"]).mean()
    body = (h4["close"] - h4["open"]).abs() / rng.replace(0, pd.NA)
    is_crt = (body >= COMMON["crt_body_min"]) & (rng > COMMON["crt_range_mult"] * avg) \
        & ((h4["close"] - h4["open"]) != 0)
    direction = pd.Series(0, index=h4.index)
    direction[h4["close"] > h4["open"]] = 1
    direction[h4["close"] < h4["open"]] = -1
    return pd.DataFrame({"range": rng, "avg_range": avg, "body": body,
                         "is_crt": is_crt, "direction": direction})


def h4_completed_asof(h4: pd.DataFrame, h1_last_open: pd.Timestamp) -> pd.DataFrame:
    """H4 bars whose information is already available at the close of the
    latest completed H1 bar (an H4 bar stamped T closes at T+4h; it can
    drive 1H decisions from T+4h onward). Non-repainting as-of read."""
    horizon = h1_last_open + pd.Timedelta("1h")
    return h4.loc[h4.index + pd.Timedelta("4h") <= horizon]


def find_latest_crt(h4c: pd.DataFrame, require_validity: bool) -> Optional[Dict[str, Any]]:
    """The most recent H4 CRT candle (definition always; validity filter only
    when require_validity - that filter is Strategy 1's hybrid rule B).
    Returns {idx_pos, ts, ts_close, direction, high, low, body} or None."""
    if len(h4c) < COMMON["crt_avg_len"] + 1:
        return None
    tab = crt_table(h4c)
    lb = COMMON["validity_lookback"]
    for i in range(len(h4c) - 1, lb - 1, -1):
        if not bool(tab["is_crt"].iloc[i]):
            continue
        direction = int(tab["direction"].iloc[i])
        if require_validity:
            strong = float(tab["body"].iloc[i]) >= COMMON["strong_body"]
            if lb and i - lb >= 0:
                prior_high = float(h4c["high"].iloc[i - lb:i].max())
                prior_low = float(h4c["low"].iloc[i - lb:i].min())
            else:
                prior_high = float(h4c["high"].iloc[:i].max()) if i else -pd.inf
                prior_low = float(h4c["low"].iloc[:i].min()) if i else pd.inf
            swept = ((direction == -1 and float(h4c["high"].iloc[i]) > prior_high)
                     or (direction == 1 and float(h4c["low"].iloc[i]) < prior_low))
            if not (swept or strong):
                continue
        return {"idx_pos": i, "ts": h4c.index[i],
                "ts_close": h4c.index[i] + pd.Timedelta("4h"),
                "direction": direction,
                "high": float(h4c["high"].iloc[i]), "low": float(h4c["low"].iloc[i]),
                "body": float(tab["body"].iloc[i]),
                "range": float(tab["range"].iloc[i])}
    return None


def pivot_highs(highs: pd.Series, k: int) -> Dict[int, float]:
    """Classic fractal swing highs: strictly higher than k bars on each side.
    A pivot at p is CONFIRMED only at the close of bar p+k (returned keys are
    pivot positions; callers must respect p + k < current bar)."""
    out: Dict[int, float] = {}
    v = highs.values
    for p in range(k, len(v) - k):
        if all(v[p] > v[p - j] and v[p] > v[p + j] for j in range(1, k + 1)):
            out[p] = float(v[p])
    return out


def pivot_lows(lows: pd.Series, k: int) -> Dict[int, float]:
    v = lows.values
    out: Dict[int, float] = {}
    for p in range(k, len(v) - k):
        if all(v[p] < v[p - j] and v[p] < v[p + j] for j in range(1, k + 1)):
            out[p] = float(v[p])
    return out


def all_valid_crts(h4c: pd.DataFrame, require_validity: bool) -> list:
    """Every CRT candle in the completed H4 window, CHRONOLOGICAL. Strategy
    backtests replay each CRT's 1H sequence in order (the sequence of one
    CRT ends where the next CRT closes - a new CRT is a context switch)."""
    if len(h4c) < COMMON["crt_avg_len"] + 1:
        return []
    tab = crt_table(h4c)
    lb = COMMON["validity_lookback"]
    out = []
    for i in range(COMMON["crt_avg_len"], len(h4c)):
        if not bool(tab["is_crt"].iloc[i]):
            continue
        direction = int(tab["direction"].iloc[i])
        if require_validity:
            strong = float(tab["body"].iloc[i]) >= COMMON["strong_body"]
            prior_high = float(h4c["high"].iloc[i - lb:i].max())
            prior_low = float(h4c["low"].iloc[i - lb:i].min())
            swept = ((direction == -1 and float(h4c["high"].iloc[i]) > prior_high)
                     or (direction == 1 and float(h4c["low"].iloc[i]) < prior_low))
            if not (swept or strong):
                continue
        out.append({"idx_pos": i, "ts": h4c.index[i],
                    "ts_close": h4c.index[i] + pd.Timedelta("4h"),
                    "direction": direction,
                    "high": float(h4c["high"].iloc[i]),
                    "low": float(h4c["low"].iloc[i]),
                    "body": float(tab["body"].iloc[i]),
                    "range": float(tab["range"].iloc[i])})
    return out


def manage_trade(df: pd.DataFrame, t: int, entry: float, sl: float,
                 tp1: float, tp2: float):
    """PINSTRIPE common management, replayed bar by bar from the bar AFTER
    the entry close: stop-first per bar (gap bars fill at the open), 50%
    partial at TP1 then SL to the EXACT entry, remainder exits at TP2.
    Returns (exit_reason, exit_px, exit_bar, r1, r2, partial_done)."""
    n = len(df)
    risk = abs(entry - sl)
    # direction from the level geometry: TP2 beyond the entry in the trade's
    # favour (a buy has TP2 above the entry, a sell below)
    sign = 1.0 if tp2 > entry else -1.0
    cur_sl, partial_done, r1 = sl, False, None
    j = t + 1
    exit_reason = exit_px = None
    while j < n:
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        stop_hit = (lo <= cur_sl) if sign == 1 else (hi >= cur_sl)
        tp1_hit = (hi >= tp1) if tp1 > entry else (lo <= tp1)
        tp2_hit = (hi >= tp2) if tp2 > entry else (lo <= tp2)
        if stop_hit:
            gap_ok = (float(df["open"].iloc[j]) > cur_sl) if sign == 1 \
                else (float(df["open"].iloc[j]) < cur_sl)
            px = cur_sl if gap_ok else float(df["open"].iloc[j])
            if partial_done:
                r2 = ((px - entry) / risk) * sign
                exit_reason = "BE_STOP_REMAINDER"
            else:
                r1, r2 = -1.0, -1.0
                exit_reason = "STOP_LOSS"
            exit_px = px
            break
        if tp1_hit and not partial_done:
            partial_done = True
            r1 = ((tp1 - entry) / risk) * sign
            cur_sl = entry                     # breakeven (exact)
        if tp2_hit:
            r2 = ((tp2 - entry) / risk) * sign
            exit_reason = "TP2"
            exit_px = tp2
            break
        j += 1
    if exit_px is None:
        last = float(df["close"].iloc[n - 1])
        r_last = ((last - entry) / risk) * sign
        if not partial_done:
            r1 = r_last
        r2 = r_last
        exit_reason, exit_px = "DATA_END", last
    return exit_reason, exit_px, min(j, n - 1), r1, r2, partial_done
