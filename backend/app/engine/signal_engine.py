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

SESSION_NAMES = ("Asian", "London", "NewYork", "Late")

# Walls that turn signals into EXTRA SIGNALS (recorded + notified, never
# auto-entered - SS22/SS26). The prop MAX-total-drawdown wall is deliberately
# NOT in this set: a blown account stops signal generation entirely.
EXTRA_WALL_REASONS = {"daily profit target reached", "daily loss limit reached",
                      "max daily loss reached", "prop daily drawdown buffer reached"}
DEFAULT_SESSION_HOURS = {"Asian": (0, 8), "London": (8, 13),
                         "NewYork": (13, 21), "Late": (21, 24)}
_UTC_LIKE = ("", "UTC", "GMT", "ETC/UTC", "UTC+0")


def session_windows(risk: dict) -> dict:
    """Built-in UTC windows overridden by the user's session_hours."""
    out = dict(DEFAULT_SESSION_HOURS)
    for k, v in (risk.get("session_hours") or {}).items():
        if k in out and isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = (int(v[0]), int(v[1]))
    return out


def effective_session_name(risk: dict, utc_ts) -> Optional[str]:
    """Session name for utc_ts under the user's session customization.

    Returns None when the user has NO customization (caller keeps the
    server-computed default). Returns '' when the instant falls in a gap
    between configured windows - an honest 'no session', never invented."""
    tz_name = str(risk.get("session_tz") or "UTC").strip()
    if not (risk.get("session_hours") or {}) and tz_name.upper() in _UTC_LIKE:
        return None
    ts = pd.Timestamp(utc_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    if tz_name.upper() not in _UTC_LIKE:
        try:
            from zoneinfo import ZoneInfo
            ts = ts.tz_convert(ZoneInfo(tz_name))
        except Exception:
            pass  # tz was validated at the API; eval-time failure falls back to UTC
    h = int(ts.hour)
    for name in SESSION_NAMES:
        a, b = session_windows(risk)[name]
        if a <= h < b:
            return name
    return ""
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
                if reason in EXTRA_WALL_REASONS:
                    # SS22/SS23: still a REAL strategy signal - record it as an
                    # EXTRA SIGNAL and notify; automatic entry stays disabled
                    # (the executor refuses it independently - belt & braces).
                    sig = self._create_signal(user_id, strategy, candidate,
                                              session, df, wall_reason=reason)
                    if sig:
                        created.append(sig)
                    continue
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
        # per-user session customization (tz / custom windows): recompute for
        # this candle; None = user has no customization, keep server default
        eff = effective_session_name(risk, getattr(candidate, "candle_time", None))
        if eff is not None:
            session = eff
        # SS31 matrix: a market entry REPLACES the global list; missing = global
        matrix = risk.get("market_sessions") or {}
        row = matrix.get(candidate.market)
        # NOTE: None-check, not `or` - an EMPTY row is an explicit per-market
        # pause and must NOT fall back to the global session list
        allowed_sessions = row if row is not None else \
            risk.get("sessions", ["London", "NewYork", "Asian", "Late"])
        if session == "" or session not in allowed_sessions:
            if candidate.market in matrix:
                return False, f"{session or 'No'} session not enabled for {candidate.market} (per-market rule)"
            return False, f"{session} session not selected"
        today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        todays = store.count("signals", filters={"userId": user_id, "day": today})
        if todays >= int(risk.get("max_signals_per_day", 6)):
            self._log("Daily signal cap reached. Capital protected.", kind="RISK")
            return False, "daily signal cap reached"
        if candidate.rr_primary < float(risk.get("min_rr", 1.5)) and \
                candidate.strategy_id == "strategy_1_zero_lag":
            return False, f"R:R 1:{candidate.rr_primary:.1f} below minimum"
        # daily loss limit - prop-firm aware (user request 2026-09-17)
        acct_type = str(risk.get("account_type", "personal") or "personal").lower()
        prop = risk.get("prop_rules") or {}
        personal_limit = abs(float(risk.get("max_daily_loss_pct", 3.0)))
        if acct_type == "propfirm":
            dd = abs(float(prop.get("daily_drawdown_pct", 5.0) or 5.0))
            buf = min(max(float(prop.get("daily_dd_buffer_pct", 20.0) or 0.0), 0.0), 50.0)
            # the tighter of: the user's personal daily limit, the prop daily DD
            # with its safety buffer applied (stop before breaching the rule)
            eff_daily_limit = min(personal_limit, dd * (1.0 - buf / 100.0))
        else:
            eff_daily_limit = personal_limit

        goals = store.list("agent_goals", filters={"userId": user_id}, limit=1)
        if goals:
            completed = store.list("signals", filters={"userId": user_id, "day": today,
                                                       "completed": True}, limit=200)
            daily_r = sum(s.get("r_multiple", 0.0) for s in completed)
            risk_pct = float(risk.get("risk_per_trade_pct", 1.0))
            daily_pl_pct = daily_r * risk_pct
            if daily_pl_pct <= -eff_daily_limit:
                if acct_type == "propfirm":
                    self._log("Prop daily drawdown protection: today's result is at "
                              "the safety buffer of the daily limit. No new signals "
                              "today - the account rule stays intact.", kind="RISK")
                    return False, "prop daily drawdown buffer reached"
                self._log("Maximum daily loss reached. Capital protected - no new "
                          "signals today.", kind="RISK")
                return False, "max daily loss reached"
            if acct_type == "propfirm":
                tdd = abs(float(prop.get("max_total_drawdown_pct", 10.0) or 10.0))
                all_completed = store.list("signals", filters={"userId": user_id,
                                                               "completed": True},
                                           limit=1000)
                total_pl_pct = sum(s.get("r_multiple", 0.0) for s in all_completed) * risk_pct
                buf = min(max(float(prop.get("daily_dd_buffer_pct", 20.0) or 0.0), 0.0), 50.0)
                if total_pl_pct <= -tdd * (1.0 - buf / 100.0):
                    self._log("Prop maximum drawdown protection reached. Signal "
                              "generation stopped - review the account phase.",
                              kind="RISK")
                    return False, "prop max drawdown buffer reached"
        # account-level USD walls (SS21-SS26) - computed from real records
        try:
            from .daily import daily_state, wall_reason
            st = daily_state(user_id, risk=risk)
            wr = wall_reason(st, risk)
            if wr:
                if "profit target" in wr:
                    self._log(f"Daily profit target reached (+${st['total_usd']:,.2f}). "
                              "Signals continue as EXTRA SIGNALS - automatic entry "
                              "disabled.", kind="RISK")
                else:
                    self._log(f"Daily loss limit reached (${st['total_usd']:,.2f}). "
                              "Signals continue as EXTRA SIGNALS - automatic entry "
                              "disabled.", kind="RISK")
                return False, wr
        except Exception:
            pass  # wall accounting must never break signal generation
        return True, ""

    # ------------------------------------------------------------------
    def _create_signal(self, user_id, strategy, cand, session, df,
                       wall_reason: str = "") -> Optional[dict]:
        store = get_store()
        # BUGFIX (2026-09-19): dedupe must be PER USER. The filter used to omit
        # userId, so once ANY user received a signal for a candle, every other
        # user's scan was silently deduped - with one shared engine loop the
        # first-scanned user got everything and the others got nothing (the
        # owner's 'only gold signals' report). Same guard, scoped to the user.
        dedupe = store.list("signals", filters={
            "userId": user_id, "strategy_id": cand.strategy_id, "market": cand.market,
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
        if wall_reason:
            try:
                from .daily import daily_state
                st = daily_state(user_id)
                store.update("signals", doc["id"], {
                    "extra_signal": True,
                    "entry_blocked_reason": wall_reason,
                    "daily_pl_at_signal": st.get("total_usd"),
                    "execution_status": "EXTRA_SIGNAL_NOT_ENTERED",
                })
                wall_line = ("Daily profit target has already been reached."
                             if "profit target" in wall_reason else
                             "Daily loss limit reached.")
                tgt = st.get("daily_profit_target_usd") or 0
                lim = st.get("daily_loss_limit_usd") or 0
                tps_txt = " - ".join(
                    f"TP{i} {sig_tp:,.5g}" for i, sig_tp in
                    enumerate((tps[0], tps[1], tps[2]), start=1) if sig_tp)
                notify(user_id, "EXTRA_SIGNAL",
                       f"EXTRA SIGNAL - {cand.market} {cand.direction}",
                       f"{strategy.short_name} | {signal_id}\n"
                       f"{wall_line}\n"
                       f"Target: ${tgt:,.0f}" + (f" | Limit: -${lim:,.0f}" if lim else "") +
                       f" | Today's P/L: {st.get('total_usd', 0):+,.2f}\n"
                       f"Automatic entry: DISABLED\n"
                       f"Entry {cand.entry:,.5g} - SL {cand.sl:,.5g} - {tps_txt}",
                       signal_id=doc["id"])
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
