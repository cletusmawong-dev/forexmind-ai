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
    alias_id = "strategy_2_liquidity_structure"   # master-spec stable ID (history kept under the original id)
    name = "Strategy 2 - Liquidity Sweep + Structure Retest"
    short_name = "Liquidity Sweep + Structure Retest"
    description = ("Liquidity sweep of a confirmed swing level, break of "
                   "structure with displacement, entry on the retest. Pure "
                   "deterministic state machine, closed candles only, "
                   "SL beyond the swept level, TP ladder 1R/2R/3R.")
    version = "2.0.0"
    base_params = {
        "swing_length": 3,          # master spec section 6
        "atr_length": 14,           # master spec section 10
        "sl_buffer_atr": 0.25,      # SL safety buffer beyond the swept level (section 12)
        "tp1_r": 1.0,               # TP ladder in R (section 13)
        "tp2_r": 2.0,
        "tp3_r": 3.0,
        "max_setup_bars": 20,       # setup expiration, entry-TF bars (section 9)
        "tp1_atr": 1.0,             # legacy v1 knobs kept for param-version compat
        "tp2_atr": 2.0,
        "tp3_atr": 3.0,
        "show_levels": True,        # display-only; never affects logic
        "show_labels": True,        # display-only; never affects logic
        "expire_bars": 200,         # backtest bookkeeping only (base model)
    }
    experiment_variables = {
        "swing_length": {"type": "int", "min": 2, "max": 10, "step": 1,
                         "description": "Swing length for liquidity levels (both sides)"},
        "atr_length": {"type": "int", "min": 5, "max": 50, "step": 1,
                       "description": "ATR period (entry timeframe)"},
        "sl_buffer_atr": {"type": "float", "min": 0.0, "max": 2.0, "step": 0.05,
                          "description": "SL buffer beyond the swept level, in ATR"},
        "tp1_r": {"type": "float", "min": 0.5, "max": 5.0, "step": 0.25,
                  "description": "TP1 in R (risk = entry to SL)"},
        "tp2_r": {"type": "float", "min": 0.5, "max": 6.0, "step": 0.25,
                  "description": "TP2 in R"},
        "tp3_r": {"type": "float", "min": 0.5, "max": 8.0, "step": 0.25,
                  "description": "TP3 in R"},
        "max_setup_bars": {"type": "int", "min": 5, "max": 100, "step": 5,
                           "description": "Setup expiration (entry-TF bars without retest)"},
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
            sw = machine.detect_sweep(sweep_df, int(params["swing_length"]))
            if sw:
                direction_key, swept_level, sweep_price = sw
                st["bullish_setup"] = direction_key == "bull"
                st["bearish_setup"] = direction_key == "bear"
                st["last_sweep_ts"] = sweep_ts
                st["swept_level"] = swept_level
                st["sweep_price"] = sweep_price
                events.append(f"SWEEP_DETECTED: {'bullish' if direction_key == 'bull' else 'bearish'} "
                              f"sweep of {swept_level:,.5g} on " + sweep_tf)
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
        if (st.get("waiting_bull_retest") or st.get("waiting_bear_retest")) \
                and st.get("last_bos_ts"):
            try:
                setup_age = entry_df.index[-1] - pd.Timestamp(
                    float(st["last_bos_ts"]), unit="s", tz="UTC")
                if setup_age > pd.Timedelta(
                        minutes=15 * int(params.get("max_setup_bars", 20))):
                    st["waiting_bull_retest"] = False
                    st["waiting_bear_retest"] = False
                    st["bullish_setup"] = False
                    st["bearish_setup"] = False
                    st["swept_level"] = None
                    st["sweep_price"] = None
                    events.append("SETUP_EXPIRED: no retest in time - back to NO_SETUP")
            except Exception:
                pass
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
                        # Master spec section 12: SL beyond the SWEPT level
                        # (+ configurable ATR buffer). ATR fallback when the
                        # swept level is unusable (missing / inverted risk).
                        swept = st.get("swept_level")
                        buf = float(params.get("sl_buffer_atr", 0.25))
                        sl = (swept - sign * buf * a) if swept is not None else None
                        if sl is None or (sign > 0 and sl >= hit["entry"]) \
                                or (sign < 0 and sl <= hit["entry"]):
                            sl = hit["entry"] - sign * 1.5 * a     # ATR fallback
                        tps = [hit["entry"] + sign * abs(hit["entry"] - sl)
                               * float(params.get(f"tp{i}_r", float(i)))
                               for i in (1, 2, 3)]
                        hit["sl"] = float(sl)
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
                                analysis=(f"{hit['direction']}: {sweep_tf} liquidity sweep -> {bos_tf} "
                                          f"structure break -> {timeframe} retest. "
                                          f"Entry {hit['entry']:,.5g}, SL {hit['sl']:,.5g} "
                                          f"(beyond swept level), "
                                          f"TP1 {tps[0]:,.5g} / TP2 {tps[1]:,.5g} / TP3 {tps[2]:,.5g} "
                                          f"(1R/2R/3R)."),
                                mtf=None,
                                params=dict(params),
                                extra={
                                    "setup_state": "SWEEP_BOS_RETEST",
                                    "setup_stage": "SIGNAL",
                                    "signal_state": "RETEST_CONFIRMED",
                                    "strategy_version": self.version,
                                    "entry_timeframe": timeframe,
                                    "sweep_timeframe": sweep_tf,
                                    "bos_timeframe": bos_tf,
                                    "setup_key": setup_key,
                                    "liquidity_level": st.get("swept_level"),
                                    "liquidity_type": ("sell_side" if hit["direction"] == "BUY"
                                                       else "buy_side"),
                                    "sweep_price": st.get("sweep_price"),
                                    "sweep_timestamp": st.get("last_sweep_ts"),
                                    "structure_level": (st.get("broken_high")
                                                        if hit["direction"] == "BUY"
                                                        else st.get("broken_low")),
                                    "structure_direction": ("bullish" if hit["direction"] == "BUY"
                                                            else "bearish"),
                                    "structure_break_timestamp": st.get("last_bos_ts"),
                                    "retest_level": (st.get("broken_high")
                                                     if hit["direction"] == "BUY"
                                                     else st.get("broken_low")),
                                    "retest_timestamp": retest_ts,
                                    "session": (score_context or {}).get("session"),
                                    "atr": a,
                                    "market_context": {"tf": timeframe, "market": market},
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
    # risk & targets (master spec sections 12-14): SL beyond swept level, TP in R
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
