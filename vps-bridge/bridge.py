"""ForexMind AI - MT5 bridge (runs on your Windows VPS next to MT5).

Receives trade commands from the cloud backend and executes them on the
MT5 terminal logged in on this machine. Token-protected. Demo-account first.

Run (after installing Python 3.11+ and 'pip install -r requirements.txt',
with MT5 open and logged in, Algo Trading enabled):

    set BRIDGE_TOKEN=choose-a-long-random-string
    python bridge.py

The backend calls:  http://<VPS-IP>:8700/...
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uvicorn import run as uvicorn_run

TOKEN = os.getenv("BRIDGE_TOKEN", "")
PORT = int(os.getenv("BRIDGE_PORT", "8700"))
MAGIC_DEFAULT = 20260914

app = FastAPI(title="ForexMind MT5 Bridge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_mt5_lock = threading.Lock()   # the MT5 API is not thread-safe


def _auth(token: Optional[str]):
    if not TOKEN or token != TOKEN:
        raise HTTPException(401, "bad bridge token")


def _ensure() -> None:
    if not mt5.terminal_info():
        if not mt5.initialize():
            raise HTTPException(503, f"MT5 terminal not reachable: {mt5.last_error()}")


# symbol aliases: brokers name indices differently (Exness: USTEC / US100)
ALIASES = {
    "NAS100": ["NAS100", "USTEC", "US100", "NAS100.cash", "USTEC.cash", "NDX100"],
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.m"],
}


def resolve_symbol(market: str) -> str:
    want = market.upper()
    with _mt5_lock:
        _ensure()
        info = mt5.symbol_info(want)
        if info is not None:
            return want
        candidates = ALIASES.get(want, [want])
        all_syms = mt5.symbols_get() or []
        names = [s.name for s in all_syms]
        for cand in candidates:
            for n in names:
                if n.upper() == cand.upper():
                    return n
        for cand in candidates:          # fuzzy: startswith/contains
            for n in names:
                if n.upper().startswith(cand.upper()):
                    return n
    raise HTTPException(404, f"no MT5 symbol matches {market}")


def normalize_lots(symbol: str, lots: float) -> float:
    with _mt5_lock:
        info = mt5.symbol_info(symbol)
    if info is None:
        return lots
    step = info.volume_step or 0.01
    lots = max(info.volume_min or 0.01, min(info.volume_max or 100.0, lots))
    return round(int(lots / step + 1e-9) * step, 8)


def _filling_mode(symbol: str) -> int:
    """Pick a filling mode the symbol actually allows (Exness is usually IOC)."""
    with _mt5_lock:
        info = mt5.symbol_info(symbol)
    allowed = getattr(info, "filling_mode", 2) if info else 2   # bitmask 1=FOK 2=IOC
    if allowed & 2:
        return mt5.ORDER_FILLING_IOC
    if allowed & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


class OrderIn(BaseModel):
    signal_id: str
    symbol: str            # app market name (EURUSD, NAS100, ...) - resolved on the VPS
    direction: str         # BUY | SELL
    lots: float
    sl: float
    tp: Optional[float] = None
    magic: int = MAGIC_DEFAULT
    deviation: int = 30


@app.get("/health")
def health(x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    with _mt5_lock:
        _ensure()
        t = mt5.terminal_info()
        a = mt5.account_info()
    return {"ok": True, "terminal": {"name": t.name, "connected": t.connected,
                                     "trade_allowed": t.trade_allowed},
            "account": {"login": a.login, "server": a.server, "currency": a.currency,
                        "balance": a.balance, "equity": a.equity, "leverage": a.leverage}}


@app.get("/account")
def account(x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    with _mt5_lock:
        _ensure()
        a = mt5.account_info()
        if a is None:
            raise HTTPException(503, "account info unavailable")
    return {"login": a.login, "server": a.server, "currency": a.currency,
            "balance": a.balance, "equity": a.equity, "leverage": a.leverage,
            "margin_free": a.margin_free}


@app.post("/execute")
def execute(order: OrderIn, x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    symbol = resolve_symbol(order.symbol)
    with _mt5_lock:
        _ensure()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise HTTPException(404, f"symbol_info failed for {symbol}")
        if not info.visible:
            mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise HTTPException(503, f"no tick for {symbol} (market closed?)")
        is_buy = order.direction.upper() == "BUY"
        price = tick.ask if is_buy else tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": normalize_lots(symbol, order.lots),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": order.sl,
            "tp": order.tp,
            "deviation": order.deviation,
            "magic": order.magic,
            "comment": order.signal_id[:31],   # MT5 comment limit
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _filling_mode(symbol),
        }
        res = mt5.order_send(req)
    if res is None:
        raise HTTPException(503, f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(400, f"MT5 retcode {res.retcode}: {res.comment}")
    return {"ok": True, "ticket": res.order, "position_id": getattr(res, "position", None),
            "volume": res.volume, "price": res.price,
            "symbol": symbol, "retcode": res.retcode}


@app.get("/positions")
def positions(x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    with _mt5_lock:
        _ensure()
        ps = mt5.positions_get() or []
    return {"positions": [{"ticket": p.ticket, "symbol": p.symbol, "volume": p.volume,
                           "type": "BUY" if p.type == 0 else "SELL", "price_open": p.price_open,
                           "price_current": p.price_current, "sl": p.sl, "tp": p.tp,
                           "profit": p.profit, "comment": p.comment,
                           "magic": p.magic, "time": p.time} for p in ps]}


@app.post("/close")
def close(body: dict, x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    ticket = int(body.get("ticket") or 0)
    with _mt5_lock:
        _ensure()
        ps = mt5.positions_get(ticket=ticket) or ()
        if not ps:
            raise HTTPException(404, f"position {ticket} not found")
        p = ps[0]
        tick = mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == 0
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
               "volume": p.volume, "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "position": ticket, "price": tick.bid if is_buy else tick.ask,
               "deviation": 30, "magic": p.magic, "comment": "fxm-close",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(p.symbol)}
        res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(400, f"close failed: {res.comment if res else mt5.last_error()}")
    return {"ok": True, "ticket": res.order}


@app.get("/deals")
def deals(since: int = Query(0), x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    frm = datetime.fromtimestamp(max(0, since), tz=timezone.utc)
    with _mt5_lock:
        _ensure()
        ds = mt5.history_deals_get(frm, datetime.now(timezone.utc)) or ()
    return {"deals": [{"ticket": d.ticket, "order": d.order, "time": d.time,
                       "symbol": d.symbol, "type": d.type, "entry": d.entry,
                       "volume": d.volume, "price": d.price, "profit": d.profit,
                       "commission": d.commission, "swap": d.swap, "comment": d.comment,
                       "magic": d.magic, "position_id": d.position_id} for d in ds]}


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set BRIDGE_TOKEN first:  set BRIDGE_TOKEN=your-long-secret")
    print(f"ForexMind MT5 bridge listening on 0.0.0.0:{PORT}")
    uvicorn_run(app, host="0.0.0.0", port=PORT)
