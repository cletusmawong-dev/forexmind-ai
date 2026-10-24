"""Strategy (LIVE): "TrendBreak v2 (H1)" - owner Pine port (2026-10-24).

Source of truth: the owner's Pine Script v6 ("TrendBreak v2 (H1)"), ported
line-for-line. Differences vs TrendBreak v1 (all from the Pine):
  * gap filter: |H4 EMA50 - EMA200| must exceed gap_min x ATR (2.0 ATR)
  * volatility floor: ATR percentrank over volWin (480) bars must exceed
    volPct (0.30)
  * NO take-profit: exits are the trailing stop and the time exit only
  * trail: 5 x ATR(signal bar) from the best price since entry, updated
    after each H1 close, ONLY EVER TIGHTENED (max/min with the prior stop)
  * initial stop: 2.5 x ATR(signal bar) from the entry
  * time exit: close at the bar where held >= 240 H1 bars
  * H4 context uses the [1] (previous completed H4 bar) values - the
    standard non-repainting security pattern; identical to an as-of read
    of H4 bars closed at/before the H1 close.

riskPct (1% sizing) is TESTER SIZING ONLY (Pine comment) - the platform's
risk-% engine owns sizing, as with every strategy.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

SPEC = {
    "name": "TrendBreak v2 (H1)",
    "entry_tf": "1H",
    "context_tf": "4H",
    "markets": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],   # v1 family rule
    "excluded_markets": ["XAGUSD"],
    "atr_len": 14,
    "lookback": 120,
    "stop_mult": 2.5,
    "trail_mult": 5.0,
    "gap_min": 2.0,
    "vol_pct": 0.30,
    "vol_win": 480,
    "max_bars": 240,
    "reference": ("owner Pine strategy defaults (tested rules); no TP - "
                  "trail 5xATR + 240-bar time exit are the only exits"),
}

SIGNAL = "SIGNAL"
NO_SIGNAL = "NO_SIGNAL"
INSUFFICIENT = "INSUFFICIENT_DATA"
OUT_OF_SPEC_MARKET = "OUT_OF_SPEC_MARKET"


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


def _atr_sma_true(df: pd.DataFrame, length: int) -> pd.Series:
    """ta.sma(ta.tr(true), length): TR uses high-low on the first bar."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=length).mean()


def _percentrank(series: pd.Series, window: int) -> pd.Series:
    """ta.percentrank: share of the window's values <= the current value."""
    def rank_of(win):
        cur = win[-1]
        return float((win <= cur).sum()) / len(win)
    return series.rolling(window, min_periods=window).apply(rank_of, raw=True)


def _trend_state(h4: pd.DataFrame) -> pd.DataFrame:
    ema50 = h4["close"].ewm(span=50, adjust=False).mean()
    ema200 = h4["close"].ewm(span=200, adjust=False).mean()
    up = (ema50 > ema200) & (h4["close"] > ema200)
    down = (ema50 < ema200) & (h4["close"] < ema200)
    return pd.DataFrame({"ema50": ema50, "ema200": ema200,
                         "trend": up.astype(int) - down.astype(int)})


def _trend_asof(trend_df: pd.DataFrame, t: pd.Timestamp) -> int:
    sub = trend_df.loc[trend_df.index <= t]
    return int(sub["trend"].iloc[-1]) if not sub.empty else 0


def _emas_asof(trend_df: pd.DataFrame, t: pd.Timestamp):
    sub = trend_df.loc[trend_df.index <= t]
    if sub.empty:
        return None, None
    return float(sub["ema50"].iloc[-1]), float(sub["ema200"].iloc[-1])


def check_market(market: Optional[str]) -> Optional[str]:
    if market is None:
        return None
    m = market.upper()
    if m in SPEC["excluded_markets"] or m not in SPEC["markets"]:
        return OUT_OF_SPEC_MARKET
    return None


