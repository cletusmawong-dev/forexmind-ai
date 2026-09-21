"""Strategy 2 - MTF Sweep -> BOS -> Retest (spec sections 1-41, 2026-09-20).

Independent, self-contained strategy translated from the supplied Pine
indicator "MTF Sweep -> BOS -> Retest". It plugs into the EXISTING pipeline
only: registry -> SignalEngine scan -> guards/walls -> signal doc -> notify ->
risk/execution -> MT5 -> AI trade manager (post-entry only, never an entry
filter). Strategy 1 modules are untouched.

State machine (spec section 4):
    NO SWEEP -> SWEEP DETECTED -> WAITING FOR BOS -> BOS CONFIRMED ->
    WAITING FOR RETEST -> RETEST CONFIRMED -> SIGNAL

Closed candles only (spec sections 7/32); pivots confirmed by right-side bars;
exact timeframe mapping (section 6); ATR = Wilder RMA (Pine ta.atr) on the
entry timeframe (section 14). Entries/SL/TPs carry NO extra filters.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ...core.indicators import atr
from ..base import BaseStrategy, Candidate
from . import machine, state as state_store

SESSION_WEIGHT = {"London": 15.0, "NewYork": 15.0, "Asian": 8.0, "Late": 10.0}


def _log_event(kind: str, msg: str, market: str = "") -> None:
    """Event-based logging (spec section 29) - only called on transitions."""
    try:
        from ...db.store import get_store
        get_store().create("agent_activity", {
            "userId": None, "kind": kind, "message": msg[:280], "market": market})
    except Exception:
        pass


class MtfSweepBosRetestStrategy(BaseStrategy):
    id = "strategy_2_mtf_sweep_bos_retest"
    name = "Strategy 2 - MTF Sweep -> BOS -> Retest"
    short_name = "MTF Sweep -> BOS -> Retest"
    description = ("Higher-timeframe liquidity sweep, break of structure on the "
                   "mapped BOS timeframe, entry on the retest. Pure state "
                   "machine, closed candles only, ATR take-profit ladder.")
    version = "1.0"
    base_params = {
        "swing_length": 3,          # spec section 5 (swingLen)
        "atr_length": 14,           # spec section 5/14
        "tp1_atr": 1.0,
        "tp2_atr": 2.0,
        "tp3_atr": 3.0,
        "show_levels": True,        # display-only in Pine; never affects logic
        "show_labels": True,        # display-only in Pine; never affects logic
        "expire_bars": 200,         # backtest bookkeeping only (base model)
    }
    experiment_variables = {
        "swing_length": {"type": "int", "min": 2, "max": 10, "step": 1,
                         "description": "Pivot swing length (both sides)"},
        "atr_length": {"type": "int", "min": 5, "max": 50, "step": 1,
                       "description": "ATR period (entry timeframe)"},
        "tp1_atr": {"type": "float", "min": 0.5, "max": 5.0, "step": 0.25,
                    "description": "TP1 ATR multiple"},
        "tp2_atr": {"type": "float", "min": 0.5, "max": 6.0, "step": 0.25,
                    "description": "TP2 ATR multiple"},
        "tp3_atr": {"type": "float", "min": 0.5, "max": 8.0, "step": 0.25,
                    "description": "TP3 ATR multiple"},
    }
    markets = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["5M", "15M", "30M", "1H", "4H"]   # all mapped entry TFs
    min_bars = 200

    # ------------------------------------------------------------------
    # BaseStrategy contract (entry-TF view; entries happen cross-TF in
    # detect_signal, so detect_on_bar is deliberately event-less here)
    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"atr": atr(df, int(params["atr_length"]))}

    def detect_on_bar(self, state, i: int) -> Optional[Dict[str, Any]]:
        return None   # entries only via the cross-timeframe machine

    def build_candidate(self, state, i, event, df, market, timeframe, params,
                        higher_frames, score_context) -> Candidate:
        """Base-class contract. The cross-TF machine in detect_signal() builds
        candidates directly (it needs the sweep/BOS frames); this assembly is
        provided for interface completeness and generic tooling."""
        close = float(df["close"].iloc[i])
        a = float(state["atr"].iloc[i])
        sign = 1.0 if event["direction"] == "BUY" else -1.0
        tps = [close + sign * a * float(params[k]) for k in ("tp1_atr", "tp2_atr", "tp3_atr")]
        return Candidate(
            strategy_id=self.id, market=market, timeframe=timeframe,
            direction=event["direction"], candle_time=str(df.index[i]),
            entry=close, entry_zone=[close, close], sl=close, tps=tps,
            risk=a, rr_primary=abs(tps[2] - close) / a if a else 0.0,
            score=60, score_components={"setup_complete": 60.0},
            checks=[], analysis="Generic assembly - see detect_signal.",
            params=dict(params))

    # ------------------------------------------------------------------
    # The real detection: 3-timeframe state machine on closed candles
    # ------------------------------------------------------------------
    def detect_signal(self, df: pd.DataFrame, market: str, timeframe: str,
                      params: Optional[Dict[str, Any]] = None,
                      higher_frames: Optional[Dict[str, pd.DataFrame]] = None,
                      score_context: Optional[Dict[str, Any]] = None) -> Optional[Candidate]:
        params = self.get_parameters(params)
        if timeframe not in machine.TIMEFRAME_MAP:
            return None
        sweep_tf, bos_tf = machine.TIMEFRAME_MAP[timeframe]
        frames = higher_frames or {}
        entry_df, sweep_df, bos_df = df, frames.get(sweep_tf), frames.get(bos_tf)

        # -- data integrity (spec section 25): never trade on bad data
        for name, frame, minb in (("entry", entry_df, machine._MIN_BARS["entry"]),
                                  ("sweep", sweep_df, machine._MIN_BARS["sweep"]),
                                  ("bos", bos_df, machine._MIN_BARS["bos"])):
            err = machine.integrity_check(frame, name, minb)
            if err:
                _log_event("DATA_INTEGRITY_FAILURE",
                           f"S2 {market} {timeframe}: {err} - no signal will be generated.",
                           market)
                return None

        st = state_store.load(market, timeframe)

        events: List[str] = []

        # 1) sweep event - once per closed sweep candle
        sweep_ts = int(sweep_df.index[-1].timestamp())
        if st.get("last_sweep_ts") != sweep_ts:
            sw = machine.detect_sweep(sweep_df)
            if sw == "bull":
                st["bullish_setup"] = True; st["bearish_setup"] = False
                st["last_sweep_ts"] = sweep_ts
                events.append("SWEEP_DETECTED: bullish sweep on " + sweep_tf)
            elif sw == "bear":
                st["bearish_setup"] = True; st["bullish_setup"] = False
                st["last_sweep_ts"] = sweep_ts
                events.append("SWEEP_DETECTED: bearish sweep on " + sweep_tf)
            else:
                st["last_sweep_ts"] = sweep_ts   # candle processed, no sweep

        # 2) BOS event - once per closed BOS-TF candle
        bos_ts = int(bos_df.index[-1].timestamp())
        candidate = None
        if st.get("last_bos_ts") != bos_ts:
            st["last_bos_ts"] = bos_ts
            for direction in ("bull", "bear"):
                fired, st = machine.detect_bos(bos_df, st, int(params["swing_length"]), direction)
                if fired:
                    events.append(f"BOS_CONFIRMED: {direction} BOS on {bos_tf} - waiting for retest")
                    break

        # 3) retest event - once per closed entry candle (one evaluation per bar)
        retest_ts = int(entry_df.index[-1].timestamp())
        if st.get("last_retest_ts") != retest_ts:
            hit = machine.check_retest(entry_df, st)
            st["last_retest_ts"] = retest_ts
            if hit:
                a = float(atr(entry_df, int(params["atr_length"])).iloc[-1])
                if pd.isna(a) or a <= 0:
                    _log_event("DATA_INTEGRITY_FAILURE",
                               f"S2 {market} {timeframe}: ATR unavailable at retest - no signal.", market)
                else:
                    sign = 1.0 if hit["direction"] == "BUY" else -1.0
                    setup_key = (f"{market}|{timeframe}|{hit['direction']}|"
                                 f"{st.get('last_sweep_ts')}|{st.get('last_bos_ts')}|{retest_ts}")
                    if st.get("last_signal_key") == setup_key:
                        events.append("DUPLICATE_SIGNAL_BLOCKED (same retest evaluated twice)")
                    else:
                        st["last_signal_key"] = setup_key
                        st["waiting_bull_retest"] = False
                        st["waiting_bear_retest"] = False
                        # Pine state machine: SIGNAL loops back to NO SWEEP -
                        # a FRESH sweep is required before the next setup.
                        # (Without this the same sweep re-armed via a new BOS
                        # and fired again 15 min later - seen live 2026-09-21
                        # SIG-006 -> SIG-007.)
                        st["bullish_setup"] = False
                        st["bearish_setup"] = False
                        st["broken_high"] = None
                        st["broken_low"] = None
                        st["structure_high"] = None
                        st["structure_low"] = None
                        st["bos_candle_low"] = None
                        st["bos_candle_high"] = None
                        tps = [hit["entry"] + sign * a * float(params[k])
                               for k in ("tp1_atr", "tp2_atr", "tp3_atr")]
                        risk = abs(hit["entry"] - hit["sl"])
                        if risk <= 0:
                            _log_event("SIGNAL_REJECTED",
                                       f"S2 {market}: zero-risk retest (entry == SL) rejected.", market)
                        else:
                            session = (score_context or {}).get("session")
                            bonus = SESSION_WEIGHT.get(session, 5.0)
                            candidate = Candidate(
                                strategy_id=self.id,
                                market=market,
                                timeframe=timeframe,
                                direction=hit["direction"],
                                candle_time=str(entry_df.index[-1]),
                                entry=hit["entry"],
                                entry_zone=[hit["entry"], hit["entry"]],
                                sl=hit["sl"],
                                tps=tps,
                                risk=risk,
                                rr_primary=abs(tps[2] - hit["entry"]) / risk,
                                score=int(min(100.0, 60.0 + bonus)),
                                score_components={"setup_complete": 60.0, "session": bonus},
                                checks=[
                                    {"label": "Sweep on " + sweep_tf, "ok": True, "detail": "liquidity sweep confirmed (closed candle)"},
                                    {"label": "BOS on " + bos_tf, "ok": True, "detail": "structure break confirmed (closed candle)"},
                                    {"label": "Retest on " + timeframe, "ok": True, "detail": "level retested and rejected (closed candle)"},
                                ],
                                analysis=(f"{hit['direction']}: {sweep_tf} sweep -> {bos_tf} BOS -> "
                                          f"{timeframe} retest. Entry {hit['entry']:,.5g}, "
                                          f"SL {hit['sl']:,.5g} (BOS candle), "
                                          f"TP1 {tps[0]:,.5g} / TP2 {tps[1]:,.5g} / TP3 {tps[2]:,.5g} "
                                          f"(ATR x {params['tp1_atr']}/{params['tp2_atr']}/{params['tp3_atr']})."),
                                mtf=None,
                                params=dict(params),
                                extra={
                                    "setup_state": "SWEEP_BOS_RETEST",
                                    "setup_stage": "SIGNAL",
                                    "entry_timeframe": timeframe,
                                    "sweep_timeframe": sweep_tf,
                                    "bos_timeframe": bos_tf,
                                    "setup_key": setup_key,
                                },
                            )
                            events.append(f"SIGNAL_CREATED: {hit['direction']} retest confirmed")
        else:
            events.append("WAITING_FOR_RETEST (no new entry candle)")

        state_store.save(market, timeframe, st)
        for e in events:
            if e.startswith("WAITING_FOR_RETEST"):
                continue    # repeat evaluation of the same candle - not a transition
            _log_event(e.split(":")[0].split(" ")[0], f"S2 {market} {timeframe}: {e}", market)
        return candidate

    # ------------------------------------------------------------------
    # risk & targets (spec sections 12-14): SL = BOS candle, TPs = ATR x mult
    # ------------------------------------------------------------------
    def calculate_risk(self, df, state, i, direction, params) -> Dict[str, float]:
        close = float(df["close"].iloc[i])
        a = float(state["atr"].iloc[i])
        return {"entry": close, "sl": close, "risk": a,
                "entry_zone": [close, close]}

    def tp_rrs(self, params) -> List[float]:
        return [float(params["tp1_atr"]), float(params["tp2_atr"]), float(params["tp3_atr"])]

    def calculate_targets(self, entry, risk, direction, params) -> List[float]:
        sign = 1.0 if direction == "BUY" else -1.0
        return [entry + sign * float(risk) * float(params[k])
                for k in ("tp1_atr", "tp2_atr", "tp3_atr")]

    def explain_signal(self, state, i, direction, params, extra) -> tuple:
        checks = [
            {"label": "HTF sweep", "ok": True, "detail": extra.get("sweep_timeframe", "sweep TF")},
            {"label": "BOS", "ok": True, "detail": extra.get("bos_timeframe", "BOS TF")},
            {"label": "Retest entry", "ok": True, "detail": extra.get("entry_timeframe", "entry TF")},
        ]
        return checks, "Sweep -> BOS -> retest state machine completed on closed candles."

    # ------------------------------------------------------------------
    # Deterministic replay (spec section 31): HTF frames are resampled from
    # the same closed entry-TF frame (documented approximation of Pine
    # request.security, which on TradingView would use the symbol's own HTF
    # series - identical OHLCV by construction for FX/gold sessions).
    # ------------------------------------------------------------------
    def backtest(self, df: pd.DataFrame, params: Optional[Dict[str, Any]] = None,
                 max_hold_bars: int = 200) -> List[Dict[str, Any]]:
        from ...core.indicators import resample_ohlcv, TF_RULES
        params = self.get_parameters(params)
        if df is None or len(df) < self.min_bars:
            return []
        df = df.dropna()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        if self.id != self.id:
            return []
        tf = "15M"   # replay runs on the 15M mapping (4H sweep / 1H BOS)
        sweep_tf, bos_tf = machine.TIMEFRAME_MAP[tf]
        sweep_df = resample_ohlcv(df, TF_RULES[sweep_tf])
        bos_df = resample_ohlcv(df, TF_RULES[bos_tf])
        if len(sweep_df) < 40 or len(bos_df) < 40:
            return []

        st = machine.default_state()
        trades: List[Dict[str, Any]] = []
        open_pos: Optional[Dict[str, Any]] = None
        h = df["high"].to_numpy(); l = df["low"].to_numpy(); t = df.index
        a_series = atr(df, int(params["atr_length"]))
        sw_ts = [int(x.timestamp()) for x in sweep_df.index]
        bo_ts = [int(x.timestamp()) for x in bos_df.index]
        sw_td = {"1H": 3600, "4H": 14400, "1D": 86400}[sweep_tf]
        bo_td = {"15M": 900, "1H": 3600, "4H": 14400}[bos_tf]

        def closed_slice(ts_list, td, i):
            # candles fully closed by entry bar i's CLOSE time
            bar_close = int(t[i].timestamp()) + 900
            hi = 0
            for j, s in enumerate(ts_list):
                if s + td <= bar_close:
                    hi = j + 1
            return hi

        for i in range(60, len(df)):
            if open_pos is not None:
                res = self._advance_position(open_pos, i, h[i], l[i], t[i], params, max_hold_bars)
                if res is not None:
                    trades.append(res)
                    open_pos = None
                continue
            e_df = df.iloc[:i + 1]
            s_df = sweep_df.iloc[:max(2, closed_slice(sw_ts, sw_td, i))]
            b_df = bos_df.iloc[:max(2, closed_slice(bo_ts, bo_td, i))]

            sweep_ts = int(s_df.index[-1].timestamp())
            if st.get("last_sweep_ts") != sweep_ts:
                st["last_sweep_ts"] = sweep_ts
                sw = machine.detect_sweep(s_df)
                if sw == "bull":
                    st["bullish_setup"] = True; st["bearish_setup"] = False
                elif sw == "bear":
                    st["bearish_setup"] = True; st["bullish_setup"] = False
            bos_ts = int(b_df.index[-1].timestamp())
            if st.get("last_bos_ts") != bos_ts:
                st["last_bos_ts"] = bos_ts
                for direction in ("bull", "bear"):
                    fired, st = machine.detect_bos(b_df, st, int(params["swing_length"]), direction)
                    if fired:
                        break
            retest_ts = int(t[i].timestamp())
            if st.get("last_retest_ts") != retest_ts:
                st["last_retest_ts"] = retest_ts
                hit = machine.check_retest(e_df, st)
                if hit:
                    a = float(a_series.iloc[i])
                    if pd.isna(a) or a <= 0:
                        continue
                    sign = 1.0 if hit["direction"] == "BUY" else -1.0
                    key = f"{hit['direction']}|{st.get('last_sweep_ts')}|{st.get('last_bos_ts')}|{retest_ts}"
                    if st.get("last_signal_key") == key:
                        continue
                    st["last_signal_key"] = key
                    st["waiting_bull_retest"] = False
                    st["waiting_bear_retest"] = False
                    tps = [hit["entry"] + sign * a * float(params[k])
                           for k in ("tp1_atr", "tp2_atr", "tp3_atr")]
                    risk = abs(hit["entry"] - hit["sl"])
                    if risk <= 0:
                        continue
                    open_pos = {
                        "direction": hit["direction"], "entry_i": i,
                        "entry": hit["entry"], "sl": hit["sl"], "tps": tps,
                        "risk": risk, "entry_time": str(t[i]),
                        "tp_rrs": [abs(tp - hit["entry"]) / risk for tp in tps],
                    }
        return trades
