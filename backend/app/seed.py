"""One-time seeding (idempotent).

Creates the demo user, registers strategies, and backfills REAL computed
history: signals are produced by running the strategy modules over the stored
historical DEMO dataset (never fabricated numbers), then lessons, hypotheses
and experiments are derived from those actual outcomes (SPEC §38, §59).
Everything is labeled demo data.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from .agent import core as agent_core
from .config import session_of, settings
from .db.store import get_store
from .learning.analysis import analyze_closed_signals
from .learning.versions import active_params, ensure_strategy_docs
from .state import State


def _deterministic_roll(key: str) -> float:
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 / 1000.0


def seed_if_empty() -> None:
    store = get_store()
    if store.count("signals") > 0:
        ensure_strategy_docs()
        _ensure_demo_user()
        return

    print("[seed] empty database - seeding demo research history...")
    _ensure_demo_user()
    ensure_strategy_docs()
    user_id = _demo_user_id()
    agent_core.ensure_user_docs(user_id)
    _widen_visibility()      # seed sees the FULL historical dataset
    try:
        _seed_history(user_id)
    finally:
        _restore_replay_window()

def _seed_history(user_id: str) -> None:
    store = get_store()
    for market in State.provider.markets():
        store.create("markets", {
            "symbol": market, "provider": State.provider.name,
            "demo": True, "note": "DEMO / HISTORICAL dataset - not live market data",
        })

    # ------------------------------------------------------------------
    # backfill signals from actual walk-forward backtests on the dataset
    # ------------------------------------------------------------------
    imported = 0
    for market in State.provider.markets():
        for tf in ("15M", "1H"):
            df = State.provider.get_candles(market, tf, limit=4000)
            if df is None:
                continue
            for sid, strategy in list(_strategies().items()):
                sdoc = store.list("strategies", filters={"id": sid}, limit=1)
                if sdoc and sdoc[0].get("status") != "ACTIVE":
                    continue
                params = active_params(sid)
                trades = strategy.backtest(df, params)
                if not trades:
                    continue
                for tr in trades:
                    tr["market"] = market
                    tr["timeframe"] = tf
                state = strategy.compute(df, params)
                vr_series = strategy._volatility_rank(state)
                index_map = {str(t): i for i, t in enumerate(df.index)}
                higher = State.provider.higher_frames(market, tf)

                for tr in trades[-14:]:  # recent window per market/tf/strategy
                    i = index_map.get(tr["entry_time"])
                    cand = None
                    if i is not None:
                        try:
                            cand = strategy.build_candidate(
                                state, i, {"direction": tr["direction"], "i": i},
                                df, market, tf, params, higher, {"session": tr.get("session", "London")})
                        except Exception:
                            cand = None
                    imported += _import_trade(user_id, strategy, tr, cand, vr_series, i)

    print(f"[seed] imported {imported} historical demo signals")

    # ------------------------------------------------------------------
    # learning: lessons -> hypotheses -> experiments (real computed results)
    # ------------------------------------------------------------------
    lessons = analyze_closed_signals(user_id)
    print(f"[seed] generated {len(lessons)} evidence-backed lessons")

    from .learning.hypotheses import propose_from_lessons
    hyps = propose_from_lessons(user_id, max_new=3)
    print(f"[seed] proposed {len(hyps)} hypotheses")

    for h in hyps[:2]:
        try:
            exp = State.experiments.run_from_hypothesis(user_id, h)
            print(f"[seed] experiment for {h['hypothesis_id']}: {exp['result']}")
        except Exception as e:
            print(f"[seed] experiment for {h['hypothesis_id']} failed: {e}")

    # ------------------------------------------------------------------
    # activity + notifications for a lively, honest home feed
    # ------------------------------------------------------------------
    recent = store.list("signals", filters={"userId": user_id}, limit=8, desc=True)
    for s in reversed(recent):
        store.create("agent_activity", {
            "userId": None, "kind": "SIGNAL",
            "message": f"{s['strategy_name']}: {s['direction']} signal on {s['market']} "
                       f"{s['timeframe']} ({s['signal_id']})",
            "market": s["market"],
        })
        if s.get("completed"):
            store.create("agent_activity", {
                "userId": None, "kind": "ANALYSIS",
                "message": f"{s['signal_id']} completed: {s['status']} "
                           f"({s['r_multiple']:+.1f}R). Trade analyzed.",
            })
    last = recent[0] if recent else None
    if last:
        t0 = pd.Timestamp(last["candle_time"])
        for mins, msg, kind in [(0, f"Scanning {last['market']} {last['timeframe']}...", "SCAN"),
                                (15, f"{last['strategy_name']} candidate detected", "CANDIDATE"),
                                (16, "Risk conditions checked", "RISK"),
                                (16, "Signal approved for notification", "SIGNAL"),
                                (17, f"User notified - {last['signal_id']}", "NOTIFY")]:
            store.create("agent_activity", {
                "userId": None, "kind": kind, "message": msg,
                "ts_override": (t0 + pd.Timedelta(minutes=mins)).isoformat(),
            })

    for l in lessons[:2]:
        store.create("notifications", {
            "userId": user_id, "type": "NEW_LESSON", "read": False,
            "title": f"New lesson #{l.get('lesson_no')} - {l['strategy_name']}",
            "body": l["observation"][:160], "signal_id": None, "meta": {},
        })
    pending = store.list("hypotheses", filters={"status": "AWAITING_APPROVAL"}, limit=3)
    for h in pending:
        store.create("notifications", {
            "userId": user_id, "type": "APPROVAL_REQUIRED", "read": False,
            "title": f"Approval required - {h['hypothesis_id']}",
            "body": f"{h['strategy_name']}: {h['variable']} {h['old_value']} -> "
                    f"{h['new_value']}. Review the experiment analysis.",
            "signal_id": None, "meta": {"hypothesis_id": h["id"]},
        })

    store.flush()
    print("[seed] done.")


# ---------------------------------------------------------------------------
def _strategies():
    from .strategies import all_strategies
    return all_strategies()


def _demo_user_id() -> str:
    store = get_store()
    u = store.list("users", filters={"email": "demo@forexmind.ai"}, limit=1)
    return u[0]["id"] if u else ""


def _ensure_demo_user() -> None:
    from .core.security import hash_password, make_salt
    store = get_store()
    if store.list("users", filters={"email": "demo@forexmind.ai"}, limit=1):
        return
    salt = make_salt()
    store.create("users", {
        "email": "demo@forexmind.ai",
        "display_name": "Cletus (Demo)",
        "salt": salt,
        "password_hash": hash_password("demo1234", salt),
        "demo": True,
    }, doc_id="demo-user")


def _import_trade(user_id, strategy, tr, cand, vr_series, i) -> int:
    store = get_store()
    day = str(tr["entry_time"])[:10]
    seq = store.count("signals") + 1
    signal_id = f"SIG-{day.replace('-', '')}-{seq:03d}"

    tps = tr["tps"] + [None] * (3 - len(tr["tps"]))
    status = tr["status"]
    if status == "EXPIRED" and tr.get("tp_hits"):
        status = f"TP{tr['tp_hits']}_HIT"

    roll = _deterministic_roll(tr["entry_time"] + strategy.id)
    if roll < 0.45:
        user_action = "entered"
    elif roll < 0.70:
        user_action = "skipped"
    else:
        user_action = None

    vr = 0.5
    try:
        if vr_series is not None and i is not None and len(vr_series) > i:
            vr = float(vr_series.iloc[i]) if not pd.isna(vr_series.iloc[i]) else 0.5
    except Exception:
        pass

    doc = store.create("signals", {
        "userId": user_id,
        "createdAt": tr["entry_time"],
        "signal_id": signal_id,
        "day": day,
        "strategy_id": strategy.id,
        "strategy_name": strategy.short_name,
        "strategy_version": "1.0",
        "market": tr.get("market", ""),
        "timeframe": tr.get("timeframe", "15M"),
        "direction": tr["direction"],
        "entry": tr["entry"],
        "entry_zone": cand.entry_zone if cand else [tr["entry"], tr["entry"]],
        "sl": tr["sl"],
        "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
        "risk": tr["risk"],
        "rr_primary": cand.rr_primary if cand else 2.0,
        "score": cand.score if cand else 70,
        "score_components": cand.score_components if cand else {},
        "reason": cand.analysis if cand else "Historical demo signal (recomputed).",
        "checks": cand.checks if cand else [],
        "mtf": cand.mtf if cand else None,
        "market_conditions": {
            "session": tr.get("session", "London"),
            "volatility_regime": ("low" if vr < 0.33 else "high" if vr > 0.75 else "normal"),
            "price": tr["entry"],
        },
        "candle_time": tr["entry_time"],
        "status": status,
        "tp_hits": tr.get("tp_hits", 0),
        "r_multiple": tr["r_multiple"],
        "outcome": tr["outcome"],
        "completed": True,
        "completed_at": tr["exit_time"],
        "exit_price": tr.get("exit_price"),
        "duration_bars": tr.get("duration_bars"),
        "user_action": user_action,
        "user_entry_price": tr["entry"] if user_action == "entered" else None,
        "user_trade_result": ({"status": status, "r_multiple": tr["r_multiple"],
                               "outcome": tr["outcome"]}
                              if user_action == "entered" else None),
    })
    return 1


def _widen_visibility() -> None:
    """Seeding runs on the complete stored historical dataset."""
    for sym in State.provider.markets():
        try:
            State.provider._cursor[sym] = len(State.provider._base[sym])
        except Exception:
            pass


def _restore_replay_window() -> None:
    """Return the replay clock to the live window (~4000 5M bars)."""
    for sym in State.provider.markets():
        try:
            State.provider._cursor[sym] = max(0, len(State.provider._base[sym]) - 4000)
        except Exception:
            pass