def evaluate(h1, h4, market: Optional[str] = None,
             current_time_utc=None) -> Dict[str, Any]:
    """Signal evaluation on the latest CLOSED H1 bar (Pine: sigL/sigS)."""
    bad = check_market(market)
    if bad:
        return {"status": bad, "strategy": SPEC["name"],
                "reason": f"{market} is not in the strategy's market list"}
    try:
        df = _frame(h1, "H1")
        h4f = _frame(h4, "H4")
    except ValueError as exc:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"], "reason": str(exc)}
    if len(df) < SPEC["lookback"] + 2 or len(h4f) < 200:
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": (f"need >= {SPEC['lookback'] + 2} H1 / 200 H4 bars, "
                           f"got {len(df)}/{len(h4f)}")}
    if len(df) < SPEC["vol_win"] + SPEC["atr_len"]:
        # percentrank window not full yet -> volOk can never be true
        return {"status": INSUFFICIENT, "strategy": SPEC["name"],
                "reason": (f"ATR percentrank needs {SPEC['vol_win'] + SPEC['atr_len']} "
                           f"H1 bars, got {len(df)}")}

    atr = _atr_sma_true(df, SPEC["atr_len"])
    hh = df["high"].shift(1).rolling(SPEC["lookback"]).max()
    ll = df["low"].shift(1).rolling(SPEC["lookback"]).min()
    pr = _percentrank(atr, SPEC["vol_win"])
    trend_df = _trend_state(h4f)

    i = len(df) - 1
    t = df.index[i]
    a = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
    hh_i = hh.iloc[i]
    ll_i = ll.iloc[i]
    c, c_prev = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])
    e50, e200 = _emas_asof(trend_df, t)
    # the trend column already encodes both Pine legs (EMA50 vs EMA200 and
    # close vs EMA200) at the as-of H4 bar - the [1] security pattern
    trend = _trend_asof(trend_df, t)

    gap_ok = bool(e50 is not None and a > 0 and
                  abs(e50 - e200) / a > SPEC["gap_min"])
    vol_ok = bool(pd.notna(pr.iloc[i]) and pr.iloc[i] > SPEC["vol_pct"])
    raw_l = bool(pd.notna(hh_i) and c > float(hh_i) and trend == 1)
    raw_s = bool(pd.notna(ll_i) and c < float(ll_i) and trend == -1)
    hh_p, ll_p = hh.iloc[i - 1], ll.iloc[i - 1]
    fresh_l = raw_l and not (pd.notna(hh_p) and c_prev > float(hh_p) and
                             _trend_asof(trend_df, df.index[i - 1]) == 1)
    fresh_s = raw_s and not (pd.notna(ll_p) and c_prev < float(ll_p) and
                             _trend_asof(trend_df, df.index[i - 1]) == -1)
    direction = "BUY" if (fresh_l and gap_ok and vol_ok) else \
        ("SELL" if (fresh_s and gap_ok and vol_ok) else None)

    checks: List[Dict[str, Any]] = [
        {"rule": "H4 trend (prev completed bar)", "ok": trend != 0,
         "detail": f"trend {trend}, EMA50 {e50} EMA200 {e200}"},
        {"rule": f"gap |EMA50-EMA200| > {SPEC['gap_min']}xATR", "ok": gap_ok,
         "detail": (f"ratio {abs(e50 - e200) / a:.2f} (ATR {a:.6g})"
                    if e50 is not None else "no H4 data")},
        {"rule": f"ATR percentrank > {SPEC['vol_pct']}", "ok": vol_ok,
         "detail": f"percentrank {pr.iloc[i] if pd.notna(pr.iloc[i]) else 'n/a'}"},
        {"rule": "first breakout bar (120-bar channel)", "ok": direction is not None,
         "detail": f"close {c} vs HH {hh_i} / LL {ll_i}"}]
    if direction is None:
        return {"status": NO_SIGNAL, "strategy": SPEC["name"], "checks": checks,
                "timestamp": str(t)}
    return {"status": SIGNAL, "strategy": SPEC["name"], "direction": direction,
            "market": market, "signal_bar": str(t), "timestamp": str(t),
            "atr": a, "stop_mult": SPEC["stop_mult"],
            "trail_mult": SPEC["trail_mult"], "max_bars": SPEC["max_bars"],
            "entry_rule": "NEXT_BAR_OPEN", "trail_only": True,
            "checks": checks}


