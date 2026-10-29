"""AI Trade Manager engine (SS10, SS13, SS32-SS35, SS5-restart).

Event-driven post-entry management. Per user per tick (60s floor):

    discover positions (broker = source of truth)
      -> reconcile state (restart-safe: rebuild from live data, never re-enter)
      -> evaluate triggers (event-driven; NOT per tick AI calls)
      -> review triggered positions (context -> ModelRouter -> decision JSON)
      -> risk gate -> Phase-3 primitives -> audit record

Triggers (SS13): position opened, TP approach/hit (see tp ladder), momentum
flip, volatility spike, news window, large floating-P/L jump, daily-wall
approach, max-age since last review. Cooldowns prevent AI spam (SS13/SS7).

The manager NEVER touches entry code paths: it only reads positions and
calls management primitives (modify_sl / partial_close / close).
"""
from __future__ import annotations

import threading
import time

import pandas as pd
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from ..db.store import get_store
from . import decisions, recorder, risk_gate

MIN_REVIEW_INTERVAL_S = 300     # per-position AI cooldown (SS13)
MAX_REVIEW_AGE_S = 900          # max-age trigger (SS13-18)
PL_JUMP_USD = 15.0              # floating P/L jump trigger (SS13-13)
TP_APPROACH_FRACTION = 0.2      # within 20% of the remaining leg to next TP
WALL_APPROACH_FRACTION = 0.2    # account wall proximity trigger (SS13-14/15)


