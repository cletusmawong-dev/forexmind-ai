"""Strategy 1 - Volume Profile + Pivot Levels [ChartPrime] (MTF).

Source-of-truth translation of the supplied Pine Script v6
("Volume Profile + Pivot Levels [ChartPrime]", (c) ChartPrime, MPL 2.0),
replacing the retired AlgoAlpha Zero Lag strategy (user directive
2026-09-30: "use the exact strategy I'm giving - don't change anything").

The Pine source is a chart OVERLAY: it draws a volume profile, the PoC and
volume-confirmed pivot levels - it defines NO entries. The user selected the
entry triggers explicitly (2026-09-30) and they are implemented verbatim as
three selectable modes (strategy setting `entry_mode`, default
`hvn_rejection` - the user's pick):

  breakout       BUY  close crosses above an active pivot-HIGH level
                 SELL close crosses below an active pivot-LOW level
  poc_bounce     BUY  bar spans the PoC and closes bullish above it
                 SELL bar spans the PoC and closes bearish below it
  hvn_rejection  BUY  bar wicks below an active pivot-LOW level and closes
                      back above it
                 SELL bar wicks above an active pivot-HIGH level and closes
                      back below it

Translated EXACTLY (defaults = the source's inputs):

  - profile window: the last `profile_period` (200) bars; H/L from highs+ lows
  - bin accumulation: volume[j] joins bin i when
        close[j] >= bin_low - bin_size  and  close[j] < bin_high + bin_size
    (the +/-bin_size widening is the source's own condition - kept verbatim)
  - PoC = the highest-volume bin's mid; on ties the LAST (highest) max bin
    wins, matching the source's re-draw loop (poc := line.new per matching bin)
  - pivots: ta.pivothigh/pivotlow(pivot_length, pivot_length) confirmed
    pivot_length bars later; left-side ties allowed, right side strict
    (community-verified TradingView semantics)
  - a pivot becomes a LEVEL when a profile bin mid sits within bin_size of it
    AND that bin's volPercent >= pivot_filter, AND its age is at most
    profile_period - 54 bars (the source's label-offset rule: labels may not
    overlap the profile drawing, which reaches 50 + 4 bars into the window -
    kept verbatim because it governs which levels exist)
  - a level DIES when any bar AFTER the pivot bar and BEFORE the evaluation
    bar SPANS it (high > level and low < level) - the source's removal loop.
    A bar entirely above or below the level does NOT kill it. The SIGNAL
    bar's own span does not pre-empt its trigger (the level must have
    survived up to the previous bar): a rejection bar by definition spans
    its own level, so counting it would make the user-chosen rejection
    entry impossible.
  - delta (buy/sell pressure): the source adds +-volume (the CURRENT bar's
    volume, not volume[j] - a ChartPrime quirk kept verbatim; it only biases
    the score, never entries)

Everything is evaluated CAUSALLY: bar i only sees bars <= i. The TP/SL is
inherited from the 9/21 EMA strategy unchanged (user directive 2026-09-30:
"it does not have tp so we need to use the tp and sl of the ema"):
SL = ATR(14) x 1.5, TP ladder 1R / 2R / 3R.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ...core.indicators import atr
from ..base import BaseStrategy, Candidate

ENTRY_MODES = ("hvn_rejection", "breakout", "poc_bounce")
TREND_LABEL = {1: "Bullish", -1: "Bearish", 0: "Neutral"}


class VPPivotsStrategy(BaseStrategy):
    id = "strategy_1_vp_pivots"
    name = "Volume Profile + Pivot Levels [ChartPrime]"
    short_name = "VP + Pivots"
    description = (
        "Volume-profile PoC with volume-confirmed pivot support/resistance "
        "levels (ChartPrime, exact translation). Three user-selectable entry "
        "modes: high-volume-node rejection (default), level breakout, PoC "
        "bounce. SL/TP inherited from the 9/21 EMA strategy: "
        "SL = ATR(14) x 1.5, TP 1R / 2R / 3R.")
    version = "1.0"
    base_params = {
        "profile_period": 200,        # Pine input "Period" (maxval 500)
        "profile_bins": 50,           # Pine input "Volume Profile Resolution"
        "pivot_length": 10,           # Pine input "Length"
        "pivot_filter": 20.0,         # Pine input "Filter % 0-100"
        "entry_mode": "hvn_rejection",
        "atr_len": 14,                # inherited SL/TP (EMA strategy)
        "sl_mult": 1.5,
        "tp1_rr": 1.0,
        "tp2_rr": 2.0,
        "tp3_rr": 3.0,
        "expire_bars": 200,
    }
    experiment_variables = {
        "entry_mode":    {"type": "select", "options": list(ENTRY_MODES),
                          "description": "Entry trigger: hvn_rejection, breakout or poc_bounce"},
        "profile_period": {"type": "int",   "min": 50,  "max": 500, "step": 10,
                           "description": "Volume profile period (bars)"},
        "profile_bins":  {"type": "int",   "min": 10,  "max": 100, "step": 5,
                          "description": "Volume profile resolution (bins)"},
        "pivot_length":  {"type": "int",   "min": 3,   "max": 50,  "step": 1,
                          "description": "Pivot high/low length"},
        "pivot_filter":  {"type": "float", "min": 0.0, "max": 100.0, "step": 5.0,
                          "description": "Minimum bin volume % for a pivot level"},
        "atr_len":       {"type": "int",   "min": 5,   "max": 50,  "step": 1,
                          "description": "ATR period (SL, inherited from EMA strategy)"},
        "sl_mult":       {"type": "float", "min": 0.5, "max": 5.0,  "step": 0.1,
                          "description": "SL ATR multiplier (inherited from EMA strategy)"},
        "tp1_rr":        {"type": "float", "min": 0.5, "max": 6.0,  "step": 0.25,
                          "description": "TP1 risk-reward"},
        "tp2_rr":        {"type": "float", "min": 0.5, "max": 6.0,  "step": 0.25,
                          "description": "TP2 risk-reward"},
        "tp3_rr":        {"type": "float", "min": 0.5, "max": 6.0,  "step": 0.25,
                          "description": "TP3 risk-reward"},
    }
    markets = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["5M", "15M", "1H", "4H", "1D"]
    min_bars = 260

    # ------------------------------------------------------------- profile
    def _profile_at(self, df: pd.DataFrame, i: int, params: Dict[str, Any]) -> Optional[dict]:
        """Volume profile over the `profile_period` bars ending at i (inclusive).

        Causal: uses bars i-start+1 .. i only. Returns None (honestly, no
        synthetic fallback) when the frame carries no usable volume - e.g. a
        feed fallback without tick volume - so the strategy simply does not
        fire rather than guessing (never silently fail)."""
        start = int(params["profile_period"]); bins = int(params["profile_bins"])
        if i + 1 < start:
            return None
        w = df.iloc[i - start + 1: i + 1]
        H = float(w["high"].max()); Lw = float(w["low"].min())
        if not (H > Lw):
            return None                      # perfectly flat window -> no profile
        bs = (H - Lw) / bins
        if "volume" not in w.columns:
            return None
        vol = w["volume"].to_numpy(dtype=float)
        if not np.isfinite(vol).all() or float(vol.sum()) <= 0:
            return None                      # feed without volume: no entry, loudly
        closes = w["close"].to_numpy(dtype=float)
        opens = w["open"].to_numpy(dtype=float)

        # bin b covers [L + bs*b, L + bs*(b+1)); the source widens each bin's
        # capture window to [blo - bs, bhi + bs) -> exactly the k in
        # {b-1, b, b+1} of floor((close - L) / bs). k == bins (close == H)
        # joins the top bin, matching the source's strict `< bhi + bs` on the
        # last bin. Implemented as an exact histogram convolution - no loops.
        k = np.floor((closes - Lw) / bs).astype(np.int64) + 1   # shift -> hist idx 1..bins+1
        k = np.clip(k, 0, bins + 1)
        hist = np.bincount(k, weights=vol, minlength=bins + 3)
        acc = np.convolve(hist, np.ones(3), mode="same")[1: bins + 1]
        mx = float(acc.max()) if len(acc) else 0.0
        if mx <= 0:
            return None
        poc_bin = int(np.where(acc == mx)[0][-1])            # LAST max bin (source re-draw)
        poc = Lw + bs * poc_bin + bs / 2.0
        # delta: source adds +-volume (the CURRENT bar's volume, not volume[j])
        # per window bar - a ChartPrime quirk kept verbatim (score bias only).
        v_last = float(vol[-1])
        bull = int(np.count_nonzero(closes > opens))
        delta = v_last * (2 * bull - start)
        return {"H": H, "L": Lw, "bs": bs, "bins": bins, "acc": acc, "max": mx,
                "poc": poc, "poc_bin": poc_bin, "delta": delta,
                "total_vol": float(vol.sum())}

    def _bin_strength(self, prof: dict, value: float,
                      params: Dict[str, Any]) -> Optional[float]:
        """volPercent (0-100) of the strongest bin whose mid sits within
        bin_size of `value` (source: |mid - val| <= bin_size), or None when no
        matching bin clears pivot_filter."""
        bs = prof["bs"]; acc = prof["acc"]; mx = prof["max"]
        filt = float(params["pivot_filter"])
        best: Optional[float] = None
        for b in range(prof["bins"]):
            mid = prof["L"] + bs * b + bs / 2.0
            if abs(mid - value) <= bs:
                vp = float(acc[b]) / mx * 100.0
                if vp >= filt and (best is None or vp > best):
                    best = vp
        return best

    # ------------------------------------------------------------- pivots
    def _pivots(self, df: pd.DataFrame, L: int) -> List[Tuple[int, float, int, bool]]:
        """ta.pivothigh/pivotlow(L, L) translation.

        Returns (confirm_idx, value, pivot_idx, is_high). At confirmation bar
        c the pivot bar p = c - L must beat the L bars before it (ties with
        OLDER bars allowed) and strictly beat the L bars after it (ties with
        NEWER bars disqualify) - community-verified TradingView semantics."""
        hi = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        out: List[Tuple[int, float, int, bool]] = []
        for c in range(2 * L, len(df)):
            p = c - L
            v = hi[p]; ok = True
            for j in range(p - L, p):
                if hi[j] > v:
                    ok = False; break
            if ok:
                for j in range(p + 1, c + 1):
                    if hi[j] >= v:
                        ok = False; break
            if ok:
                out.append((c, float(v), p, True))
            v = lo[p]; ok = True
            for j in range(p - L, p):
                if lo[j] < v:
                    ok = False; break
            if ok:
                for j in range(p + 1, c + 1):
                    if lo[j] <= v:
                        ok = False; break
            if ok:
                out.append((c, float(v), p, False))
        return out

    def _active_levels(self, df: pd.DataFrame, state: dict, i: int,
                       prof: dict, params: Dict[str, Any]) -> List[dict]:
        """Volume-confirmed pivot levels alive at bar i (causal).

        Source rules, verbatim:
          - pivots pruned once p.index <= bar_index - start (outside window)
          - label-offset rule: p.index >= bar_index - start + 54 (profile
            drawing reaches 50 + 4 bars into the window)
          - bin-matching: |bin mid - pivot| <= bin_size AND volPercent >= filter
          - removal: any bar between the pivot and the evaluation bar SPANS
            the level (high > level and low < level). Bars fully above/below
            don't kill it; the evaluation bar's own span is excluded - see
            the module docstring (a rejection bar spans its level by
            definition)."""
        start = int(params["profile_period"])
        hi = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        out: List[dict] = []
        for confirm, value, pidx, is_high in state["pivots"]:
            if confirm > i:
                continue                    # not yet confirmed at bar i
            if pidx <= i - start:
                continue                    # pruned (outside the window)
            age = i - pidx
            if age > start - 54:
                continue                    # source label-offset rule
            strength = self._bin_strength(prof, value, params)
            if strength is None:
                continue                    # not a high-volume node
            engulfed = False
            for j in range(pidx + 1, i):     # signal bar's own span excluded (see docstring)
                if hi[j] > value and lo[j] < value:
                    engulfed = True; break
            if engulfed:
                continue                    # level crossed out (source removal)
            out.append({"level": float(value), "is_high": bool(is_high),
                        "strength": float(strength), "pidx": int(pidx)})
        return out

    @staticmethod
    def _pick(levels: List[dict], close: float) -> Optional[dict]:
        """Deterministic trigger choice: strongest node first, then nearest."""
        if not levels:
            return None
        return sorted(levels, key=lambda l: (-l["strength"],
                                             abs(close - l["level"]),
                                             l["level"]))[0]

    # ------------------------------------------------------------- compute
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        L = int(params["pivot_length"])
        return {
            "df": df,
            "params": params,
            "atr": atr(df, int(params["atr_len"])),
            "pivots": self._pivots(df, L),
        }

    def detect_on_bar(self, state: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        if i < 2:
            return None
        df = state["df"]; params = state["params"]
        start = int(params["profile_period"]); L = int(params["pivot_length"])
        if i < max(start, 2 * L + 1):
            return None
        prof = self._profile_at(df, i, params)
        if prof is None:
            return None                     # no volume / flat window -> no entry
        mode = str(params.get("entry_mode", "hvn_rejection"))
        o = float(df["open"].iloc[i]); c = float(df["close"].iloc[i])
        h = float(df["high"].iloc[i]); l = float(df["low"].iloc[i])
        cp = float(df["close"].iloc[i - 1])

        if mode == "poc_bounce":
            poc = prof["poc"]
            if l <= poc <= h:
                if c > poc and c > o:
                    return {"direction": "BUY", "i": i,
                            "trigger": {"kind": "poc_bounce", "level": poc,
                                        "strength": 100.0}}
                if c < poc and c < o:
                    return {"direction": "SELL", "i": i,
                            "trigger": {"kind": "poc_bounce", "level": poc,
                                        "strength": 100.0}}
            return None

        levels = self._active_levels(df, state, i, prof, params)
        if mode == "breakout":
            ups = [x for x in levels if x["is_high"] and c > x["level"] and cp <= x["level"]]
            dns = [x for x in levels if not x["is_high"] and c < x["level"] and cp >= x["level"]]
            if ups:
                t = self._pick(ups, c)
                return {"direction": "BUY", "i": i,
                        "trigger": {"kind": "breakout", **t}}
            if dns:
                t = self._pick(dns, c)
                return {"direction": "SELL", "i": i,
                        "trigger": {"kind": "breakout", **t}}
            return None

        # hvn_rejection (default): wick through a volume node, close back inside
        buys = [x for x in levels if not x["is_high"] and l < x["level"] < c]
        sells = [x for x in levels if x["is_high"] and h > x["level"] > c]
        if buys:
            t = self._pick(buys, c)
            return {"direction": "BUY", "i": i,
                    "trigger": {"kind": "hvn_rejection", **t}}
        if sells:
            t = self._pick(sells, c)
            return {"direction": "SELL", "i": i,
                    "trigger": {"kind": "hvn_rejection", **t}}
        return None

    # ------------------------------------------------------- risk & targets
    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        """Inherited UNCHANGED from the 9/21 EMA strategy (user directive):
        risk = ATR(atr_len) x sl_mult; SL = entry -/+ risk."""
        close = float(df["close"].iloc[i])
        risk = float(state["atr"].iloc[i]) * float(params["sl_mult"])
        if pd.isna(risk) or risk <= 0:
            return {"entry": close, "sl": close, "risk": 0.0,
                    "entry_zone": [close, close]}
        sl = close - risk if direction == "BUY" else close + risk
        return {"entry": close, "sl": sl, "risk": risk,
                "entry_zone": [close - 0.03 * risk, close + 0.03 * risk]}

    def tp_rrs(self, params) -> List[float]:
        return [float(params["tp1_rr"]), float(params["tp2_rr"]), float(params["tp3_rr"])]

    def calculate_targets(self, entry, risk, direction, params) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * risk * float(params[k]) for k in ("tp1_rr", "tp2_rr", "tp3_rr")]

    # ------------------------------------------------------------------ MTF
    def mtf_trend(self, higher_frames: Dict[str, pd.DataFrame],
                  params: Dict[str, Any]) -> Dict[str, int]:
        """Per higher timeframe: price above the frame's PoC = bullish (+1),
        below = bearish (-1), unavailable/flat = 0 (honestly excluded)."""
        out: Dict[str, int] = {}
        for tf, df in (higher_frames or {}).items():
            try:
                if df is None or len(df.dropna()) < max(int(params["profile_period"]),
                                                        self.min_bars // 2):
                    out[tf] = 0
                    continue
                d = df.dropna()
                prof = self._profile_at(d, len(d) - 1, params)
                if prof is None:
                    out[tf] = 0
                    continue
                px = float(d["close"].iloc[-1])
                out[tf] = 1 if px > prof["poc"] else (-1 if px < prof["poc"] else 0)
            except Exception:
                out[tf] = 0
        return out

    # ----------------------------------------------------------- candidate
    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Optional[Candidate]:
        direction = event["direction"]
        rk = self.calculate_risk(df, state, i, direction, params)
        if rk["risk"] <= 0:
            return None
        tps = self.calculate_targets(rk["entry"], rk["risk"], direction, params)
        rr_primary = float(params["tp3_rr"])
        mtf = self.mtf_trend(higher_frames, params)
        prof = self._profile_at(df, i, params) or {}
        trigger = event.get("trigger") or {}
        checks, analysis = self.explain_signal(
            state, i, direction, params,
            {"mtf": mtf, "rr_primary": rr_primary, "risk": rk["risk"],
             "trigger": trigger, "prof": prof})
        ctx = dict(score_context or {})
        ctx.setdefault("close", float(df["close"].iloc[i]))
        ctx["trigger"] = trigger
        ctx["prof"] = prof
        ctx["mtf"] = mtf
        score, comps = self.score(state, i, direction, ctx)
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=direction, candle_time=str(df.index[i]),
            entry=rk["entry"], entry_zone=rk.get("entry_zone", [rk["entry"], rk["entry"]]),
            sl=rk["sl"], tps=tps, risk=rk["risk"], rr_primary=rr_primary,
            score=score, score_components=comps, checks=checks, analysis=analysis,
            mtf=mtf, params=dict(params), params_version=self.version,
        )

    def score(self, state, i, direction, ctx) -> tuple:
        """Confidence ranking ONLY - never moves entry/SL/TP (SPEC).

        40 pts: node strength (volPercent of the triggering level / PoC)
        20 pts: delta alignment (buy/sell pressure across the window)
        20 pts: risk/reward (full at 1:3 to TP3)
        20 pts: higher-timeframe PoC bias agreement."""
        sgn = 1 if direction == "BUY" else -1
        trig = ctx.get("trigger") or {}
        c1 = round(40.0 * min(max(float(trig.get("strength", 0.0)), 0.0), 100.0) / 100.0, 1)

        prof = ctx.get("prof") or {}
        c2 = 10.0
        if prof:
            tv = float(prof.get("total_vol") or 0.0)
            if tv > 0:
                ratio = float(prof.get("delta") or 0.0) / tv     # [-1, 1]
                c2 = round(max(0.0, min(20.0, 10.0 + 10.0 * sgn * ratio)), 1)

        rr = float(ctx.get("rr_primary") or 0.0) or 3.0
        c3 = round(min(rr / 3.0, 1.0) * 20.0, 1)

        mtf = ctx.get("mtf") or {}
        n = len(mtf)
        if n:
            agree = sum(1 for v in mtf.values() if v == sgn)
            neutral = sum(1 for v in mtf.values() if v == 0)
            c4 = round(20.0 * (agree + 0.5 * neutral) / n, 1)
        else:
            c4 = 0.0

        total = int(round(min(100.0, c1 + c2 + c3 + c4)))
        return total, {"Node strength": c1, "Delta alignment": c2,
                       "Risk/Reward": c3, "HTF PoC bias": c4}

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        sgn = 1 if direction == "BUY" else -1
        trig = (extra or {}).get("trigger") or {}
        prof = (extra or {}).get("prof") or {}
        mtf = (extra or {}).get("mtf") or {}
        rr = float((extra or {}).get("rr_primary") or 0)
        sm = float(params["sl_mult"]); al = int(params["atr_len"])
        kind = trig.get("kind", "hvn_rejection")
        lvl = float(trig.get("level", 0.0)); vp = float(trig.get("strength", 0.0))
        mode_name = {"hvn_rejection": "High-volume-node rejection",
                     "breakout": "Level breakout",
                     "poc_bounce": "PoC bounce"}[kind]
        if kind == "poc_bounce":
            det = (f"bar spanned the PoC at {lvl:.5g} and closed "
                   f"{'bullish above' if sgn > 0 else 'bearish below'} it")
            ok = True
        elif kind == "breakout":
            det = (f"close crossed {'above' if sgn > 0 else 'below'} the pivot "
                   f"{'resistance' if sgn > 0 else 'support'} level {lvl:.5g} "
                   f"(node strength {vp:.0f}% of max)")
            ok = True
        else:
            det = (f"bar wicked {'below' if sgn > 0 else 'above'} the volume-"
                   f"confirmed pivot {'support' if sgn > 0 else 'resistance'} "
                   f"level {lvl:.5g} (node strength {vp:.0f}% of max) and closed "
                   f"back {'above' if sgn > 0 else 'below'} it")
            ok = True
        agree = sum(1 for v in (mtf or {}).values() if v == sgn)
        checks = [
            {"label": f"{mode_name} confirmed", "ok": ok, "detail": det},
            {"label": "Volume profile context",
             "ok": bool(prof),
             "detail": (f"PoC {float(prof['poc']):.5g} over the last "
                        f"{int(params['profile_period'])} bars, "
                        f"{int(params['profile_bins'])} bins"
                        if prof else "volume profile unavailable") },
            {"label": "Higher-timeframe PoC bias",
             "ok": agree >= 2 if mtf else False,
             "detail": " / ".join(f"{tf} {TREND_LABEL.get(mtf.get(tf, 0), 'N/A')[:4]}"
                                  for tf in ["5M", "15M", "1H", "4H", "1D"]) if mtf else "n/a"},
            {"label": "Risk/reward acceptable",
             "ok": rr >= 2.0,
             "detail": f"1 : {rr:.1f} to TP3; SL = {sm:g} x ATR({al}) "
                       f"(inherited from the 9/21 EMA strategy)"},
        ]
        dir_word = "bullish" if sgn > 0 else "bearish"
        analysis = (
            f"The {dir_word} entry fired from the ChartPrime volume profile: "
            f"{det}. Price is trading around the point of control with "
            f"{agree} of {len(mtf) if mtf else 5} higher timeframes biased "
            f"{dir_word} (price vs PoC). Stop and targets follow the 9/21 EMA "
            f"strategy's ATR model.")
        return checks, analysis
