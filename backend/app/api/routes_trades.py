"""Trade journal routes (SPEC §17, §31). Manual trades only - the app never
executes orders (SPEC §42)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.schemas import TradeIn, TradePatch
from ..state import State
from .deps import get_user_id

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
def list_trades(user_id: str = Depends(get_user_id), limit: int = Query(100, le=300)):
    trades = State.store.list("trades", filters={"userId": user_id},
                              order_by="createdAt", limit=limit)
    return {"trades": trades, "demo": State.provider.is_demo}


@router.post("")
def create_trade(body: TradeIn, user_id: str = Depends(get_user_id)):
    store = State.store
    if body.direction not in ("BUY", "SELL"):
        raise HTTPException(422, "direction must be BUY or SELL")
    risk = abs(body.entry_price - body.sl)
    if risk <= 0:
        raise HTTPException(422, "Stop loss must differ from entry")
    doc = store.create("trades", {
        "userId": user_id,
        "source": "MANUAL" if not body.signal_id else "SIGNAL",
        "signal_id": body.signal_id,
        "market": body.market, "direction": body.direction,
        "entry_price": body.entry_price, "sl": body.sl, "tp": body.tp,
        "risk": risk, "lot_size": body.lot_size, "notes": body.notes,
        "status": "OPEN", "r_multiple": 0.0,
    })
    return {"trade": doc}


@router.patch("/{trade_id}")
def patch_trade(trade_id: str, body: TradePatch, user_id: str = Depends(get_user_id)):
    store = State.store
    t = store.get("trades", trade_id)
    if not t or t.get("userId") != user_id:
        raise HTTPException(404, "Trade not found")
    patch = {}
    if body.notes is not None:
        patch["notes"] = body.notes
    if body.exit_price is not None:
        exit_px = float(body.exit_price)
        direction = 1 if t["direction"] == "BUY" else -1
        r = round((exit_px - float(t["entry_price"])) * direction / float(t["risk"]), 3)
        patch.update({"exit_price": exit_px, "status": "CLOSED",
                      "r_multiple": r,
                      "outcome": "WIN" if r > 0 else ("LOSS" if r < 0 else "BREAKEVEN")})
    if body.status:
        patch["status"] = body.status
    updated = store.update("trades", trade_id, patch)
    return {"trade": updated}


@router.get("/{trade_id}/analysis")
def trade_analysis(trade_id: str, user_id: str = Depends(get_user_id)):
    store = State.store
    t = store.get("trades", trade_id)
    if not t or t.get("userId") != user_id:
        raise HTTPException(404, "Trade not found")
    analysis = None
    if t.get("signal_id"):
        sig = store.get("signals", t["signal_id"])
        if sig:
            analysis = sig.get("result_analysis")
    if not analysis:
        analysis = {"message": "Analysis will be generated when the linked signal "
                               "completes, or once the trade is closed."}
    return {"trade": t, "analysis": analysis}
