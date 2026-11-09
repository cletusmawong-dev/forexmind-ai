"""
FOREXMIND strategy module: Gold NY EMA Pullback (research name: S1_2DAY_FILT)
Follows strategies/ STRATEGY MODULE TEMPLATE (owner spec 2026-11-02).
=============================================================================

MARKET / TIMEFRAME: XAUUSD only, 15M entries. 1H / 4H / 1D used for analysis only.

RULES (UTC; closed 15M bars only; bar times = bar OPEN times)
  1. Session: signal bar opens 13:00-16:45 UTC (open hour in [13, 17)).
  2. D1 regime: close>SMA50>SMA200 (up) or close<SMA50<SMA200 (down), AND D1 ADX14 >= 20.
     Uses the D1 bar TWO bars back (exactly as tested; param regime_extra_lag_bars=1).
  3. H1 bias (last COMPLETED H1 bar): BUY if EMA20>EMA50 and close>EMA20; SELL mirror. H1 ADX14 >= 20.
  4. H4 not opposed: last completed H4 close vs H4 EMA20.
  5. D1 not opposed: last completed D1 close vs D1 EMA50.
  6. Volatility: ATR14(15M)/close*100 >= 0.07.
  7. Trigger (BUY; SELL mirror): low <= EMA21 on any of bars i-3..i, close[i] > EMA9[i] with
     close[i-1] <= EMA9[i-1], and close[i] > EMA21[i].
  8. SL = lowest low of bars i-4..i - 0.1*ATR14. TP = entry + 3.0 * risk (single TP, all-out).
  9. Max 2 signals per UTC day (counted in compute(); a signal counts even if not taken).
 10. Time exit after 32 completed 15M bars (8 h) -> Candidate.extra["max_hold_bars"]=32.
     The engine plan block must honour it (the inherited backtest() does not know about it).

TEMPLATE CONTRACT NOTES
  * compute(df, params) only receives the 15M df, so 1H/4H/1D are RESAMPLED from 15M inside
    compute() (UTC buckets). A 15M bar opening at t only uses HTF bars whose CLOSE (open+period)
    <= t, so there is no lookahead, even on the live forming bar. Everything (HTF bias, regime,
    session, day cap, stop/target levels) is precomputed into `state`. detect_on_bar(state, i)
    only reads it. higher_frames passed to build_candidate is deliberately NOT used, so live
    signals and the inherited backtest() are identical.
  * History: SMA200 on D1 (min_periods 100) resampled from 15M needs >= ~10,000 15M bars
    (~22,000 for a full SMA200). With less history the regime reads "not trending" -> no trades.
  * Entry convention: per template, Candidate.entry = close of signal bar i and SL/TP are
    computed from it (TP = close + 3R, the same TP price the research used). The research
    backtest FILLED at the next bar's open (+ half spread), which is what a market order placed
    at bar close gets. The stop is a fixed price, so realised R varies slightly with the fill.
  * Time zone: sessions are UTC. If df timestamps are broker server time (naive), set
    params["server_utc_offset_hours"] (fixed offset), or better, convert to UTC upstream (DST-safe).
  * One open position per strategy at a time (as tested): enforce in the engine plan block.

MANAGEMENT: locked plan (fixed SL, single TP 3R, 32-bar time exit, no partials/BE/trail/AI).
The Universal ladder cut this strategy's pass rate from ~34% to ~9% in testing.

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
class GoldNYPullbackStrategy(BaseStrategy):
    id = "strategy_gold_ny_pullback"
    name = "Gold NY EMA Pullback"
    short_name = "GOLDPB"
    description = ("XAUUSD 15M trend pullback in the New York session (signals 13:00-17:00 UTC). Trade only when "
                   "the daily chart is trending (close/SMA50/SMA200 aligned and daily ADX>=20), the last completed "
                   "1H bar has EMA20 beyond EMA50 with price beyond EMA20 and 1H ADX>=20, and the 4H (EMA20) and "
                   "daily (EMA50) charts do not point the other way, with ATR at least 0.07% of price. Enter when "
                   "price has touched the 15M EMA21 within the last 4 bars and the bar closes back through EMA9 "
                   "(and beyond EMA21). Stop beyond the 5-bar swing plus 0.1 ATR; single target at 3R; close "
                   "after 32 bars (8h) if neither is hit; at most 2 signals per UTC day.")
    version = "1.0"
    markets = ["XAUUSD"]
    timeframes = ["15M"]
    min_bars = 300

    base_params: Dict[str, Any] = {
        "server_utc_offset_hours": 0.0,
        "session_start_hour_utc": 13,
        "session_end_hour_utc": 17,
        "ema_fast": 9,
        "ema_pullback": 21,
        "atr_period": 14,
        "atr_pct_min": 0.07,
        "touch_lookback_bars": 4,
        "swing_lookback_bars": 5,
        "stop_atr_buffer": 0.1,
        "min_risk_price": 0.1,
        "rr": 3.0,
        "max_signals_per_day": 2,
        "max_hold_bars": 32,
        "warmup_bars": 60,
        "h1_ema_fast": 20,
        "h1_ema_slow": 50,
        "h1_adx_period": 14,
        "h1_adx_min": 20.0,
        "h1_require_close_beyond_fast": True,
        "require_h4": True,
        "h4_ema": 20,
        "d1_ema": 50,
        "use_regime_filter": True,
        "regime_sma_fast": 50,
        "regime_sma_slow": 200,
        "regime_sma_slow_min_periods": 100,
        "regime_adx_period": 14,
        "regime_adx_min": 20.0,
        "regime_extra_lag_bars": 1,
    }
    experiment_variables: Dict[str, Any] = {
        "rr":                   {"type": "float", "min": 2.0,  "max": 4.0},
        "h1_adx_min":           {"type": "float", "min": 15.0, "max": 30.0},
        "atr_pct_min":          {"type": "float", "min": 0.04, "max": 0.12},
        "regime_adx_min":       {"type": "float", "min": 15.0, "max": 28.0},
        "stop_atr_buffer":      {"type": "float", "min": 0.0,  "max": 0.3},
        "max_signals_per_day":  {"type": "int",   "min": 1,    "max": 3},
        "max_hold_bars":        {"type": "int",   "min": 16,   "max": 48},
        "session_end_hour_utc": {"type": "int",   "min": 16,   "max": 18},
    }

    # ------------------------------------------------------------------ compute
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        p = {**self.base_params, **(params or {})}
        base = _to_utc_frame(df, float(p["server_utc_offset_hours"]))
        out = base.copy()
        out["ema9"] = _ema(out["close"], int(p["ema_fast"]))
        out["ema21"] = _ema(out["close"], int(p["ema_pullback"]))
        out["atr"] = _atr(out, int(p["atr_period"]))
        out["atr_pct"] = out["atr"] / out["close"] * 100

        h1, h4, d1 = (_resample(base, _TF_RULE[k]) for k in ("1H", "4H", "1D"))
        e20, e50 = _ema(h1["close"], int(p["h1_ema_fast"])), _ema(h1["close"], int(p["h1_ema_slow"]))
        if p["h1_require_close_beyond_fast"]:
            up, dn = (e20 > e50) & (h1["close"] > e20), (e20 < e50) & (h1["close"] < e20)
        else:
            up, dn = e20 > e50, e20 < e50
        hv = pd.DataFrame({"h1_bias": np.where(up, 1, np.where(dn, -1, 0)),
                           "h1_adx": _adx(h1, int(p["h1_adx_period"]))}, index=h1.index)
        a = _align_completed(hv, _TF_PERIOD["1H"], out.index)
        out["h1_bias"] = a["h1_bias"].fillna(0).values
        out["h1_adx"] = a["h1_adx"].values
        h4v = pd.DataFrame({"h4_bias": np.where(h4["close"] > _ema(h4["close"], int(p["h4_ema"])), 1, -1)}, index=h4.index)
        out["h4_bias"] = _align_completed(h4v, _TF_PERIOD["4H"], out.index)["h4_bias"].fillna(0).values
        d1v = pd.DataFrame({"d1_bias": np.where(d1["close"] > _ema(d1["close"], int(p["d1_ema"])), 1, -1)}, index=d1.index)
        out["d1_bias"] = _align_completed(d1v, _TF_PERIOD["1D"], out.index)["d1_bias"].fillna(0).values
        out["regime_ok"] = _regime(p, d1, out.index)

        n = len(out)
        sig = np.zeros(n, dtype=int)
        stop = np.full(n, np.nan)
        in_sess = np.zeros(n, dtype=bool)
        close, high, low = out["close"].values, out["high"].values, out["low"].values
        e9, e21, atrv, atrp = out["ema9"].values, out["ema21"].values, out["atr"].values, out["atr_pct"].values
        hb, hadx, h4b, d1b = out["h1_bias"].values, out["h1_adx"].values, out["h4_bias"].values, out["d1_bias"].values
        reg = out["regime_ok"].values
        hours, dates = out.index.hour, out.index.date
        tl, sl = int(p["touch_lookback_bars"]), int(p["swing_lookback_bars"])
        buf, minr, cap = float(p["stop_atr_buffer"]), float(p["min_risk_price"]), int(p["max_signals_per_day"])
        day_count: Dict[Any, int] = {}
        for i in range(max(int(p["warmup_bars"]), tl, sl), n):
            if not (p["session_start_hour_utc"] <= hours[i] < p["session_end_hour_utc"]):
                continue
            in_sess[i] = True
            d = dates[i]
            if day_count.get(d, 0) >= cap:
                continue
            if np.isnan(hadx[i]) or hadx[i] < p["h1_adx_min"]:
                continue
            if np.isnan(atrp[i]) or atrp[i] < p["atr_pct_min"] or np.isnan(atrv[i]):
                continue
            b, c, a_ = hb[i], close[i], atrv[i]
            if b > 0:
                if (p["require_h4"] and h4b[i] < 0) or d1b[i] < 0:
                    continue
                touched = any(low[i - k] <= e21[i - k] for k in range(tl))
                if touched and c > e9[i] and close[i - 1] <= e9[i - 1] and c > e21[i]:
                    st = low[i - sl + 1: i + 1].min() - buf * a_
                    if c - st > minr:
                        day_count[d] = day_count.get(d, 0) + 1
                        if (not p["use_regime_filter"]) or reg[i]:
                            sig[i], stop[i] = 1, st
            elif b < 0:
                if (p["require_h4"] and h4b[i] > 0) or d1b[i] > 0:
                    continue
                touched = any(high[i - k] >= e21[i - k] for k in range(tl))
                if touched and c < e9[i] and close[i - 1] >= e9[i - 1] and c < e21[i]:
                    st = high[i - sl + 1: i + 1].max() + buf * a_
                    if st - c > minr:
                        day_count[d] = day_count.get(d, 0) + 1
                        if (not p["use_regime_filter"]) or reg[i]:
                            sig[i], stop[i] = -1, st
        out["in_session"] = in_sess
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
            score=50, score_components={"gold_ny_pullback_rules": 50.0}, checks=checks, analysis=analysis,
            params={}, params_version=self.version,
            extra={"management_plan": "GOLD_NY_PULLBACK_LOCKED", "max_hold_bars": mh,
                   "max_hold_minutes": mh * 15, "max_open_positions": 1,
                   "candle_time_utc": str(state["time_utc"].iloc[i]),
                   "backtest_fill": "next_bar_open"},
            metadata={"source": "owner spec 2026-11-02 template; research round6 S1_2DAY_FILT"},
        )

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        sl = float(state["signal_stop"].iloc[i])
        if not np.isfinite(sl):  # not a signal bar
            return {"entry": entry, "sl": entry, "risk": 0.0}
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(self, entry: float, risk: float, direction: str, params: Dict[str, Any]) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float({**self.base_params, **(params or {})}["rr"])]

    def explain_signal(self, state, i, direction, params, extra):
        p = {**self.base_params, **(params or {})}
        buy = direction == "BUY"
        g = lambda k: state[k].iloc[i]
        want = 1 if buy else -1
        checks = [
            {"label": "NY session 13:00-17:00 UTC", "ok": bool(g("in_session")), "detail": f"bar {g('time_utc')}"},
            {"label": "D1 regime trending (SMA50/200 aligned, ADX>=20)", "ok": bool(g("regime_ok")), "detail": "D1 bar two back"},
            {"label": "H1 EMA20/50 bias agrees", "ok": int(g("h1_bias")) == want, "detail": f"bias={int(g('h1_bias'))}"},
            {"label": f"H1 ADX >= {p['h1_adx_min']}", "ok": bool(g("h1_adx") >= p["h1_adx_min"]), "detail": f"adx={g('h1_adx'):.1f}"},
            {"label": "H4 not opposed (close vs EMA20)", "ok": int(g("h4_bias")) != -want, "detail": f"h4={int(g('h4_bias'))}"},
            {"label": "D1 not opposed (close vs EMA50)", "ok": int(g("d1_bias")) != -want, "detail": f"d1={int(g('d1_bias'))}"},
            {"label": f"ATR% >= {p['atr_pct_min']}", "ok": bool(g("atr_pct") >= p["atr_pct_min"]), "detail": f"atr%={g('atr_pct'):.3f}"},
            {"label": "EMA21 pullback + EMA9 reclaim", "ok": int(g("signal")) == want,
             "detail": f"close={g('close'):.2f} ema9={g('ema9'):.2f} ema21={g('ema21'):.2f}"},
        ]
        analysis = (f"{direction} gold NY pullback: trend aligned on D1/H4/H1, price pulled back to the 15M EMA21 and "
                    f"closed back through EMA9. Stop beyond the 5-bar swing +{p['stop_atr_buffer']} ATR, single target "
                    f"{p['rr']}R, close after {p['max_hold_bars']} bars if neither is hit.")
        return checks, analysis

    def tp_rrs(self, params) -> List[float]:
        return [float({**self.base_params, **(params or {})}["rr"])]
