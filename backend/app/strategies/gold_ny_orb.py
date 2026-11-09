"""
FOREXMIND strategy module: Gold NY 30-min Opening Range Breakout
(research name: C1 = "ORB15|XAUUSD|ny|or30|rr2.0|g1")
Follows strategies/ STRATEGY MODULE TEMPLATE (owner spec 2026-11-02).
=============================================================================

MARKET / TIMEFRAME: XAUUSD only, 15M entries. 1H trend gate, 1D regime.

RULES (UTC; closed 15M bars only; bar times = bar OPEN times)
  1. Opening range (OR) = high/low of the 15M bars opening 13:30 and 13:45 UTC (13:30-14:00).
  2. Breakout window: bars opening 14:00 ... 15:45 UTC.
  3. Range sanity: 0.35*ATR14 <= OR height <= 2.8*ATR14 (ATR14 of the signal bar, 15M).
  4. BUY: close > OR high and last COMPLETED H1 close >= its EMA20 (trend +1, or 0 if unknown).
     SELL: close < OR low and H1 trend <= 0.
  5. SL: BUY max(OR low, close - 1.2*ATR); SELL min(OR high, close + 1.2*ATR).
     TP = entry + 2.0 * risk (single TP, all-out).
  6. One break per UTC day: the first qualifying break consumes the day (counted in compute()).
  7. D1 regime filter identical to gold_ny_pullback (SMA50/200 aligned + ADX>=20, D1 bar two back).
  8. Time exit after 24 completed 15M bars (6 h) -> Candidate.extra["max_hold_bars"]=24.
     The engine plan block must honour it.

TEMPLATE CONTRACT NOTES: same as gold_ny_pullback.py. The 1H/1D frames are resampled from 15M
inside compute() with completed-bar alignment, all logic is precomputed into state,
detect_on_bar(state, i) only reads state, and higher_frames is deliberately unused.
Candidate.entry = close of the signal bar (template convention). The research filled at the
next bar's open. Sessions are UTC (set server_utc_offset_hours or feed UTC). Max 1 open
position per strategy.

MANAGEMENT: locked plan (fixed SL, single TP 2R, 24-bar time exit, no partials/BE/trail/AI).

NOT FINANCIAL ADVICE. Past backtest results do not guarantee future results.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .base import BaseStrategy, Candidate


# ----------------------------------------------------------------------------- helpers
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_n = _atr(df, 1).rolling(n, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n, min_periods=n).mean() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n, min_periods=n).mean() / atr_n
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.rolling(n, min_periods=n).mean()


_TF_PERIOD = {"1H": pd.Timedelta(hours=1), "4H": pd.Timedelta(hours=4), "1D": pd.Timedelta(days=1)}
_TF_RULE = {"1H": "1h", "4H": "4h", "1D": "1D"}


def _to_utc_frame(df: pd.DataFrame, offset_hours: float = 0.0) -> pd.DataFrame:
    """Return an OHLC frame with a tz-aware UTC DatetimeIndex (bar OPEN time)."""
    out = df
    if not isinstance(out.index, pd.DatetimeIndex):
        for col in ("time", "datetime", "timestamp", "date"):
            if col in out.columns:
                vals = out[col]
                if np.issubdtype(vals.dtype, np.number):
                    idx = pd.to_datetime(vals, unit="ms" if vals.iloc[0] > 1e11 else "s", utc=True)
                else:
                    idx = pd.to_datetime(vals, utc=offset_hours == 0)
                out = out.set_index(pd.DatetimeIndex(idx))
                break
    idx = out.index
    if idx.tz is None:
        idx = idx - pd.Timedelta(hours=offset_hours)
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    out = out.copy()
    out.index = idx
    out.columns = [c.lower() for c in out.columns]
    # NOTE: no de-duplication / re-sorting here, so row i of the state == row i of df.
    return out[["open", "high", "low", "close"]].astype(float)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    lo = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    out = pd.concat([o, h, lo, c], axis=1)
    out.columns = ["open", "high", "low", "close"]
    return out.dropna()


def _align_completed(htf_values: pd.DataFrame, period: pd.Timedelta, ltf_index: pd.DatetimeIndex) -> pd.DataFrame:
    """For each LTF bar OPEN time t, take the last HTF row whose close (open+period) <= t."""
    v = htf_values.copy()
    v["_avail"] = v.index + period
    v = v.sort_values("_avail")
    left = pd.DataFrame({"_t": ltf_index})
    merged = pd.merge_asof(left, v, left_on="_t", right_on="_avail", direction="backward")
    merged.index = ltf_index
    return merged.drop(columns=["_t", "_avail"])


def _regime(p: Dict[str, Any], d1: pd.DataFrame, ltf_index: pd.DatetimeIndex) -> np.ndarray:
    d = d1.copy()
    sma_f = d["close"].rolling(p["regime_sma_fast"], min_periods=p["regime_sma_fast"]).mean()
    sma_s = d["close"].rolling(p["regime_sma_slow"], min_periods=p["regime_sma_slow_min_periods"]).mean()
    adxv = _adx(d, p["regime_adx_period"])
    trending = ((d["close"] > sma_f) & (sma_f > sma_s)) | ((d["close"] < sma_f) & (sma_f < sma_s))
    ok = (trending & (adxv >= p["regime_adx_min"])).astype(float)
    ok[adxv.isna()] = 0.0
    lag = int(p["regime_extra_lag_bars"])
    ok = ok.shift(lag) if lag else ok
    f = pd.DataFrame({"ok": ok.values, "_key": d.index.tz_convert("UTC").tz_localize(None).normalize()})
    f = f[~f["_key"].duplicated(keep="last")].sort_values("_key")
    left = pd.DataFrame({"_q": ltf_index.tz_convert("UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)})
    m = pd.merge_asof(left, f, left_on="_q", right_on="_key", direction="backward", tolerance=pd.Timedelta(days=6))
    return m["ok"].fillna(0.0).values > 0.5




# ----------------------------------------------------------------------------- strategy
class GoldNYORBStrategy(BaseStrategy):
    id = "strategy_gold_ny_orb"
    name = "Gold NY Opening Range Breakout"
    short_name = "GOLDORB"
    description = ("XAUUSD 15M breakout of the New York opening range: mark the high and low of 13:30-14:00 UTC, "
                   "then between 14:00 and 16:00 UTC take the first 15M close beyond the range in the direction of "
                   "the last completed 1H close versus its EMA20, only if the range is 0.35-2.8 ATR tall and the "
                   "daily chart is trending (close/SMA50/SMA200 aligned and daily ADX>=20). Stop at the opposite "
                   "side of the range but no further than 1.2 ATR; single target at 2R; close after 24 bars (6h) "
                   "if neither is hit; one trade per day.")
    version = "1.0"
    markets = ["XAUUSD"]
    timeframes = ["15M"]
    min_bars = 300

    base_params: Dict[str, Any] = {
        "server_utc_offset_hours": 0.0,
        "or_start_hour_utc": 13,
        "or_start_minute_utc": 30,
        "or_minutes": 30,
        "trade_end_hour_utc": 16,
        "atr_period": 14,
        "range_min_atr": 0.35,
        "range_max_atr": 2.8,
        "stop_max_atr": 1.2,
        "min_risk_price": 0.1,
        "rr": 2.0,
        "max_signals_per_day": 1,
        "max_hold_bars": 24,
        "h1_ema": 20,
        "use_regime_filter": True,
        "regime_sma_fast": 50,
        "regime_sma_slow": 200,
        "regime_sma_slow_min_periods": 100,
        "regime_adx_period": 14,
        "regime_adx_min": 20.0,
        "regime_extra_lag_bars": 1,
    }
    experiment_variables: Dict[str, Any] = {
        "rr":                 {"type": "float", "min": 1.5,  "max": 3.0},
        "or_minutes":         {"type": "int",   "min": 15,   "max": 30},
        "range_min_atr":      {"type": "float", "min": 0.2,  "max": 0.6},
        "range_max_atr":      {"type": "float", "min": 2.0,  "max": 3.5},
        "stop_max_atr":       {"type": "float", "min": 0.8,  "max": 1.6},
        "max_hold_bars":      {"type": "int",   "min": 12,   "max": 32},
        "trade_end_hour_utc": {"type": "int",   "min": 15,   "max": 17},
        "regime_adx_min":     {"type": "float", "min": 15.0, "max": 28.0},
    }

    # ------------------------------------------------------------------ compute
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        p = {**self.base_params, **(params or {})}
        base = _to_utc_frame(df, float(p["server_utc_offset_hours"]))
        out = base.copy()
        out["atr"] = _atr(out, int(p["atr_period"]))
        h1, d1 = _resample(base, _TF_RULE["1H"]), _resample(base, _TF_RULE["1D"])
        hv = pd.DataFrame({"h1_trend": np.where(h1["close"] > _ema(h1["close"], int(p["h1_ema"])), 1, -1)}, index=h1.index)
        out["h1_trend"] = _align_completed(hv, _TF_PERIOD["1H"], out.index)["h1_trend"].fillna(0).values
        out["regime_ok"] = _regime(p, d1, out.index)

        tod = out.index.hour * 60 + out.index.minute
        or_start = int(p["or_start_hour_utc"]) * 60 + int(p["or_start_minute_utc"])
        or_end = or_start + int(p["or_minutes"])
        in_or = (tod >= or_start) & (tod < or_end)
        in_trade = (tod >= or_end) & (tod < int(p["trade_end_hour_utc"]) * 60)
        dates = out.index.date
        n = len(out)
        sig = np.zeros(n, dtype=int)
        stop = np.full(n, np.nan)
        orh, orl = np.full(n, np.nan), np.full(n, np.nan)
        close, high, low, atrv = out["close"].values, out["high"].values, out["low"].values, out["atr"].values
        trs, reg = out["h1_trend"].values, out["regime_ok"].values
        smax, minr, cap = float(p["stop_max_atr"]), float(p["min_risk_price"]), int(p["max_signals_per_day"])
        cur_day, day_hi, day_lo, used = None, np.nan, np.nan, 0
        for i in range(n):
            d = dates[i]
            if d != cur_day:
                cur_day, day_hi, day_lo, used = d, np.nan, np.nan, 0
            if in_or[i]:
                day_hi = high[i] if np.isnan(day_hi) else max(day_hi, high[i])
                day_lo = low[i] if np.isnan(day_lo) else min(day_lo, low[i])
                continue
            if not in_trade[i] or np.isnan(day_hi):
                continue
            orh[i], orl[i] = day_hi, day_lo
            if used >= cap:
                continue
            a = atrv[i]
            if np.isnan(a) or a <= 0:
                continue
            rng = day_hi - day_lo
            if rng < p["range_min_atr"] * a or rng > p["range_max_atr"] * a:
                continue
            c = close[i]
            if c > day_hi and trs[i] >= 0:
                st = max(day_lo, c - smax * a)
                if c - st > minr:
                    used += 1
                    if (not p["use_regime_filter"]) or reg[i]:
                        sig[i], stop[i] = 1, st
            elif c < day_lo and trs[i] <= 0:
                st = min(day_hi, c + smax * a)
                if st - c > minr:
                    used += 1
                    if (not p["use_regime_filter"]) or reg[i]:
                        sig[i], stop[i] = -1, st
        out["in_trade_window"] = in_trade
        out["or_high"], out["or_low"] = orh, orl
        out["signal"] = sig
        out["signal_stop"] = stop
        out["signal_target"] = out["close"] + np.sign(sig) * float(p["rr"]) * (out["close"] - out["signal_stop"]).abs()
        state: Dict[str, Any] = {c: out[c] for c in out.columns}
        state["time_utc"] = pd.Series(out.index, index=out.index)
        state["frame"] = out
        return state

    # ------------------------------------------------------------------ detection
    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        s = int(state["signal"].iloc[i])
        if s > 0:
            return {"direction": "BUY", "i": i}
        if s < 0:
            return {"direction": "SELL", "i": i}
        return None

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        # higher_frames intentionally unused: HTF comes from compute() so live == backtest.
        p = {**self.base_params, **(params or {})}
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, p)
        if not np.isfinite(rk["risk"]) or rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, p)
        checks, analysis = self.explain_signal(state, i, direction, p, {})
        mh = int(p["max_hold_bars"])
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe, direction=direction,
            candle_time=str(df.index[i]), entry=rk["entry"], entry_zone=[rk["entry"], rk["entry"]],
            sl=rk["sl"], tps=tps, risk=rk["risk"],
            rr_primary=(abs(tps[0] - rk["entry"]) / rk["risk"]) if tps else 0.0,
            score=50, score_components={"gold_ny_orb_rules": 50.0}, checks=checks, analysis=analysis,
            params={}, params_version=self.version,
            extra={"management_plan": "GOLD_NY_ORB_LOCKED", "max_hold_bars": mh,
                   "max_hold_minutes": mh * 15, "max_open_positions": 1,
                   "or_high": float(state["or_high"].iloc[i]), "or_low": float(state["or_low"].iloc[i]),
                   "candle_time_utc": str(state["time_utc"].iloc[i]),
                   "backtest_fill": "next_bar_open"},
            metadata={"source": "owner spec 2026-11-02 template; research round6 C1 ORB15|XAUUSD|ny|or30|rr2.0|g1"},
        )

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        sl = float(state["signal_stop"].iloc[i])
        if not np.isfinite(sl):
            return {"entry": entry, "sl": entry, "risk": 0.0}
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str, params: Dict[str, Any]) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float({**self.base_params, **(params or {})}["rr"])]

    def explain_signal(self, state, i, direction, params, extra):
        p = {**self.base_params, **(params or {})}
        buy = direction == "BUY"
        g = lambda k: state[k].iloc[i]
        a = g("atr")
        rng = g("or_high") - g("or_low")
        checks = [
            {"label": "Breakout window 14:00-16:00 UTC", "ok": bool(g("in_trade_window")), "detail": f"bar {g('time_utc')}"},
            {"label": "D1 regime trending (SMA50/200 aligned, ADX>=20)", "ok": bool(g("regime_ok")), "detail": "D1 bar two back"},
            {"label": f"OR height {p['range_min_atr']}-{p['range_max_atr']} ATR",
             "ok": bool(p["range_min_atr"] * a <= rng <= p["range_max_atr"] * a), "detail": f"range={rng:.2f} atr={a:.2f}"},
            {"label": "Close beyond opening range", "ok": bool(g("close") > g("or_high")) if buy else bool(g("close") < g("or_low")),
             "detail": f"close={g('close'):.2f} OR=[{g('or_low'):.2f}, {g('or_high'):.2f}]"},
            {"label": "H1 close vs EMA20 agrees", "ok": bool(g("h1_trend") >= 0) if buy else bool(g("h1_trend") <= 0),
             "detail": f"h1_trend={int(g('h1_trend'))}"},
        ]
        analysis = (f"{direction} gold NY opening-range breakout: 15M close broke the 13:30-14:00 UTC range with the H1 "
                    f"trend, daily chart trending. Stop at the opposite range edge capped at {p['stop_max_atr']} ATR, "
                    f"single target {p['rr']}R, close after {p['max_hold_bars']} bars if neither is hit; 1 trade/day.")
        return checks, analysis

    def tp_rrs(self, params) -> List[float]:
        return [float({**self.base_params, **(params or {})}["rr"])]
