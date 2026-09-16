"""Signal engine (SPEC §6, §7, §14, §15, §55).

Scans markets through the MarketDataProvider, runs every ACTIVE strategy
module, applies agent guards (anti-overtrading, sessions, min R:R) and creates
fully-explained signal records. If there is no valid setup: "No qualifying
setup. Capital protected." is a valid outcome - the goal NEVER forces a trade.
"""
from __future__ import annotations

import pandas as pd
from typing import Any, Dict, List, Optional

from ..config import session_of, settings
from ..db.store import get_store
from ..learning.versions import active_params, active_version
from ..notifications.service import notify
from ..strategies import all_strategies, get_strategy


def volatility_regime(rank: float) -> str:
    if pd.isna(rank):
        return "unknown"
    if rank < 0.33:
        return "low"
    if rank > 0.75:
        return "high"
    return "normal"


class SignalEngine:
    def __init__(self, provider):
        self.provider = provider

    # ------------------------------------------------------------------
    def scan(self, user_id: str, market: str, timeframe: str,
             log_activity: bool = True) -> List[Dict[str, Any]]:
        store = get_store()
        created: List[Dict[str, Any]] = []
        df = self.provider.get_candles(market, timeframe, limit=1600)
        if df is None or len(df) < 300:
            # ALWAYS visible - silent data starvation masked a cache bug before
            self._log(f"Market data unavailable for {market} {timeframe} "
                      f"({0 if df is None else len(df)} bars). Signal generation paused.",
                      kind="DATA", market=market)
            return []
        # Data-gap protection (VPS-readiness): an unresolved hole in the
        # recent candle series invalidates EMA/crossover continuity. No
        # candles are fabricated and no signal is generated - the gap is
        # logged and backfill resolves it. Weekend/holiday spans are
        # market closure, not gaps (integrity handles that).
        try:
            from ..market_data.integrity import series_is_trustworthy
            check = series_is_trustworthy([int(pd.Timestamp(x).timestamp())
                                           for x in df.index], timeframe, 300)
            if not check["ok"] and check["reason"] in ("data_gap", "malformed_series"):
                self._log(f"Data integrity: {check['reason']} on {market} {timeframe} "
                          f"({check['detail']}). Signal generation paused - "
                          "waiting for clean candles.",
                          kind="DATA", market=market)
                return []
        except Exception:
            pass  # integrity is a safety ADD-ON; never break scanning on its failure
        higher = self.provider.higher_frames(market, timeframe)
        session = session_of(pd.Timestamp(df.index[-1]).hour)

        for sid, strategy in all_strategies().items():
            sdoc = store.list("strategies", filters={"id": sid}, limit=1)
            if sdoc and sdoc[0].get("status") != "ACTIVE":
                continue
            params = active_params(sid)
            candidate = strategy.detect_signal(
                df, market, timeframe, params=params, higher_frames=higher,
                score_context={"session": session})
            if candidate is None:
                continue

            ok, reason = self._guards(user_id, candidate, session)
            if not ok:
                self._log(f"Candidate on {market} {timeframe} rejected: {reason}",
                          kind="REJECTED", market=market)
                continue

            sig = self._create_signal(user_id, strategy, candidate, session, df)
            if sig:
                created.append(sig)
        return created

    # ------------------------------------------------------------------
    def _guards(self, user_id: str, candidate, session: str) -> tuple:
        """Risk controls are for signal filtering and guidance (SPEC §35, §55)."""
        store = get_store()
        risk_cfg = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
        risk = risk_cfg[0] if risk_cfg else {
            "allowed_markets": ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY"],
            "sessions": ["London", "NewYork", "Asian", "Late"],
            "max_signals_per_day": 6, "min_rr": 1.5, "max_daily_loss_pct": 3.0,
        }
        if candidate.market not in risk.get("allowed_markets", candidate.market):
            return False, "market not in allowed list"
        if session not in risk.get("sessions", ["London", "NewYork", "Asian", "Late"]):
            return False, f"{session} session not selected"
        today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        todays = store.count("signals", filters={"userId": user_id, "day": today})
        if todays >= int(risk.get("max_signals_per_day", 6)):
            self._log("Daily signal cap reached. Capital protected.", kind="RISK")
            return False, "daily signal cap reached"
        if candidate.rr_primary < float(risk.get("min_rr", 1.5)) and \
                candidate.strategy_id == "strategy_1_zero_lag":
            return False, f"R:R 1:{candidate.rr_primary:.1f} below minimum"
        # daily loss limit
        goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
        if goals:
            completed = store.list("signals", filters={"userId": user_id, "day": today,
                                                       "completed": True}, limit=200)
            daily_r = sum(s.get("r_multiple", 0.0) for s in completed)
            risk_pct = float(risk.get("risk_per_trade_pct", 1.0))
            daily_pl_pct = daily_r * risk_pct
            if daily_pl_pct <= -abs(float(risk.get("max_daily_loss_pct", 3.0))):
                self._log("Maximum daily loss reached. Capital protected - no new "
                          "signals today.", kind="RISK")
                return False, "max daily loss reached"
        return True, ""

    # ------------------------------------------------------------------
    def _create_signal(self, user_id, strategy, cand, session, df) -> Optional[dict]:
        store = get_store()
        dedupe = store.list("signals", filters={
            "strategy_id": cand.strategy_id, "market": cand.market,
            "timeframe": cand.timeframe, "candle_time": cand.candle_time}, limit=1)
        if dedupe:
            return None

        day = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        seq = store.count("signals", filters={"day": day}) + 1
        signal_id = f"SIG-{day.replace('-', '')}-{seq:03d}"

        try:
            vr_series = strategy._volatility_rank(
                strategy.compute(df, cand.params))
            vr = float(vr_series.iloc[-1]) if len(vr_series) else 0.5
        except Exception:
            vr = 0.5

        tps = cand.tps + [None] * (3 - len(cand.tps))
        doc = store.create("signals", {
            "userId": user_id,
            "signal_id": signal_id,
            "day": day,
            "strategy_id": cand.strategy_id,
            "strategy_name": strategy.short_name,
            "strategy_version": active_version(cand.strategy_id),
            "market": cand.market,
            "timeframe": cand.timeframe,
            "direction": cand.direction,
            "entry": cand.entry,
            "entry_zone": cand.entry_zone,
            "sl": cand.sl,
            "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
            "risk": cand.risk,
            "rr_primary": cand.rr_primary,
            "score": cand.score,
            "score_components": cand.score_components,
            "reason": cand.analysis,
            "checks": cand.checks,
            "mtf": cand.mtf,
            "market_conditions": {
                "session": session,
                "volatility_regime": volatility_regime(vr),
                "price": cand.entry,
            },
            "candle_time": cand.candle_time,
            "dna": None,                # Signal DNA snapshot (filled right below)
            "forensics": None,          # filled automatically on completion
            "adaptive": None,           # Adaptive Quality (filled right below)
            "status": "ACTIVE",
            "tp_hits": 0,
            "r_multiple": 0.0,
            "outcome": None,
            "completed": False,
            "user_action": None,        # entered | skipped | None
        })

        score_word = "SETUP QUALIFIED"
        notify(user_id, "NEW_SIGNAL",
               f"{'🟢' if cand.direction == 'BUY' else '🔴'} {cand.market} {cand.direction}",
               f"{strategy.short_name}\n{score_word} - Signal score {cand.score}/100.\n"
               f"Entry {cand.entry:,.5g} - SL {cand.sl:,.5g}. Tap to view analysis.",
               signal_id=doc["id"])
        self._log(f"{strategy.short_name}: {cand.direction} signal on {cand.market} "
                  f"{cand.timeframe} approved for notification.",
                  kind="SIGNAL", market=cand.market)
        try:  # Signal DNA: full market snapshot at the exact signal moment
            from ..learning.dna import build as build_dna
            store.update("signals", doc["id"],
                         {"dna": build_dna(cand.market, cand.timeframe, df, cand.mtf)})
        except Exception:
            pass
        try:  # Adaptive Quality: evidence-based evaluation of this signal
            fresh = store.get("signals", doc["id"])
            if fresh:
                from ..learning.adaptive import evaluate_and_store
                evaluate_and_store(store, fresh)
        except Exception:
            pass
        self._log(f"User notified - {signal_id}.", kind="NOTIFY", market=cand.market)
        try:  # MT5 auto-execution (VPS bridge) - never blocks signal creation
            from ..execution.mt5 import execute_signal
            execute_signal(doc, user_id)
        except Exception:
            pass
        return store.get("signals", doc["id"])

    # ------------------------------------------------------------------
    def _log(self, message: str, kind: str = "INFO", market: Optional[str] = None):
        store = get_store()
        store.create("agent_activity", {
            "userId": None, "kind": kind, "message": message, "market": market,
        })

    def analyze_manual(self, user_id: str, market: str, timeframe: str) -> dict:
        """POST /signals/analyze - honest result even when nothing qualifies."""
        store = get_store()
        df = self.provider.get_candles(market, timeframe, limit=1600)
        if df is None or len(df) < 300:
            return {"qualified": False,
                    "message": "Market data unavailable. Signal generation paused."}
        session = session_of(pd.Timestamp(df.index[-1]).hour)
        higher = self.provider.higher_frames(market, timeframe)
        results = []
        for sid, strategy in all_strategies().items():
            sdoc = store.list("strategies", filters={"id": sid}, limit=1)
            if sdoc and sdoc[0].get("status") != "ACTIVE":
                continue
            cand = strategy.detect_signal(df, market, timeframe,
                                          params=active_params(sid),
                                          higher_frames=higher,
                                          score_context={"session": session})
            results.append({
                "strategy_id": sid, "strategy_name": strategy.short_name,
                "qualified": cand is not None,
                "candidate": cand.__dict__ if cand else None,
            })
        if not any(r["qualified"] for r in results):
            return {"qualified": False,
                    "message": "No qualifying setup. Capital protected.", "strategies": results}
        return {"qualified": True, "strategies": results}
