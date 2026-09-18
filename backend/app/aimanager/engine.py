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
        for pos in self._positions(user_id):
            summary["positions"] += 1
            sig = self._link_signal(user_id, pos)
            st = self._state_for(pos, sig)
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

        # 0) TP-ladder deterministic layer FIRST (SS14-16): hard rules run
        #    regardless of AI availability/decisions (implemented in P9)
        ladder = self._tp_ladder(user_id, pos, sig, st, trigger)
        out["actions"].extend(ladder.get("actions", []))
        if ladder.get("skip_ai"):
            st["last_review"] = now
            st["last_pl"] = float(pos.get("profit") or 0)
            recorder.record(user_id, pos, trigger, "deterministic", "local",
                            None, True, ladder.get("verdict", "TP_LADDER"),
                            execution=ladder, snapshot_ts=now)
            return out

        # 1) evidence -> AI (primary, escalation per event severity - SS7)
        try:
            from .context import build_context
            context = build_context(user_id, pos, sig, daily=daily,
                                    provider=self.provider)
        except Exception as exc:
            recorder.record(user_id, pos, trigger, "-", "local", None, False,
                            "CONTEXT_ERROR", error=type(exc).__name__, snapshot_ts=now)
            return out

        model_name, layer = settings.ai_primary_model, "primary"
        decision: Optional[dict] = None
        decision_err: Optional[str] = None
        snapshot_ts = time.time()
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
                            snapshot_ts=snapshot_ts)
        if decision["action"] == "HOLD":
            recorder.record(user_id, pos, trigger, model_name, layer, decision,
                            decision_err is None, verdict, error=decision_err,
                            snapshot_ts=snapshot_ts)

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
