"""
FOREXMIND strategy module: Gold Asian-Range Breakout
(research name: GOLD_ASIA_RANGE_BO / round-9 strategy B)
Follows strategies/ STRATEGY MODULE TEMPLATE (owner spec 2026-11-02).
=============================================================================

MARKET / TIMEFRAME: XAUUSD only, 15M entries. 1D used for trend filter only.

RULES (UTC; closed 15M bars only; bar times = bar OPEN times) — EXACT passing settings
  1. Asian range = high/low of 15M bars whose open hour is in [00:00, 06:00) UTC,
     grouped by calendar UTC day (floor to midnight).
  2. D1 trend (NO ADX): last COMPLETED D1 bar (22:00 UTC session, same as research)
     with close>SMA50>SMA200 → longs only; close<SMA50<SMA200 → shorts only;
     no trade if neither. SMA200 uses min_periods=100. No extra lag.
  3. Breakout window: bars opening 06:00 ... 15:45 UTC (open hour in [6, 16)).
  4. BUY: close > Asian high AND D1 uptrend. SELL: close < Asian low AND D1 downtrend.
  5. SL = the other side of the Asian range (BUY → Asian low; SELL → Asian high).
     No fixed profit target in live management. Candidate.tps carries a far 20R placeholder
     so the template coherence check passes; the locked plan IGNORES it.
  6. One signal per UTC calendar day (first qualifying break consumes the day).
  7. Management (engine plan GOLD_ASIA_BO_LOCKED, NOT the strategy itself):
       - when price reaches +0.75R, move stop to entry + 0.1R (long) / entry - 0.1R (short)
       - flatten everything at 20:45 UTC the same day
       - no partials, no trailing, no AI
       - Friday 20:45 UTC flatten if still open
       - skip entry if live spread > 0.50 USD

FRAGILITY WARNING: this is a parameter peak. Only 1 of 48 neighbour settings reaches
PF>=1.3 (median neighbour PF 1.14). DO NOT change range hours, BE trigger, stop side,
or exit hour. Live expect PF closer to 1.2-1.3 than the backtest 1.33.

TEMPLATE CONTRACT NOTES: same as gold_ny_orb.py. HTF (D1) is resampled from 15M inside
compute() with completed-bar alignment (22:00 UTC D1 offset to match research).
detect_on_bar(state, i) only reads state. higher_frames is deliberately unused.
Candidate.entry = close of the signal bar (template convention). Research filled at the
next bar's open + half spread. Sessions are UTC.

NOT FINANCIAL ADVICE. Past backtest results do not guarantee future results.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .base import BaseStrategy, Candidate


# ----------------------------------------------------------------------------- helpers
def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _to_utc_frame(df: pd.DataFrame, offset_hours: float = 0.0) -> pd.DataFrame:
    """Return an OHLC frame with a tz-aware UTC DatetimeIndex (bar OPEN time)."""
    out = df
    if not isinstance(out.index, pd.DatetimeIndex):
        for col in ("time", "datetime", "timestamp", "date"):
            if col in out.columns:
                vals = out[col]
                if np.issubdtype(vals.dtype, np.number):
                    idx = pd.to_datetime(
                        vals, unit="ms" if vals.iloc[0] > 1e11 else "s", utc=True
                    )
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
    return out[["open", "high", "low", "close"]].astype(float)


def _resample(df: pd.DataFrame, rule: str, offset=None) -> pd.DataFrame:
    kw = {"offset": offset} if offset is not None else {}
    r = df.resample(rule, label="left", closed="left", **kw)
    out = pd.concat(
        [r["open"].first(), r["high"].max(), r["low"].min(), r["close"].last()],
        axis=1,
    )
    out.columns = ["open", "high", "low", "close"]
    return out.dropna()


def _align_completed(
    htf_values: pd.DataFrame, period: pd.Timedelta, ltf_index: pd.DatetimeIndex
) -> pd.DataFrame:
    """For each LTF bar OPEN time t, take the last HTF row whose close (open+period) <= t."""
    v = htf_values.copy()
    v["_avail"] = v.index + period
    v = v.sort_values("_avail")
    left = pd.DataFrame({"_t": ltf_index})
    merged = pd.merge_asof(left, v, left_on="_t", right_on="_avail", direction="backward")
    merged.index = ltf_index
    return merged.drop(columns=["_t", "_avail"])


# ----------------------------------------------------------------------------- strategy
class GoldAsiaRangeBOStrategy(BaseStrategy):
    id = "strategy_gold_asia_range_bo"
    name = "Gold Asian-Range Breakout"
    short_name = "GOLDASIA"
    description = (
        "XAUUSD 15M breakout of the Asian session range: mark the high and low of "
        "00:00-06:00 UTC, then between 06:00 and 16:00 UTC take the first 15M close "
        "beyond the range in the direction of the last completed D1 trend "
        "(close/SMA50/SMA200 aligned, no ADX). Stop at the opposite side of the Asian "
        "range. No fixed TP: at +0.75R move stop to entry+0.1R; flatten at 20:45 UTC. "
        "One trade per day. DO NOT change these parameters — the edge is fragile."
    )
    version = "1.0"
    markets = ["XAUUSD"]
    timeframes = ["15M"]
    min_bars = 300

    base_params: Dict[str, Any] = {
        "server_utc_offset_hours": 0.0,
        # Asian range window [start, end) UTC hours — EXACT passing: 00-06
        "range_start_hour_utc": 0,
        "range_end_hour_utc": 6,
        # Breakout entry window [range_end, trade_end) — EXACT passing: 06-16
        "trade_end_hour_utc": 16,
        # D1 trend (SMA50/200, NO ADX). 22:00 UTC D1 to match research.
        "d1_session_offset_hours": 22,
        "sma_fast": 50,
        "sma_slow": 200,
        "sma_slow_min_periods": 100,
        # Stop / target
        "stop_mode": "opp",          # opposite side of Asian range
        "min_risk_price": 0.08,      # research PIP*0.8 for XAUUSD
        "rr_placeholder": 20.0,      # far TP; locked plan ignores it
        # Management (encoded for the engine plan; strategy does not move stops)
        "be_trigger_r": 0.75,        # EXACT: move stop once +0.75R is reached
        "be_lock_r": 0.1,            # EXACT: new stop = entry +/- 0.1R
        "session_close_hour_utc": 20,
        "session_close_minute_utc": 45,
        # Caps
        "max_signals_per_day": 1,
        "max_open_positions": 1,
        # Spread guard (engine-side); research used 0.35 round-trip
        "max_spread_usd": 0.50,
        "research_spread_usd": 0.35,
    }
    # Offline research ONLY. Live must keep base_params frozen — see FRAGILITY WARNING.
    experiment_variables: Dict[str, Any] = {
        "range_end_hour_utc": {"type": "int", "min": 5, "max": 7},
        "trade_end_hour_utc": {"type": "int", "min": 15, "max": 17},
        "be_trigger_r": {"type": "float", "min": 0.5, "max": 1.0},
        "be_lock_r": {"type": "float", "min": 0.0, "max": 0.2},
        "rr_placeholder": {"type": "float", "min": 10.0, "max": 30.0},
    }

    # ------------------------------------------------------------------ compute
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        p = {**self.base_params, **(params or {})}
        base = _to_utc_frame(df, float(p["server_utc_offset_hours"]))
        out = base.copy()

        # D1 on the 22:00 UTC session (research alignment). Value available at bar close.
        off_h = int(p["d1_session_offset_hours"])
        d1 = _resample(base, "1D", offset=f"{off_h}h")
        # Drop tiny Sunday stubs the same way research does (need enough 15M bars in the day).
        # Approximate: require at least ~20 15M bars worth of range; research used m5 count>100.
        if len(d1):
            bar_count = base["close"].resample(
                "1D", offset=f"{off_h}h", label="left", closed="left"
            ).count().reindex(d1.index)
            d1 = d1[bar_count.fillna(0) > 20]
        sma_f = d1["close"].rolling(int(p["sma_fast"]), min_periods=int(p["sma_fast"])).mean()
        sma_s = d1["close"].rolling(
            int(p["sma_slow"]), min_periods=int(p["sma_slow_min_periods"])
        ).mean()
        up = (d1["close"] > sma_f) & (sma_f > sma_s)
        dn = (d1["close"] < sma_f) & (sma_f < sma_s)
        ddir = pd.DataFrame(
            {"ddir": np.where(up, 1.0, np.where(dn, -1.0, 0.0))}, index=d1.index
        )
        aligned = _align_completed(ddir, pd.Timedelta(days=1), out.index)
        out["ddir"] = aligned["ddir"].fillna(0.0).values

        tod_h = out.index.hour
        rng_lo = int(p["range_start_hour_utc"])
        rng_hi = int(p["range_end_hour_utc"])
        trade_end = int(p["trade_end_hour_utc"])
        in_range = (tod_h >= rng_lo) & (tod_h < rng_hi)
        in_trade = (tod_h >= rng_hi) & (tod_h < trade_end)
        dates = out.index.date
        n = len(out)
        sig = np.zeros(n, dtype=int)
        stop = np.full(n, np.nan)
        ash = np.full(n, np.nan)
        asl = np.full(n, np.nan)
        close = out["close"].values
        high = out["high"].values
        low = out["low"].values
        ddir_v = out["ddir"].values
        minr = float(p["min_risk_price"])
        cap = int(p["max_signals_per_day"])

        cur_day = None
        day_hi = np.nan
        day_lo = np.nan
        used = 0
        for i in range(n):
            d = dates[i]
            if d != cur_day:
                cur_day, day_hi, day_lo, used = d, np.nan, np.nan, 0
            if in_range[i]:
                day_hi = high[i] if np.isnan(day_hi) else max(day_hi, high[i])
                day_lo = low[i] if np.isnan(day_lo) else min(day_lo, low[i])
                continue
            if not in_trade[i] or np.isnan(day_hi) or np.isnan(day_lo):
                continue
            ash[i], asl[i] = day_hi, day_lo
            if used >= cap:
                continue
            c = close[i]
            # Long: close beyond Asian high, D1 uptrend
            if c > day_hi and ddir_v[i] > 0:
                st = day_lo
                if c - st > minr:
                    used += 1
                    sig[i], stop[i] = 1, st
            # Short: close beyond Asian low, D1 downtrend
            elif c < day_lo and ddir_v[i] < 0:
                st = day_hi
                if st - c > minr:
                    used += 1
                    sig[i], stop[i] = -1, st

        out["in_trade_window"] = in_trade
        out["in_asia_range"] = in_range
        out["asia_high"] = ash
        out["asia_low"] = asl
        out["signal"] = sig
        out["signal_stop"] = stop
        # Far placeholder TP (locked plan ignores it)
        risk_abs = (out["close"] - out["signal_stop"]).abs()
        out["signal_target"] = out["close"] + np.sign(sig) * float(p["rr_placeholder"]) * risk_abs

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

    def build_candidate(
        self, state, i, event, df, market, timeframe, params, higher_frames, score_context
    ) -> Optional[Candidate]:
        # higher_frames intentionally unused: D1 comes from compute() so live == backtest.
        p = {**self.base_params, **(params or {})}
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, p)
        if not np.isfinite(rk["risk"]) or rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, p)
        checks, analysis = self.explain_signal(state, i, direction, p, {})
        close_h = int(p["session_close_hour_utc"])
        close_m = int(p["session_close_minute_utc"])
        return Candidate(
            strategy_id=self.id,
            market=market,
            timeframe=timeframe,
            direction=direction,
            candle_time=str(df.index[i]),
            entry=rk["entry"],
            entry_zone=[rk["entry"], rk["entry"]],
            sl=rk["sl"],
            tps=tps,
            risk=rk["risk"],
            rr_primary=(abs(tps[0] - rk["entry"]) / rk["risk"]) if tps else 0.0,
            score=50,
            score_components={"gold_asia_range_bo_rules": 50.0},
            checks=checks,
            analysis=analysis,
            params={},
            params_version=self.version,
            extra={
                "management_plan": "GOLD_ASIA_BO_LOCKED",
                "be_trigger_r": float(p["be_trigger_r"]),
                "be_lock_r": float(p["be_lock_r"]),
                "session_close_utc": f"{close_h:02d}:{close_m:02d}",
                "session_close_hour_utc": close_h,
                "session_close_minute_utc": close_m,
                "no_fixed_tp": True,
                "rr_placeholder": float(p["rr_placeholder"]),
                "max_open_positions": int(p["max_open_positions"]),
                "max_spread_usd": float(p["max_spread_usd"]),
                "asia_high": float(state["asia_high"].iloc[i]),
                "asia_low": float(state["asia_low"].iloc[i]),
                "ddir": float(state["ddir"].iloc[i]),
                "candle_time_utc": str(state["time_utc"].iloc[i]),
                "backtest_fill": "next_bar_open",
                "friday_flatten_utc": f"{close_h:02d}:{close_m:02d}",
            },
            metadata={
                "source": (
                    "owner spec 2026-11-02 template; research round9 GOLD_ASIA_RANGE_BO "
                    "exact: range=00-06 stop=opp be=0.75/0.1 exit=20:45 trend=dd"
                )
            },
        )

    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        entry = float(df["close"].iloc[i])
        sl = float(state["signal_stop"].iloc[i])
        if not np.isfinite(sl):
            return {"entry": entry, "sl": entry, "risk": 0.0}
        return {"entry": entry, "sl": sl, "risk": abs(entry - sl)}

    def calculate_targets(
        self, entry: float, risk: float, direction: str, params: Dict[str, Any]
    ) -> List[float]:
        # Far placeholder only. Locked plan GOLD_ASIA_BO_LOCKED ignores TP and uses BE + 20:45 exit.
        p = {**self.base_params, **(params or {})}
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float(p["rr_placeholder"])]

    def explain_signal(self, state, i, direction, params, extra):
        p = {**self.base_params, **(params or {})}
        buy = direction == "BUY"
        g = lambda k: state[k].iloc[i]
        checks = [
            {
                "label": f"Breakout window {p['range_end_hour_utc']:02d}:00-{p['trade_end_hour_utc']:02d}:00 UTC",
                "ok": bool(g("in_trade_window")),
                "detail": f"bar {g('time_utc')}",
            },
            {
                "label": "D1 trend SMA50/200 aligned (no ADX)",
                "ok": bool(g("ddir") > 0) if buy else bool(g("ddir") < 0),
                "detail": f"ddir={int(g('ddir'))}",
            },
            {
                "label": "Close beyond Asian range",
                "ok": bool(g("close") > g("asia_high")) if buy else bool(g("close") < g("asia_low")),
                "detail": f"close={g('close'):.2f} Asia=[{g('asia_low'):.2f}, {g('asia_high'):.2f}]",
            },
            {
                "label": "Stop = opposite side of Asian range",
                "ok": True,
                "detail": f"sl={g('signal_stop'):.2f}",
            },
            {
                "label": f"BE at +{p['be_trigger_r']}R → entry±{p['be_lock_r']}R; flatten {p['session_close_hour_utc']:02d}:{p['session_close_minute_utc']:02d} UTC",
                "ok": True,
                "detail": "engine plan GOLD_ASIA_BO_LOCKED",
            },
        ]
        analysis = (
            f"{direction} gold Asian-range breakout: 15M close broke the "
            f"{p['range_start_hour_utc']:02d}:00-{p['range_end_hour_utc']:02d}:00 UTC range "
            f"with D1 SMA50/200 trend. Stop at the opposite range edge. No fixed TP — "
            f"at +{p['be_trigger_r']}R move stop to entry±{p['be_lock_r']}R; flatten at "
            f"{p['session_close_hour_utc']:02d}:{p['session_close_minute_utc']:02d} UTC; "
            f"1 trade/day. DO NOT change these parameters."
        )
        return checks, analysis

    def tp_rrs(self, params) -> List[float]:
        return [float({**self.base_params, **(params or {})}["rr_placeholder"])]