class TradeManager:
    def __init__(self, provider=None, router=None):
        self.provider = provider          # State.provider (the ONE data path)
        self._router = router             # agent.router.ModelRouter (lazy ok)
        self._lock = threading.Lock()
        self._state: Dict[int, Dict[str, Any]] = {}
        self.last_tick_ts = 0.0
        self.last_summary: Dict[str, Any] = {}

    # -- router lazily (avoids import cycles at app boot) -----------------
    @property
    def router(self):
        if self._router is None:
            from ..agent.router import get_router
            self._router = get_router()
        return self._router

    # ==================================================================
    # discovery + linking (broker is the source of truth - SS5)
    # ==================================================================
    def _positions(self, user_id: str) -> List[dict]:
        from ..execution.mt5 import MAGIC, bridge_get, user_mode
        if user_mode(user_id) != "vps":
            return []
        data = bridge_get("/positions", timeout=8) or {}
        out = []
        for p in (data.get("positions") or []):
            if int(p.get("magic") or 0) == self._magic():
                p = dict(p)
                comment = str(p.get("comment") or "").strip()
                p["app_market"] = p.get("symbol")
                p["signal_id"] = comment or None
                out.append(p)
        return out

    @staticmethod
    def _magic() -> int:
        from ..execution.mt5 import MAGIC
        return MAGIC

    def _link_signal(self, user_id: str, position: dict) -> Optional[dict]:
        sig_id = position.get("signal_id")
        if not sig_id:
            return None
        docs = get_store().list("signals", filters={"userId": user_id,
                                                    "signal_id": sig_id}, limit=1)
        return docs[0] if docs else None

    # ==================================================================
    # state + reconciliation (SS5: never blindly duplicate / assume)
    # ==================================================================
    def _state_for(self, position: dict, signal: Optional[dict]) -> Dict[str, Any]:
        ticket = int(position["ticket"])
        st = self._state.get(ticket)
        if st is None:
            # rebuild from LIVE data - after a VPS/backend restart (SS5).
            # tp_state stays 0 (UNKNOWN history): transitions must be OBSERVED,
            # not inferred from a snapshot - otherwise the ladder never sees
            # one. The inferred level is kept separately for reconcile.
            price = float(position.get("price_current") or 0)
            entry = float(position.get("price_open") or 0)
            is_buy = str(position.get("type", "")).upper() == "BUY"
            inferred = 0
            if signal:
                for i in (1, 2, 3):
                    tp = signal.get(f"tp{i}")
                    if tp and price and ((price >= float(tp)) if is_buy
                                         else (price <= float(tp))):
                        inferred = i
            st = {"tp_state": 0, "inferred": inferred, "last_review": 0.0,
                  "last_action": None, "opened": position.get("time"),
                  "entry": entry, "reconciled": True}
            self._state[ticket] = st
        return st

    def reconcile(self, user_id: str) -> int:
        """Post-restart pass: rebuild state from the broker, enforce the TP2
        safety lock if evidence shows it should already be in place."""
        n = 0
        for pos in self._positions(user_id):
            sig = self._link_signal(user_id, pos)
            st = self._state_for(pos, sig)
            n += 1
            # SS5: if TP2 was already surpassed but SL is still looser than
            # TP1, the deterministic lock repairs it WITHOUT any AI call.
            if sig and int(st.get("inferred") or 0) >= 2:
                lock = sig.get("tp1")
                if lock:
                    from .risk_gate import execute_plan
                    res = execute_plan(user_id, pos, {"modify_sl": float(lock)},
                                       reason="TP2 lock repair (reconcile)")
                    if res.get("executed"):
                        recorder.record(user_id, pos, "reconcile", "deterministic",
                                        "local", None, True, "TP2_LOCK_REPAIRED",
                                        execution=res)
        return n

    # ==================================================================
    # triggers (cheap checks only - full evidence built on review)
    # ==================================================================
    def _triggers(self, user_id: str, pos: dict, sig: Optional[dict],
                  st: dict, daily: Optional[dict], now: float) -> List[Tuple[str, bool]]:
        """Returns [(trigger_name, escalate_flag)]. Empty = no review."""
        out: List[Tuple[str, bool]] = []
        ticket = int(pos["ticket"])
        price = float(pos.get("price_current") or 0)
        entry = float(pos.get("price_open") or 0)
        is_buy = str(pos.get("type", "")).upper() == "BUY"

        if ticket not in self._state:
            out.append(("position_opened", False))
        if now - float(st.get("last_review") or 0) >= MAX_REVIEW_AGE_S:
            out.append(("scheduled_review", False))

        # floating P/L jump since last snapshot (SS13-13)
        try:
            prev_pl = float(st.get("last_pl") or 0.0)
            if abs(float(pos.get("profit") or 0) - prev_pl) >= PL_JUMP_USD:
                out.append(("pl_jump", False))
        except Exception:
            pass

        # TP ladder geometry (approach + already-hit detection)
        if sig:
            for i in (1, 2, 3):
                tp = sig.get(f"tp{i}")
                if not tp:
                    continue
                hit = (price >= float(tp)) if is_buy else (price <= float(tp))
                if hit and st.get("tp_state", 0) < i:
                    out.append((f"tp{i}_hit", True))
                    break
                if st.get("tp_state", 0) < i <= (st.get("tp_state", 0) + 1):
                    leg = abs(float(tp) - entry)
                    if leg > 0 and abs(float(tp) - price) <= TP_APPROACH_FRACTION * leg:
                        out.append((f"tp{i}_approach", i >= 2))
                        break

        # account wall approach (SS13-14/15, SS32)
        if daily:
            tgt = float(daily.get("daily_profit_target_usd") or 0)
            lim = float(daily.get("daily_loss_limit_usd") or 0)
            total = float(daily.get("total_usd") or 0)
            if tgt and total >= tgt * (1 - WALL_APPROACH_FRACTION):
                out.append(("daily_target_approach", True))
            if lim and total <= -lim * (1 - WALL_APPROACH_FRACTION):
                out.append(("daily_loss_approach", True))

        # news window for this market (SS13-10)
        try:
            from ..market_data.calendar import is_blackout
            blackout, ev = is_blackout(pos.get("app_market") or pos.get("symbol", ""))
            if blackout and not st.get("news_flagged"):
                out.append(("news_window", True))
        except Exception:
            pass

        # dedupe, cap noise
        seen, uniq = set(), []
        for name, esc in out:
            if name not in seen:
                uniq.append((name, esc))
                seen.add(name)
        return uniq

    # ==================================================================
    # one tick for one user
    # ==================================================================
    def tick(self, user_id: str) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"positions": 0, "reviews": 0, "actions": [],
                                   "skipped": None}
        rdoc = get_store().list("settings", filters={"userId": user_id,
                                                     "kind": "risk"}, limit=1)
        risk = rdoc[0] if rdoc else {}
        if not bool(risk.get("ai_manage_enabled", False)):
            summary["skipped"] = "ai_manage_enabled is off"
            self.last_summary = summary
            return summary

        from ..execution.mt5 import execution_enabled, user_mode
        if not execution_enabled(user_id):
            summary["skipped"] = "kill switch is ON - manager touches nothing"
            self.last_summary = summary
            return summary
        if user_mode(user_id) != "vps":
            summary["skipped"] = "no live position feed (vps mode only)"
            self.last_summary = summary
            return summary

        try:
            from ..engine.daily import daily_state
            daily = daily_state(user_id)
        except Exception:
            daily = None

        now = time.time()
        self.last_tick_ts = now
        _ps_ids = ("strategy_hybrid_crt_tbs", "strategy_fade_crt_ext")
        for pos in self._positions(user_id):
            summary["positions"] += 1
            sig = self._link_signal(user_id, pos)
            st = self._state_for(pos, sig)
            # UNIVERSAL TRADE MANAGER (owner reference impl 2026-10-29):
            # deterministic BE/lock/trail ladder runs EVERY tick - it must
            # never wait for a review trigger or a cooldown. Strategy-owned
            # plans (TrendBreak v2 trail, PINSTRIPE ladder) keep their own
            # verbatim management and are not touched by it.
            if (sig or {}).get("strategy_id") != "strategy_trendbreak_v2" and \
               (sig or {}).get("strategy_id") not in _ps_ids:
                uni = self._universal_manage(user_id, pos, sig, st, now)
                if uni.get("actions"):
                    summary["actions"].extend(uni["actions"])
                if uni.get("closed"):
                    st["last_review"] = now
                    st["last_pl"] = float(pos.get("profit") or 0)
                    continue
            triggers = self._triggers(user_id, pos, sig, st, daily, now)
            if not triggers:
                st["last_pl"] = float(pos.get("profit") or 0)
                continue
            # cooldown: one review per position per window (except hard events)
            hard = any(n in ("tp2_hit", "tp3_hit") for n, _ in triggers)
            if not hard and now - float(st.get("last_review") or 0) < MIN_REVIEW_INTERVAL_S:
                continue
            trigger = "+".join(n for n, _ in triggers)
            escalate = any(e for _, e in triggers)
            res = self._review(user_id, pos, sig, trigger, escalate, daily, now)
            summary["reviews"] += 1
            if res.get("actions"):
                summary["actions"].extend(res["actions"])
        self.last_summary = summary
        return summary

    # ==================================================================
    # the review pipeline for one position
    # ==================================================================
    def _review(self, user_id: str, pos: dict, sig: Optional[dict], trigger: str,
                escalate: bool, daily: Optional[dict], now: float) -> dict:
        st = self._state.get(int(pos["ticket"]), {})
        out: Dict[str, Any] = {"actions": []}

        # 0a) STRATEGY-OWNED DETERMINISTIC MANAGEMENT (TrendBreak v2): this
        #     position's exits belong to the strategy's own rules (owner
        #     Pine 2026-10-24) - a 5xATR trail from the best price since
        #     entry (ONLY ever tightened) and the 240-bar time exit. The
        #     generic TP ladder and position policy MUST NOT touch a
        #     trail-only position: the strategy IS the management plan.
        if (sig or {}).get("strategy_id") == "strategy_trendbreak_v2":
            v2 = self._trendbreak_v2_manage(user_id, pos, sig)
            out["actions"].extend(v2.get("actions", []))
            st["last_review"] = now
            st["last_pl"] = float(pos.get("profit") or 0)
            recorder.record(user_id, pos, trigger, "deterministic", "local",
                            None, True, v2.get("verdict", "TB2_TRAIL"),
                            execution=v2, snapshot_ts=now)
            return out
        _ps_ids = ("strategy_hybrid_crt_tbs", "strategy_fade_crt_ext")
        if (sig or {}).get("strategy_id") in _ps_ids:
            ps = self._pinstripe_manage(user_id, pos, sig)
            out["actions"].extend(ps.get("actions", []))
            st["last_review"] = now
            st["last_pl"] = float(pos.get("profit") or 0)
            recorder.record(user_id, pos, trigger, "deterministic", "local",
                            None, True, ps.get("verdict", "PINSTRIPE_LADDER"),
                            execution=ps, snapshot_ts=now)
            return out

        # 0) TP-ladder deterministic layer FIRST (SS14-16): hard rules run
        #    regardless of AI availability/decisions (implemented in P9).
        #    When the Universal manager owns this position it has ALREADY
        #    applied the hard rules this tick (stage machine in tick()) -
        #    running this ladder too would double-fire the partial.
        ladder: Dict[str, Any] = {"actions": [], "skip_ai": False,
                                  "verdict": "UNIVERSAL_LADDER"}
        if not st.get("universal"):
            ladder = self._tp_ladder(user_id, pos, sig, st, trigger)
        out["actions"].extend(ladder.get("actions", []))
        if ladder.get("skip_ai"):
            st["last_review"] = now
            st["last_pl"] = float(pos.get("profit") or 0)
            recorder.record(user_id, pos, trigger, "deterministic", "local",
                            None, True, ladder.get("verdict", "TP_LADDER"),
                            execution=ladder, snapshot_ts=now)
            return out

        # 0b) DETERMINISTIC POSITION POLICY (owner brief 2026-10-10, s.3):
        #     market food (1H/4H levels) + completed-candle evidence decide
        #     when they can. When the policy has an actionable decision it
        #     IS the decision for this review (recorded as
        #     deterministic_policy) and flows through the SAME risk gate as
        #     everything else. A HOLD from the policy leaves the existing
        #     AI path untouched (existing behaviour preserved). Wicks are
        #     observations; only completed closes + follow-through act.
        policy_decision = None
        try:
            market = pos.get("app_market") or pos.get("symbol", "")
            from ..analysis.market_food import analyze as mf_analyze
            from .position_policy import evaluate as policy_eval
            food = mf_analyze(market, provider=self.provider)
            h1_df = None
            if self.provider is not None:
                h1_df = self.provider.get_candles(market, "1H", limit=400)
            entry_px = float(pos.get("price_open") or 0)
            cur_px = float(pos.get("price_current") or 0)
            is_buy = str(pos.get("type", "")).upper() == "BUY"
            init_sl = (sig or {}).get("sl") or pos.get("sl")
            risk = abs(entry_px - float(init_sl)) if init_sl else 0.0
            r_mult = ((cur_px - entry_px) / risk if is_buy else
                      (entry_px - cur_px) / risk) if risk > 0 else 0.0
            policy_decision = policy_eval(pos=pos, risk=risk,
                                          r_multiple=r_mult, food=food,
                                          h1=h1_df)
            st["last_policy"] = {"code": policy_decision.get("reason_code"),
                                 "detail": policy_decision.get("detail"),
                                 "ts": now}
        except Exception as exc:
            policy_decision = None
            recorder.record(user_id, pos, trigger, "-", "local", None, False,
                            "POLICY_ERROR_HELD", error=str(exc)[:160],
                            snapshot_ts=now)

        if policy_decision and policy_decision.get("action") not in (None, "HOLD"):
            decision = {
                "action": policy_decision["action"],
                "recommended_sl": policy_decision.get("recommended_sl"),
                "partial_fraction": policy_decision.get("partial_fraction"),
                "reasoning": policy_decision.get("detail", ""),
            }
            model_name, layer = "deterministic_policy", "local"
            decision_err = None
            brain_meta = None
            snapshot_ts = time.time()

        # 1) evidence -> AI. Brain 2.0 pipeline when enabled (world model ->
        #    generate -> self-challenge -> decide); on ANY brain error we fall
        #    back to the legacy single-call path and RECORD it (never silent).
        #    Both paths end at the SAME deterministic gate below.
        market = pos.get("app_market") or pos.get("symbol", "")
        model_name, layer = settings.ai_primary_model, "primary"
        decision: Optional[dict] = None
        decision_err: Optional[str] = None
        brain_meta: Optional[dict] = None
        snapshot_ts = time.time()
        if settings.brain_v2_enabled:
            try:
                from ..ai.brain import run_brain
                from ..ai.world_model import build_world_model
                world = build_world_model(user_id, market, position=pos,
                                          signal=sig, daily=daily,
                                          provider=self.provider, now=now)
                brain_meta = run_brain(user_id, world, escalate=escalate,
                                       router=self.router, signal=sig)
                snapshot_ts = float(brain_meta.get("snapshot_ts") or snapshot_ts)
                model_name = brain_meta.get("model", model_name)
                layer = brain_meta.get("layer", layer)
                if brain_meta.get("decision"):
                    decision = brain_meta["decision"]
                else:
                    decision_err = f"brain answer: {brain_meta.get('answer')}"
            except Exception as exc:
                recorder.record(user_id, pos, trigger, "-", "local", None, False,
                                "BRAIN_ERROR_FALLBACK_LEGACY",
                                error=f"{type(exc).__name__}: {exc}",
                                snapshot_ts=now)
                brain_meta = None      # legacy path below takes over

        if decision is None and brain_meta is None:
            # legacy single-call path (Brain 2.0 disabled or failed)
            try:
                from .context import build_context
                context = build_context(user_id, pos, sig, daily=daily,
                                        provider=self.provider)
            except Exception as exc:
                recorder.record(user_id, pos, trigger, "-", "local", None, False,
                                "CONTEXT_ERROR", error=type(exc).__name__, snapshot_ts=now)
                return out
            try:
                res = self.router.analyze(
                    "Manage this open trade. Reply with ONLY the JSON decision.",
                    context, escalate=escalate, user_id=user_id)
                model_name, layer = res.get("model", model_name), res.get("layer", layer)
                if layer != "local":
                    try:
                        decision = decisions.validate(res.get("text", ""))
                    except decisions.DecisionError as exc:
                        decision_err = f"invalid decision: {exc}"
                else:
                    decision_err = "ai unavailable - deterministic fallback"
            except Exception as exc:
                decision_err = f"router error: {type(exc).__name__}"

        if decision is None:
            decision = decisions.deterministic_hold(decision_err or "no decision")

        # 2) deterministic gate (SS19) -> 3) execute plan -> 4) audit (SS46)
        fresh = self._refresh_position(user_id, pos)
        if fresh is None:
            recorder.record(user_id, pos, trigger, model_name, layer, decision,
                            True, "POSITION_CLOSED_BEFORE_EXECUTION",
                            snapshot_ts=snapshot_ts)
            return out
        verdict = "OK"
        execution = None
        if decision["action"] == "HOLD":
            verdict = "OK_HOLD"
        else:
            allowed, verdict, plan = risk_gate.validate_action(
                user_id, fresh, decision, snapshot_ts,
                current_sl=fresh.get("sl"), last_action=st.get("last_action"))
            if allowed and plan:
                execution = risk_gate.execute_plan(user_id, fresh, plan,
                                                   reason=f"AI {trigger}")
                for e in execution.get("executed", []):
                    out["actions"].append(e["op"])
                st["last_action"] = {"action": decision["action"],
                                     "sl": decision.get("recommended_sl"),
                                     "ts": time.time()}
            recorder.record(user_id, pos, trigger, model_name, layer, decision,
                            decision_err is None, verdict,
                            execution=execution or {}, error=decision_err,
                            snapshot_ts=snapshot_ts, brain=brain_meta)
        if decision["action"] == "HOLD":
            recorder.record(user_id, pos, trigger, model_name, layer, decision,
                            decision_err is None, verdict, error=decision_err,
                            snapshot_ts=snapshot_ts, brain=brain_meta)

        st["last_review"] = now
        st["last_pl"] = float(fresh.get("profit") or 0)
        return out

    def _refresh_position(self, user_id: str, pos: dict) -> Optional[dict]:
        """Re-fetch the position right before acting (gate re-verification)."""
        ticket = int(pos.get("ticket") or 0)
        for p in self._positions(user_id):
            if int(p.get("ticket") or 0) == ticket:
                return p
        return None

    # ==================================================================
    # TP ladder (SS14/SS15/SS16) - deterministic, AI-independent
    # ==================================================================
    def _pinstripe_manage(self, user_id: str, pos: dict,
                          sig: Optional[dict]) -> Dict[str, Any]:
        """PINSTRIPE V6 common settings (owner locked rules, 2025) - the
        strategy IS the management plan, so this deterministic ladder runs
        INSTEAD of the generic TP ladder / position policy / AI:
          TP1 reached -> close exactly 50% of the position, then move the
                         SL to the exact entry price (breakeven);
          TP2 reached -> close the remaining 50% (final target).
        State (ps_tp_state) is restart-safe: the level is recomputed from
        the live price and re-issue is idempotent via the state."""
        actions: List[Dict[str, Any]] = []
        try:
            tp1 = (sig or {}).get("tp1")
            tp2 = (sig or {}).get("tp2")
            if not tp1 or not tp2:
                return {"actions": [], "verdict": "PINSTRIPE_NO_TP_CONTEXT",
                        "skip_ai": True}
            price = float(pos.get("price_current") or 0)
            entry = float(pos.get("price_open") or 0)
            tp1, tp2 = float(tp1), float(tp2)
            # PINSTRIPE TPs are CRT-derived: TP1/TP2 can sit on EITHER side
            # of the entry (e.g. a buy whose CRT midpoint is below it) - a
            # level is reached when price crosses it FROM THE ENTRY SIDE,
            # not merely by direction.
            hit1 = (price >= tp1) if tp1 > entry else (price <= tp1)
            hit2 = (price >= tp2) if tp2 > entry else (price <= tp2)
            crossed = 2 if hit2 else (1 if hit1 else 0)
            prev = int(self._state.get(int(pos["ticket"]), {}).get("ps_tp_state") or 0)
            if crossed <= prev:
                return {"actions": actions, "verdict": "PINSTRIPE_LADDER",
                        "tp_state": prev, "skip_ai": True}
            self._state.setdefault(int(pos["ticket"]), {})["ps_tp_state"] = crossed
            if crossed == 1:
                res = risk_gate.execute_plan(
                    user_id, pos, {"partial_fraction": 0.5},
                    reason="PINSTRIPE: TP1 -> close exactly 50% (locked spec)")
                actions.append(res)
                if entry > 0:
                    res2 = risk_gate.execute_plan(
                        user_id, pos, {"modify_sl": entry},
                        reason="PINSTRIPE: after the partial -> SL to breakeven")
                    actions.append(res2)
            elif crossed == 2:
                # TP2: close the remainder (if TP1 was never applied - e.g. a
                # gap straight to TP2 - the full close is the honest outcome)
                res = risk_gate.execute_plan(
                    user_id, pos, {"close_full": True},
                    reason="PINSTRIPE: TP2 -> final target (remainder closed)")
                actions.append(res)
            return {"actions": actions, "verdict": "PINSTRIPE_LADDER",
                    "tp_state": crossed, "tp1": tp1, "tp2": tp2, "skip_ai": True}
        except Exception as exc:
            return {"actions": actions, "verdict": "PINSTRIPE_ERROR",
                    "error": type(exc).__name__, "skip_ai": True}

    def _trendbreak_v2_manage(self, user_id: str, pos: dict,
                              sig: Optional[dict]) -> Dict[str, Any]:
        """TrendBreak v2 (H1) deterministic exits (owner Pine 2026-10-24):
        trail = best-price-so-far -/+ 5 x ATR(signal bar), applied ONLY
        when it tightens the existing stop, and a time exit that closes
        the position once 240 H1 bars have completed since entry. No TP
        exists on this position by rule - nothing else to manage."""
        actions: List[Dict[str, Any]] = []
        try:
            from ..strategies.research_only import trendbreak_v2 as tb2
            market = pos.get("app_market") or pos.get("symbol", "")
            is_buy = str(pos.get("type", "")).upper() == "BUY"
            entry = float(pos.get("price_open") or 0)
            sig_atr = float((sig or {}).get("atr") or 0)
            cur_stop = pos.get("sl")
            if cur_stop not in (None, "", 0):
                cur_stop = float(cur_stop)
            else:
                cur_stop = None
            if entry <= 0 or sig_atr <= 0:
                return {"actions": [], "verdict": "TB2_NO_SIG_CONTEXT",
                        "skip_ai": True}
            df = None
            if self.provider is not None:
                try:
                    df = self.provider.get_candles(market, "1H", limit=800)
                except Exception:
                    df = None
            if df is None or len(df) < 5:
                return {"actions": [], "verdict": "TB2_NO_DATA",
                        "skip_ai": True}
            idx = df.index if isinstance(df.index, pd.DatetimeIndex) else None
            # bars closed since the fill: entry bar (partial) counts as bar 0
            try:
                import pandas as _pd
                t0 = _pd.Timestamp((sig or {}).get("mt5_executed_at")
                                   or (sig or {}).get("entry_time")
                                   or df.index[-1])
                if t0.tzinfo is None:
                    t0 = t0.tz_localize("UTC")
                else:
                    t0 = t0.tz_convert("UTC")
                cut = idx.searchsorted(t0) if idx is not None else 0
                window = df.iloc[max(0, cut - 1):]
            except Exception:
                window = df
            held = max(0, len(window) - 1)
            best = (float(window["high"].max()) if is_buy
                    else float(window["low"].min()))
            trail = (best - tb2.SPEC["trail_mult"] * sig_atr if is_buy
                     else best + tb2.SPEC["trail_mult"] * sig_atr)
            tighten = (cur_stop is None or trail > cur_stop) if is_buy \
                else (cur_stop is None or trail < cur_stop)
            if tighten:
                res = risk_gate.execute_plan(
                    user_id, pos, {"modify_sl": round(trail, 6)},
                    reason="TB2 trail (5xATR from best price, tighten-only)")
                actions.append(res)
            if held >= tb2.SPEC["max_bars"]:
                res = risk_gate.execute_plan(
                    user_id, pos, {"close_full": True},
                    reason="TB2 time exit (240 H1 bars since entry)")
                actions.append(res)
            return {"actions": actions, "verdict": "TB2_TRAIL",
                    "best": best, "trail": round(trail, 6),
                    "held_bars": held, "sig_atr": sig_atr, "skip_ai": True}
        except Exception as exc:
            return {"actions": actions, "verdict": "TB2_ERROR",
                    "error": type(exc).__name__, "skip_ai": True}

    def _uni_tighten(self, user_id: str, pos: dict, st: dict,
                     new_sl: float, is_buy: bool, reason: str) -> Optional[dict]:
        """Ratchet guard for the universal ladder: never loosen, never
        re-send the same stop. Compares against BOTH the broker snapshot
        (possibly stale after a previous modify) and our own last write."""
        cur = [float(pos.get("sl") or 0), float(st.get("last_sl") or 0)]
        cur = [c for c in cur if c > 0]
        if cur:
            ref = max(cur) if is_buy else min(cur)
            looser = (new_sl < ref - 1e-9) if is_buy else (new_sl > ref + 1e-9)
            same = abs(new_sl - ref) <= 1e-9
            if looser or same:
                return None
        res = risk_gate.execute_plan(user_id, pos, {"modify_sl": round(new_sl, 5)},
                                     reason=reason)
        if res.get("executed"):
            st["last_sl"] = float(new_sl)
            return res
        return None

    # ==================================================================
    # UNIVERSAL TRADE MANAGER - owner reference implementation 2026-10-29
    # Stage 0 -> 1 at +1R : bank 50% (policy) + SL to break-even + buffer
    # Stage 1 -> 2 at +2R : lock profit (default +1R)
    # Stage 2             : trail the extreme (default 0.9R) and, when the
    #                       market flips against the position, tighten to
    #                       0.5R behind price. Stage advances on the BEST
    #                       price reached (never regresses with a wick).
    # ==================================================================
    def _universal_manage(self, user_id: str, pos: dict, sig: Optional[dict],
                          st: dict, now: float) -> Dict[str, Any]:
        actions: List[str] = []
        out: Dict[str, Any] = {"actions": actions, "closed": False}
        if not sig:
            return out
        entry = float(pos.get("price_open") or 0)
        sl0 = st.get("initial_sl")
        if sl0 is None:
            sl0 = float(pos.get("sl") or 0)
            st["initial_sl"] = sl0
        price = float(pos.get("price_current") or 0)
        if entry <= 0 or not sl0 or sl0 <= 0 or price <= 0 or abs(entry - sl0) <= 0:
            return out
        risk_dist = abs(entry - sl0)
        is_buy = str(pos.get("type", "")).upper() == "BUY"
        sign = 1.0 if is_buy else -1.0
        st["universal"] = True
        # extremes since entry (owner reference: highest/lowest tracking)
        st["highest"] = max(float(st.get("highest") or entry), price)
        st["lowest"] = min(float(st.get("lowest") or entry), price)

        rdoc = get_store().list("settings", filters={"userId": user_id,
                                                     "kind": "risk"}, limit=1)
        risk = rdoc[0] if rdoc else {}
        be_buf = float(risk.get("mg_be_buffer_r", 0.1) or 0.1)
        lock_r = float(risk.get("mg_lock_at_2r_r", 1.0) or 1.0)
        trail_r = float(risk.get("mg_trail_r", 0.9) or 0.9)
        tp1_policy = str(risk.get("tp1_policy") or "ai_decide")
        tp3_policy = str(risk.get("tp3_policy") or "close")

        # favorable excursion in R, measured on the BEST price reached
        best = st["highest"] if is_buy else st["lowest"]
        r_best = (best - entry) * sign / risk_dist

        stage = int(st.get("uni_stage") or 0)

        # TP3 final (broker TP should have filled; belt + braces)
        tp3 = sig.get("tp3")
        if tp3_policy == "close" and tp3 and (stage >= 1 or r_best >= 3.0) and \
                ((price >= float(tp3)) if is_buy else (price <= float(tp3))):
            res = risk_gate.execute_plan(user_id, pos, {"close_full": True},
                                         reason="Universal: TP3 reached - secure final profit")
            if res.get("executed"):
                actions.append("close_full(tp3)")
                self._notify_tp(user_id, pos, sig, 3)
                out["closed"] = True
            return out

        # Stage 0 -> 1 : +1R -> bank (policy) + break-even + buffer
        if stage == 0 and r_best >= 1.0:
            self._notify_tp(user_id, pos, sig, 1)
            if tp1_policy == "partial":
                res = risk_gate.execute_plan(user_id, pos, {"partial_fraction": 0.5},
                                             reason="Universal TP1: bank 50%")
                if res.get("executed"):
                    actions.append("partial_close(50%)")
            new_sl = entry + sign * be_buf * risk_dist
            if self._uni_tighten(user_id, pos, st, new_sl, is_buy,
                                 f"Universal TP1: SL to BE +{be_buf}R"):
                actions.append(f"modify_sl(be+{be_buf}R)")
            st["uni_stage"] = 1
            stage = 1
            recorder.record(user_id, pos, "universal_tp1", "deterministic",
                            "local", None, True, "UNIVERSAL_TP1",
                            execution={"actions": actions}, snapshot_ts=now)

        # Stage 1 -> 2 : +2R -> lock profit
        if stage == 1 and r_best >= 2.0:
            self._notify_tp(user_id, pos, sig, 2)
            new_sl = entry + sign * lock_r * risk_dist
            if self._uni_tighten(user_id, pos, st, new_sl, is_buy,
                                 f"Universal TP2: lock +{lock_r}R"):
                actions.append(f"modify_sl(lock {lock_r}R)")
            st["uni_stage"] = 2
            stage = 2
            recorder.record(user_id, pos, "universal_tp2", "deterministic",
                            "local", None, True, "UNIVERSAL_TP2_LOCK",
                            execution={"actions": actions}, snapshot_ts=now)

        # Stage 2 : trail the extreme; tighten harder on a market flip
        if stage == 2:
            extreme = st["highest"] if is_buy else st["lowest"]
            trail_sl = extreme - sign * trail_r * risk_dist
            if self._uni_tighten(user_id, pos, st, trail_sl, is_buy,
                                 f"Universal trail {trail_r}R behind the extreme"):
                actions.append(f"modify_sl(trail {trail_r}R)")
                pos = self._refresh_position(user_id, pos) or pos
            state = self._read_market(str(pos.get("app_market") or pos.get("symbol", "")))
            flipped = (state == "strong_sell") if is_buy else (state == "strong_buy")
            if flipped:
                tighter = price - sign * 0.5 * risk_dist
                if self._uni_tighten(user_id, pos, st, tighter, is_buy,
                                     "Universal: market flipped against the position - tighten"):
                    actions.append("modify_sl(flip-tighten 0.5R)")
        return out

    def _read_market(self, market: str, bars: int = 30) -> str:
        """Owner reference `read_market`: EMA8/21 + 6-bar momentum on 5M ->
        strong_buy / strong_sell / neutral / weak. Bridge candles, never
        invented: unreadable market reads neutral (no action)."""
        try:
            import pandas as pd
            from ..execution.mt5 import bridge_get
            data = bridge_get(f"/rates?market={market}&tf=5M&count={bars}") or {}
            rows = data.get("bars") or []
            if len(rows) < 20:
                return "neutral"
            closes = [float(b["c"]) for b in rows]
            ser = pd.Series(closes)
            ema_f = float(ser.ewm(span=8).mean().iloc[-1])
            ema_s = float(ser.ewm(span=21).mean().iloc[-1])
            last = closes[-1]
            momentum = last - closes[-6]
            if ema_f > ema_s and momentum > 0 and last > ema_f:
                return "strong_buy"
            if ema_f < ema_s and momentum < 0 and last < ema_f:
                return "strong_sell"
            if abs(momentum) < last * 0.0003:
                return "weak"
            return "neutral"
        except Exception:
            return "neutral"

    def _tp_ladder(self, user_id: str, pos: dict, sig: Optional[dict],
                   st: dict, trigger: str) -> Dict[str, Any]:
        """Updates tp_state from live price and applies the hard rules:
        TP1 -> per policy (ai_decide default), TP2 -> SL := TP1 (LOCK),
        TP3 -> final behavior (close remaining, per policy)."""
        actions: List[str] = []
        out: Dict[str, Any] = {"actions": actions}
        if not sig:
            return out
        price = float(pos.get("price_current") or 0)
        is_buy = str(pos.get("type", "")).upper() == "BUY"
        rdoc = get_store().list("settings", filters={"userId": user_id,
                                                     "kind": "risk"}, limit=1)
        risk = rdoc[0] if rdoc else {}
        tp1_policy = str(risk.get("tp1_policy") or "ai_decide")
        tp3_policy = str(risk.get("tp3_policy") or "close")

        # detect level crossings (transition only)
        crossed = 0
        for i in (3, 2, 1):
            tp = sig.get(f"tp{i}")
            if tp and ((price >= float(tp)) if is_buy else (price <= float(tp))):
                crossed = i
                break
        prev = int(st.get("tp_state") or 0)
        if crossed <= prev:
            # nothing new crossed; still repair a violated TP2 lock on reconcile
            if prev >= 2 and trigger == "reconcile":
                lock = sig.get("tp1")
                if lock and self._sl_is_looser(pos, float(lock), is_buy):
                    res = risk_gate.execute_plan(user_id, pos,
                                                 {"modify_sl": float(lock)},
                                                 reason="TP2 lock repair (reconcile)")
                    if res.get("executed"):
                        actions.append("modify_sl(tp1_repair)")
            out["verdict"] = "TP_LADDER_NO_CHANGE"
            return out

        st["tp_state"] = crossed
        if crossed >= 1:
            self._notify_tp(user_id, pos, sig, 1)
            if crossed == 1 and tp1_policy == "protect":
                res = risk_gate.execute_plan(user_id, pos,
                                             {"modify_sl": float(pos["price_open"])},
                                             reason="TP1 policy: breakeven")
                if res.get("executed"):
                    actions.append("modify_sl(breakeven)")
            elif crossed == 1 and tp1_policy == "partial":
                res = risk_gate.execute_plan(user_id, pos, {"partial_fraction": 0.5},
                                             reason="TP1 policy: 50% partial")
                if res.get("executed"):
                    actions.append("partial_close(50%)")
                    # lock what was banked: SL -> entry so the runner can
                    # only add (owner 2026-10-28: the manager must protect
                    # profit, never give a banked partial back)
                    _be = risk_gate.execute_plan(user_id, pos,
                                                 {"modify_sl": float(pos["price_open"])},
                                                 reason="TP1 policy: breakeven after partial")
                    if _be.get("executed"):
                        actions.append("modify_sl(breakeven)")
            # tp1_policy ai_decide (default): NO deterministic action - the AI
            # review (trigger tp1_hit) decides continue/protect/exit (SS14)
        if crossed >= 2:
            self._notify_tp(user_id, pos, sig, 2)
            lock = sig.get("tp1")
            if lock:
                res = risk_gate.execute_plan(user_id, pos,
                                             {"modify_sl": float(lock)},
                                             reason="SS15 hard rule: TP2 -> SL := TP1")
                if res.get("executed"):
                    actions.append("modify_sl(tp1_lock)")
        if crossed >= 3:
            self._notify_tp(user_id, pos, sig, 3)
            if tp3_policy == "close":
                res = risk_gate.execute_plan(user_id, pos, {"close_full": True},
                                             reason="TP3 reached - secure final profit")
                if res.get("executed"):
                    actions.append("close_full(tp3)")
        out["verdict"] = f"TP{crossed}_LADDER_APPLIED"
        # Only a CLOSED position (TP3 final close) skips the AI review;
        # after TP1/TP2 deterministic actions the AI still reassesses
        # continuation (SS14/SS15: gather fresh info, then decide).
        out["skip_ai"] = crossed >= 3 and tp3_policy == "close"
        return out

    @staticmethod
    def _sl_is_looser(pos: dict, lock: float, is_buy: bool) -> bool:
        sl = pos.get("sl")
        if not sl:
            return True
        return (float(sl) < float(lock) - 1e-9) if is_buy else (float(sl) > float(lock) + 1e-9)

    def _notify_tp(self, user_id: str, pos: dict, sig: dict, level: int) -> None:
        try:
            from ..notifications.service import notify
            notify(user_id, f"TP{level}_HIT_LIVE",
                   f"TP{level} reached - {pos.get('app_market')} {pos.get('type')}",
                   f"{sig.get('strategy_name')} | {sig.get('signal_id')}\n"
                   "Live MT5 position management engaged.",
                   signal_id=sig.get("id"))
        except Exception:
            pass

    # ==================================================================
    # dashboard surface (P10 consumes this)
    # ==================================================================
    def status(self) -> Dict[str, Any]:
        return {"last_tick_ts": self.last_tick_ts,
                "managed_positions": len(self._state),
                "state": {str(k): {kk: vv for kk, vv in v.items()}
                          for k, v in list(self._state.items())[:20]},
                "last_summary": self.last_summary,
                "min_review_interval_s": MIN_REVIEW_INTERVAL_S,
                "max_review_age_s": MAX_REVIEW_AGE_S}


_manager: Optional[TradeManager] = None
_mgr_lock = threading.Lock()


def get_manager() -> TradeManager:
    global _manager
    with _mgr_lock:
        if _manager is None:
            from ..state import State
            _manager = TradeManager(provider=State.provider)
        return _manager