def backtest(h1, h4, market: str) -> Dict[str, Any]:
    """Exact Pine replay: next-open entry, initial stop 2.5xATR(signal),
    per-bar stop-first exits, trail tightened AFTER the close, 240-bar time
    exit at the close. R multiples against 2.5xATR risk."""
    bad = check_market(market)
    if bad:
        return {"status": bad, "strategy": SPEC["name"], "trades": [],
                "summary": {"n": 0}}
    df = _frame(h1, "H1")
    h4f = _frame(h4, "H4")
    atr = _atr_sma_true(df, SPEC["atr_len"])
    hh = df["high"].shift(1).rolling(SPEC["lookback"]).max()
    ll = df["low"].shift(1).rolling(SPEC["lookback"]).min()
    pr = _percentrank(atr, SPEC["vol_win"])
    trend_df = _trend_state(h4f)
    n = len(df)
    trades: List[Dict[str, Any]] = []
    i = max(SPEC["lookback"] + 1, SPEC["atr_len"] + SPEC["vol_win"] - 1) + 1
    while i < n - 1:
        t = df.index[i]
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0 or pd.isna(pr.iloc[i]):
            i += 1
            continue
        c, c_prev = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])
        e50, e200 = _emas_asof(trend_df, t)
        if e50 is None:
            i += 1
            continue
        trend = _trend_asof(trend_df, t)
        gap_ok = abs(e50 - e200) / float(a) > SPEC["gap_min"]
        vol_ok = float(pr.iloc[i]) > SPEC["vol_pct"]
        hh_i, ll_i = hh.iloc[i], ll.iloc[i]
        raw_l = pd.notna(hh_i) and c > float(hh_i) and trend == 1
        raw_s = pd.notna(ll_i) and c < float(ll_i) and trend == -1
        hp, lp = hh.iloc[i - 1], ll.iloc[i - 1]
        tp_prev = _trend_asof(trend_df, df.index[i - 1])
        fresh_l = raw_l and not (pd.notna(hp) and c_prev > float(hp) and tp_prev == 1)
        fresh_s = raw_s and not (pd.notna(lp) and c_prev < float(lp) and tp_prev == -1)
        direction = "BUY" if (fresh_l and gap_ok and vol_ok) else \
            ("SELL" if (fresh_s and gap_ok and vol_ok) else None)
        if direction is None:
            i += 1
            continue
        sig_atr = float(a)
        risk = SPEC["stop_mult"] * sig_atr
        sign = 1.0 if direction == "BUY" else -1.0
        j0 = j = i + 1
        entry = float(df["open"].iloc[j])
        stop = entry - sign * risk
        best = entry
        exit_px = exit_reason = exit_t = None
        while j < n:
            o = float(df["open"].iloc[j])
            hi_ = float(df["high"].iloc[j])
            lo_ = float(df["low"].iloc[j])
            cl = float(df["close"].iloc[j])
            gap_stop = (o <= stop) if sign == 1 else (o >= stop)
            touch_stop = (lo_ <= stop) if sign == 1 else (hi_ >= stop)
            if gap_stop:
                exit_px, exit_reason = o, "STOP_GAP_OPEN"
            elif touch_stop:
                exit_px, exit_reason = stop, "TRAIL_STOP"
            else:
                # Pine convention: bar_index - entry_bar_index (entry bar = 0)
                held = j - j0
                best = max(best, hi_) if sign == 1 else min(best, lo_)
                trail = best - sign * SPEC["trail_mult"] * sig_atr
                stop = max(stop, trail) if sign == 1 else min(stop, trail)
                if held >= SPEC["max_bars"]:
                    exit_px, exit_reason = cl, "TIME_EXIT_240"
            if exit_px is not None:
                exit_t = df.index[j]
                break
            j += 1
        if exit_px is None:               # data ended in-trade: honest marker
            exit_px, exit_reason, exit_t = float(df["close"].iloc[n - 1]), "DATA_END", df.index[n - 1]
        r = ((exit_px - entry) / risk) * sign
        trades.append({
            "market": market, "direction": direction,
            "signal_bar": str(t), "entry_time": str(df.index[i + 1]),
            "entry": round(entry, 6), "stop_initial": round(entry - sign * risk, 6),
            "exit_time": str(exit_t), "exit": round(exit_px, 6),
            "exit_reason": exit_reason, "r_multiple": round(r, 4),
            "bars_held": j - j0, "no_tp": True})
        i = j + 1                          # one position at a time
    from .trendbreak import _summarize
    return {"status": "OK", "strategy": SPEC["name"], "trades": trades,
            "summary": _summarize(trades), "reference": SPEC["reference"]}
