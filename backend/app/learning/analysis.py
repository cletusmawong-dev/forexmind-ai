"""Win/Loss analysis -> Lessons (SPEC §19, §20, §40).

Reads ACTUAL recorded signal outcomes, segments them by observable conditions
(strategy, session, volatility regime, direction, timeframe) and records a
lesson only when the evidence is strong enough (n >= threshold and a clear
deviation from the strategy baseline). Lessons are OBSERVATIONS - never
automatic strategy changes (SPEC §26, §41).

A single loss is never sufficient evidence for a strategy change (SPEC §20).
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..config import settings
from ..db.store import get_store


def _fmt(v: float) -> str:
    return f"{v:.1f}%"


def analyze_closed_signals(user_id: str, commit: bool = True) -> List[Dict[str, Any]]:
    """Segments completed signals and produces evidence-backed lessons."""
    store = get_store()
    completed = store.list("signals", filters={"userId": user_id, "completed": True},
                           limit=2000)
    if len(completed) < settings.min_trades_for_lesson * 2:
        return []

    lessons: List[Dict[str, Any]] = []
    try:
        seq = store.count("lessons") + 1
    except Exception:
        seq = 1   # quota latch etc - never let a counter kill the whole pass

    for strategy_id in {s["strategy_id"] for s in completed}:
        strat_signals = [s for s in completed if s["strategy_id"] == strategy_id]
        wins = [s for s in strat_signals if s.get("outcome") == "WIN"]
        baseline_n = len(strat_signals)
        baseline_wr = 100.0 * len(wins) / baseline_n
        if baseline_n < settings.min_trades_for_lesson * 2:
            continue

        segments: Dict[str, List[dict]] = {}
        for s in strat_signals:
            mc = s.get("market_conditions") or {}
            tags = []
            tags.append(f"session:{mc.get('session', '?')}")
            tags.append(f"direction:{s.get('direction', '?')}")
            tags.append(f"timeframe:{s.get('timeframe', '?')}")
            vr = mc.get("volatility_regime", "unknown")
            tags.append(f"volatility:{vr}")
            mtf = s.get("mtf") or {}
            if mtf:
                agree = sum(1 for v in mtf.values() if v and
                            ((v > 0) == (s.get("direction") == "BUY")))
                tags.append(f"mtf_alignment:{agree}of{len(mtf)}")
            for t in tags:
                segments.setdefault(t, []).append(s)

        for tag, group in sorted(segments.items()):
            n = len(group)
            if n < settings.min_trades_for_lesson:
                continue
            g_wr = 100.0 * sum(1 for s in group if s.get("outcome") == "WIN") / n
            delta = g_wr - baseline_wr
            if abs(delta) < settings.lesson_delta_pp:
                continue
            kind = "winning" if delta > 0 else "losing"
            human_tag = tag.replace("session:", "trading session ").replace(
                "direction:", "direction ").replace("timeframe:", "timeframe ").replace(
                "volatility:", "volatility regime ").replace("mtf_alignment:", "MTF alignment ")
            if "session:" in tag or "direction:" in tag or "timeframe:" in tag or "volatility:" in tag:
                observation = (f"Recent {strategy_name(strategy_id)} signals show a "
                               f"{'higher' if delta > 0 else 'lower'} frequency of success when the "
                               f"signal occurs during {human_tag}: win rate {_fmt(g_wr)} across "
                               f"{n} comparable signals vs a {_fmt(baseline_wr)} baseline. "
                               f"Status: OBSERVATION - not a conclusion.")
            else:
                observation = (f"Recent {strategy_name(strategy_id)} signals with {human_tag} show "
                               f"win rate {_fmt(g_wr)} across {n} comparable signals vs a "
                               f"{_fmt(baseline_wr)} baseline. Status: OBSERVATION.")

            key = f"{strategy_id}|{tag}"
            # de-duplicate lessons with the same segment
            existing = store.list("lessons", filters={"dedupe_key": key}, limit=1)
            if existing:
                store.update("lessons", existing[0]["id"], {
                    "evidence": n, "win_rate": round(g_wr, 1),
                    "baseline_win_rate": round(baseline_wr, 1),
                    "observation": observation,
                })
                continue

            lessons.append(store.create("lessons", {
                "userId": user_id,
                "lesson_no": seq,
                "strategy_id": strategy_id,
                "strategy_name": strategy_name(strategy_id),
                "observation": observation,
                "segment": tag,
                "dedupe_key": key,
                "evidence": n,
                "win_rate": round(g_wr, 1),
                "baseline_win_rate": round(baseline_wr, 1),
                "delta_pp": round(delta, 1),
                "status": "OBSERVATION",
            }, doc_id=f"lesson-{seq:03d}"))
            seq += 1
    return lessons


def strategy_name(strategy_id: str) -> str:
    from ..strategies import get_strategy
    try:
        return get_strategy(strategy_id).short_name
    except KeyError:
        return strategy_id


def analyze_single_result(user_id: str, signal: Dict[str, Any]) -> Dict[str, Any]:
    """Per-signal result analysis (SPEC §19/§20) - descriptive, never a
    strategy change, never fabricated."""
    mc = signal.get("market_conditions") or {}
    won = signal.get("outcome") == "WIN"
    factors = []
    for c in signal.get("checks", []):
        factors.append(f"{c['label']}: {'confirmed' if c.get('ok') else 'not met'}")
    what = ("Price reached {} before the stop loss."
            .format("take profit " + str(signal.get("tp_hits", 1))) if won
            else "Price hit the stop loss before the first take profit.")
    analysis = {
        "what_happened": what,
        "setup_conditions": factors,
        "market_context": {
            "session": mc.get("session"),
            "volatility_regime": mc.get("volatility_regime"),
            "timeframe": signal.get("timeframe"),
            "mtf": signal.get("mtf"),
        },
        "isolated_or_pattern": (
            "This is a single result. It is recorded as evidence; it does NOT by itself "
            "justify any strategy change. Recurring patterns are reviewed in the Learning Lab."
        ),
    }
    store = get_store()
    store.update("signals", signal["id"], {"result_analysis": analysis})
    return analysis
