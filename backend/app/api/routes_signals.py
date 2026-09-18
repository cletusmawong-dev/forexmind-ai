"""Signal routes (SPEC §7, §16, §17, §50)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import INITIAL_MARKETS, TIMEFRAMES
from ..learning.analysis import analyze_single_result
from ..models.schemas import AnalyzeIn, ManualResultIn, SignalActionIn
from ..notifications.service import notify
from ..state import State
from .deps import get_user_id

router = APIRouter(prefix="/signals", tags=["signals"])

OPEN_STATUSES = ("ACTIVE", "TP1_HIT", "TP2_HIT")


@router.post("/analyze")
def analyze(body: AnalyzeIn, user_id: str = Depends(get_user_id)):
    if body.market not in INITIAL_MARKETS or body.timeframe not in TIMEFRAMES:
        raise HTTPException(422, "Unknown market or timeframe")
    return State.engine.analyze_manual(user_id, body.market, body.timeframe)


@router.get("")
def list_signals(user_id: str = Depends(get_user_id),
                 status: str = Query("all"),
                 strategy: str = Query("all"),
                 market: str = Query("all"),
                 limit: int = Query(50, le=200)):
    store = State.store
    filters = {"userId": user_id}
    if market != "all":
        filters["market"] = market
    if strategy != "all":
        filters["strategy_id"] = strategy
    sigs = store.list("signals", filters=filters, order_by="candle_time", limit=300)
    if status == "open":
        sigs = [s for s in sigs if s.get("status") in OPEN_STATUSES]
    elif status == "closed":
        sigs = [s for s in sigs if s.get("completed")]
    return {"signals": sigs[:limit], "count": len(sigs),
            "demo": State.provider.is_demo}


@router.get("/{signal_id}")
def get_signal(signal_id: str, user_id: str = Depends(get_user_id)):
    sig = State.store.get("signals", signal_id)
    if not sig or sig.get("userId") != user_id:
        raise HTTPException(404, "Signal not found")
    return {"signal": sig, "demo": State.provider.is_demo}


@router.post("/{signal_id}/action")
def signal_action(signal_id: str, body: SignalActionIn,
                  user_id: str = Depends(get_user_id)):
    store = State.store
    sig = store.get("signals", signal_id)
    if not sig or sig.get("userId") != user_id:
        raise HTTPException(404, "Signal not found")
    if sig.get("user_action"):
        raise HTTPException(409, f"Already recorded: {sig['user_action']}")
    if body.action not in ("entered", "skipped"):
        raise HTTPException(422, "action must be 'entered' or 'skipped'")

    patch = {"user_action": body.action,
             "user_action_at": datetime.utcnow().isoformat()}
    if body.action == "entered":
        entry = body.entry_price or sig["entry"]
        patch.update({
            "user_entry_price": entry,
            "user_lot_size": body.lot_size,
            "user_notes": body.notes,
            "user_screenshot": body.screenshot_uri,
            # user's SL/TP mirror the signal levels; results tracked separately
            "user_sl": sig["sl"],
            "user_trade_result": {"status": sig.get("status", "ACTIVE"),
                                  "r_multiple": _user_r(sig, entry),
                                  "outcome": sig.get("outcome")},
        })
        State.store.create("agent_activity", {
            "userId": None, "kind": "USER",
            "message": f"User entered trade on {sig['market']} ({sig['signal_id']})",
        })
    else:
        patch["user_notes"] = body.notes
        State.store.create("agent_activity", {
            "userId": None, "kind": "USER",
            "message": f"User skipped {sig['signal_id']} - signal continues to be tracked",
        })
        notify(user_id, "SIGNAL_SKIPPED", "Signal skipped",
               f"{sig['signal_id']} will still be tracked to its outcome so the "
               f"agent can learn from it.", signal_id=signal_id)
    updated = store.update("signals", signal_id, patch)
    if updated and updated.get("completed"):
        analyze_single_result(user_id, updated)
    return {"signal": updated}


@router.post("/{signal_id}/manual-result")
def manual_result(signal_id: str, body: ManualResultIn,
                  user_id: str = Depends(get_user_id)):
    """SS24: mark an (EXTRA) signal as manually taken and record the manual
    result. Never mixed with automatically executed trades."""
    store = State.store
    sig = store.get("signals", signal_id)
    if not sig or sig.get("userId") != user_id:
        raise HTTPException(status_code=404, detail="Signal not found")
    manual = {
        "taken": bool(body.taken),
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    for k in ("entry_price", "sl", "tp", "pl", "result", "exit_reason"):
        v = getattr(body, k)
        if v is not None:
            manual[k] = v
    store.update("signals", signal_id, {
        "user_manual": manual,
        "user_action": "manual_extra" if body.taken else sig.get("user_action"),
    })
    if body.taken:
        notify(user_id, "EXTRA_SIGNAL_TAKEN",
               f"Manual trade recorded - {sig.get('market')} {sig.get('direction')}",
               f"{sig.get('strategy_name')} | {sig.get('signal_id')}\n"
               "Tracked separately from automatic execution.")
    return {"ok": True, "user_manual": manual}


def _user_r(sig: dict, entry: float) -> float:
    """User's own R multiple based on THEIR entry vs signal levels."""
    risk = abs(entry - float(sig["sl"]))
    if risk <= 0 or not sig.get("completed"):
        return 0.0
    if sig.get("outcome") == "LOSS":
        return -1.0
    tp = sig.get(f"tp{max(sig.get('tp_hits', 0), 1)}") if sig.get("tp_hits") else None
    if tp is None:
        return 0.0
    move = (float(tp) - entry) * (1 if sig["direction"] == "BUY" else -1)
    return round(move / risk, 3)
