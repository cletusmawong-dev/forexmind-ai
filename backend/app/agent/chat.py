"""Agent Chat - answers ONLY from stored data (SPEC §29, §57).

The chat engine queries the knowledge base (signals, lessons, hypotheses,
experiments, strategies) and renders answers from those records. It never
fabricates results, and if the AI provider is unavailable it says so honestly.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..db.store import get_store
from ..learning.versions import active_params, active_version
from ..strategies import all_strategies

CAPABILITIES = (
    "I can answer from the stored research data, for example:\n"
    "• \"Why did the last signal qualify?\"\n"
    "• \"Why did the last trade lose?\"\n"
    "• \"What have you learned from Strategy 1 / Strategy 2?\"\n"
    "• \"Show me the latest lessons.\"\n"
    "• \"Show me pending suggestions.\"\n"
    "• \"Why are you suggesting this change?\"\n"
    "• \"Compare Strategy 1 and Strategy 2.\"\n"
    "• \"Which strategy is performing better this week?\"\n"
    "• \"Show me the last five experiments.\"\n"
    "• \"What is your current objective?\""
)


def _fmt_signal(sig: dict) -> str:
    return (f"{sig['signal_id']} | {sig['market']} {sig['direction']} {sig['timeframe']} | "
            f"{sig['strategy_name']} v{sig.get('strategy_version', '1.0')} | "
            f"Entry {sig.get('entry'):,.5g} SL {sig.get('sl'):,.5g} | "
            f"Score {sig.get('score')}/100 | {sig.get('status')}")


def answer(message: str, user_id: str) -> Dict[str, Any]:
    store = get_store()
    q = message.lower()

    # ---- why did signal qualify -----------------------------------------
    if "qualify" in q or "why did" in q and "signal" in q:
        sigs = store.list("signals", filters={"userId": user_id}, limit=1)
        if not sigs:
            return {"reply": "There are no signals stored yet, so I have nothing to explain."}
        s = sigs[0]
        checks = "\n".join(f"✓ {c['label']} - {c.get('detail', '')}" for c in s.get("checks", []))
        return {"reply": f"{s['signal_id']} ({s['market']} {s['direction']}, "
                         f"{s['strategy_name']}):\n\n{s.get('reason', '')}\n\n{checks}",
                "signal_id": s["id"]}

    # ---- why did trade lose ---------------------------------------------
    if "lose" in q or "loss" in q or "lost" in q:
        losses = [s for s in store.list("signals", filters={"userId": user_id, "completed": True},
                                        limit=100) if s.get("outcome") == "LOSS"]
        if not losses:
            return {"reply": "There are no recorded losses yet."}
        s = losses[0]
        ra = s.get("result_analysis") or {}
        return {"reply": f"{s['signal_id']} ({s['market']} {s['direction']}) hit the stop loss.\n"
                         f"{ra.get('what_happened', '')}\n\n"
                         f"Setup conditions at entry:\n" +
                         "\n".join(f"• {c}" for c in ra.get("setup_conditions", [])) +
                         f"\n\n{ra.get('isolated_or_pattern', '')}",
                "signal_id": s["id"]}

    # ---- lessons ----------------------------------------------------------
    if "lesson" in q or ("learned" in q and "strateg" not in q):
        lessons = store.list("lessons", filters={"userId": user_id}, limit=3)
        if not lessons:
            return {"reply": "No lessons recorded yet - I need more completed signals "
                             "before drawing any observation."}
        body = "\n\n".join(f"LESSON #{l.get('lesson_no')} ({l['strategy_name']}):\n"
                           f"{l['observation']}\nEvidence: {l['evidence']} signals | "
                           f"win rate {l['win_rate']}% vs {l['baseline_win_rate']}% baseline"
                           for l in lessons)
        return {"reply": body}

    if "learned from strategy 1" in q or "learned from strategy 2" in q or \
            ("learned" in q and "strateg" in q):
        which = "strategy_1_zero_lag" if "1" in q else "strategy_2_ema_atr"
        name = all_strategies()[which].short_name
        lessons = store.list("lessons", filters={"userId": user_id, "strategy_id": which},
                             limit=3)
        if not lessons:
            return {"reply": f"I have not recorded evidence-backed lessons for {name} yet."}
        body = "\n\n".join(f"LESSON #{l.get('lesson_no')}: {l['observation']} "
                           f"(evidence: {l['evidence']} signals, {l['win_rate']}% vs "
                           f"{l['baseline_win_rate']}%)" for l in lessons)
        return {"reply": body}

    # ---- pending suggestions / approvals -----------------------------------
    if "pending" in q or "suggestion" in q or "approval" in q:
        pending = store.list("hypotheses", filters={"status": "AWAITING_APPROVAL"}, limit=5)
        if not pending:
            return {"reply": "There are no suggestions awaiting your approval right now."}
        body = "\n\n".join(f"{h['hypothesis_id']} ({h['strategy_name']}): change "
                           f"{h['variable']} {h['old_value']} -> {h['new_value']}. "
                           f"Reason: {h['reason']}" for h in pending)
        return {"reply": "Pending suggestions awaiting your approval:\n\n" + body}

    # ---- why suggesting this change ----------------------------------------
    if "why are you suggesting" in q or "why suggest" in q:
        pending = store.list("hypotheses", filters={"status": "AWAITING_APPROVAL"}, limit=1)
        if not pending:
            return {"reply": "I currently have no pending change suggestions."}
        h = pending[0]
        exp = store.get("experiments", h.get("experiment_id", ""))
        exp_line = f"\n\nExperiment result: {exp['result']} - {exp['conclusion']}" if exp else ""
        return {"reply": f"{h['hypothesis_id']} proposes changing {h['variable']} from "
                         f"{h['old_value']} to {h['new_value']} on {h['strategy_name']}. "
                         f"Reason: {h['reason']} Expected effect: {h['expected_effect']}"
                         f"{exp_line}\n\nEverything else stays UNCHANGED, and nothing is "
                         f"applied until you approve it."}

    # ---- compare strategies -------------------------------------------------
    if "compare" in q or "better" in q:
        return {"reply": _compare_strategies(user_id)}

    # ---- experiments ---------------------------------------------------------
    if "experiment" in q:
        exps = store.list("experiments", filters={"userId": user_id}, limit=5)
        if not exps:
            return {"reply": "No experiments have been run yet."}
        body = "\n\n".join(f"{e.get('hypothesis_id', '?')} | {e['strategy_id']} | "
                           f"{e['variable']} {e['old_value']} -> {e['new_value']} | "
                           f"{e['result']} | WR {e['original_metrics'].get('win_rate')}% -> "
                           f"{e['experimental_metrics'].get('win_rate')}%"
                           for e in exps)
        return {"reply": "Last experiments (same-dataset comparisons):\n\n" + body}

    # ---- objective -------------------------------------------------------------
    if "objective" in q or "goal" in q:
        from .core import get_goals, compute_progress
        g = get_goals(user_id); p = compute_progress(user_id)
        return {"reply": f"My current objective is {g['daily_objective_pct']:+.1f}% for the day "
                         f"(account ${g['account_balance']}). Progress so far: "
                         f"{p['daily_progress_pct']:+.2f}%. Remember: the objective guides my "
                         f"research focus - it NEVER forces signals. If there is no valid "
                         f"setup, I protect capital and wait."}

    # ---- status ------------------------------------------------------------------
    if "status" in q or "what are you doing" in q:
        from .core import agent_status
        st = agent_status(user_id)
        return {"reply": f"Agent status: ACTIVE. Current task: {st['current_task']}. "
                         f"Strategies active: {st['strategies_active']} "
                         f"(S1 v{st['strategy_versions']['strategy_1_zero_lag']}, "
                         f"S2 v{st['strategy_versions']['strategy_2_ema_atr']}). "
                         f"Signals today: {st['signals_today']}. Lessons: {st['lessons']}. "
                         f"Experiments: {st['experiments']}. Pending approvals: "
                         f"{st['pending_approvals']}."}

    if "help" in q or "what can you" in q:
        return {"reply": CAPABILITIES}

    return {"reply": "I don't have stored data matching that question, and I never invent "
                     "answers.\n\n" + CAPABILITIES}


def _compare_strategies(user_id: str) -> str:
    store = get_store()
    completed = store.list("signals", filters={"userId": user_id, "completed": True},
                           limit=1000)
    lines = []
    for sid, strat in all_strategies().items():
        sigs = [s for s in completed if s["strategy_id"] == sid]
        n = len(sigs)
        if n == 0:
            lines.append(f"{strat.short_name} v{active_version(sid)}: no completed signals yet.")
            continue
        wins = sum(1 for s in sigs if s.get("outcome") == "WIN")
        losses = sum(1 for s in sigs if s.get("outcome") == "LOSS")
        rs = [s.get("r_multiple", 0.0) for s in sigs]
        wr = 100.0 * wins / max(wins + losses, 1)
        pf_g = sum(r for r in rs if r > 0)
        pf_l = abs(sum(r for r in rs if r < 0))
        lines.append(f"{strat.short_name} v{active_version(sid)}: {n} completed signals, "
                     f"win rate {wr:.1f}% (W{wins}/L{losses}), avg R {sum(rs)/n:+.2f}, "
                     f"profit factor {(pf_g/pf_l if pf_l else 999):.2f}, "
                     f"params: {_params_brief(sid)}")
    return "Strategy comparison from completed signals:\n\n" + "\n".join(lines)


def _params_brief(strategy_id: str) -> str:
    p = active_params(strategy_id)
    if strategy_id == "strategy_2_ema_atr":
        return (f"EMA {p['fast_len']}/{p['slow_len']}, ATR {p['atr_len']}, "
                f"SL {p['sl_mult']}xATR")
    return f"length {p['length']}, band {p['band_mult']}x"
