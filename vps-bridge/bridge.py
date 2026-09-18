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

import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

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

_mt5_lock = threading.RLock()  # REENTRANT: /execute & /partial_close call
                               # _filling_mode() while holding the lock; a plain
                               # Lock self-deadlocks the whole bridge (found by
                               # Phase 3 tests - faulthandler stack proof)


def _auth(token: Optional[str]):
    if not TOKEN or token != TOKEN:
        raise HTTPException(401, "bad bridge token")


def _ensure() -> None:
    if not mt5.terminal_info():
        if not mt5.initialize():
            raise HTTPException(503, f"MT5 terminal not reachable: {mt5.last_error()}")


# symbol aliases: brokers name indices differently (Exness: USTEC / US100)
def parse_symbol_aliases(raw):
    """SYMBOL_ALIASES env -> dict. Format: "NAS100=USTEC,US100;XAUUSD=XAUUSD.m".
    Lets broker suffixes/alt names be configured WITHOUT editing code on the
    VPS. Junk segments are skipped; whitespace tolerated; keys uppercased."""
    out = {}
    for seg in (raw or "").split(";"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        market, _, names = seg.partition("=")
        market = market.strip().upper()
        cands = [n.strip() for n in names.split(",") if n.strip()]
        if market and cands:
            out[market] = cands
    return out


ALIASES = {
    "NAS100": ["NAS100", "USTEC", "US100", "NAS100.cash", "USTEC.cash", "NDX100"],
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.m"],
}

# env-configurable broker suffixes (SS29): set e.g.
# SYMBOL_ALIASES="XAUUSD=XAUUSDm;EURUSD=EURUSDm" before starting the bridge.
ALIASES.update(parse_symbol_aliases(os.getenv("SYMBOL_ALIASES", "")))


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
            # MT5 wants 0.0 for "absent", not None (None -> order_send returns
            # None with 'Invalid "tp" argument')
            "sl": float(order.sl or 0.0),
            "tp": float(order.tp or 0.0),
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
    return {"ok": True, "ticket": res.order,
            # Exness fills res.order; res.position may be 0/None on market
            # orders - on hedging accounts the position id equals the
            # opening order ticket (verified live in the P12 matrix)
            "position_id": getattr(res, "position", None) or res.order,
            "volume": res.volume, "price": res.price,
            "symbol": symbol, "retcode": res.retcode}


# ---------------------------------------------------------------------------
# position management (AI trade-manager primitives - cloud drives these)
# ---------------------------------------------------------------------------
def validate_sl_modify(is_buy: bool, new_sl: float, bid: float, ask: float,
                       stops_dist: float) -> Optional[str]:
    """Broker-rule check for an SL move. Pure function (no MT5 calls).

    stops_dist = (trade_stops_level * point) in PRICE units. Returns an error
    string, or None when the new SL is legal on the correct side of price."""
    if new_sl is None or new_sl <= 0:
        return "sl must be positive"
    if is_buy:
        if new_sl >= bid:
            return f"SL {new_sl} must be below current bid {bid} for a BUY position"
        if bid - new_sl < stops_dist:
            return f"SL too close to price - broker minimum distance {stops_dist}"
    else:
        if new_sl <= ask:
            return f"SL {new_sl} must be above current ask {ask} for a SELL position"
        if new_sl - ask < stops_dist:
            return f"SL too close to price - broker minimum distance {stops_dist}"
    return None


def split_partial(volume_req: Optional[float], fraction: Optional[float],
                  pos_vol: float, step: float, vmin: float, vmax: float
                  ) -> Tuple[float, bool, Optional[str]]:
    """Decide the volume for a partial close. Pure function.

    Returns (volume, closes_full, error). Floors to the lot step (never rounds
    a partial UP beyond what was asked), clamps to [vmin, vmax]. A request at
    or above the position volume closes it fully; a position smaller than
    2 x vmin cannot be split."""
    if volume_req is None and fraction is None:
        return 0.0, False, "volume or fraction required"
    if volume_req is None:
        if not (0.0 < float(fraction) < 1.0):
            return 0.0, False, "fraction must be strictly between 0 and 1"
        volume_req = pos_vol * float(fraction)
    volume_req = float(volume_req)
    if volume_req <= 0:
        return 0.0, False, "volume must be positive"
    if pos_vol < 2 * vmin and volume_req < pos_vol:
        return 0.0, False, f"position {pos_vol} lots is too small to split (broker min lot {vmin})"
    # NEVER round a partial UP beyond what was asked: floor to the lot step,
    # cap at vmax; a floored volume below vmin is REJECTED, not bumped up.
    vol = math.floor(volume_req / step + 1e-9) * step if step > 0 else volume_req
    vol = min(vmax, vol)
    if vol >= pos_vol:
        return pos_vol, True, None
    if vol < vmin:
        return 0.0, False, f"requested volume floors below broker minimum {vmin} - increase it"
    return round(vol, 8), False, None


class SLIn(BaseModel):
    ticket: int
    sl: float


@app.post("/modify_sl")
def modify_sl(body: SLIn, x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    with _mt5_lock:
        _ensure()
        ps = mt5.positions_get(ticket=body.ticket) or ()
        if not ps:
            raise HTTPException(404, f"position {body.ticket} not found")
        p = ps[0]
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            raise HTTPException(503, f"no tick for {p.symbol}")
        info = mt5.symbol_info(p.symbol)
        point = float(getattr(info, "point", 0.0) or 0.0)
        stops_dist = (float(getattr(info, "trade_stops_level", 0) or 0)) * point
        err = validate_sl_modify(p.type == 0, body.sl, tick.bid, tick.ask, stops_dist)
        if err:
            raise HTTPException(400, err)
        # TRADE_ACTION_SLTP sets BOTH stops - pass the existing TP through
        # unchanged so a TP is never accidentally cleared.
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
               "position": body.ticket, "sl": body.sl, "tp": p.tp}
        res = mt5.order_send(req)
    if res is None:
        raise HTTPException(503, f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(400, f"MT5 retcode {res.retcode}: {res.comment}")
    return {"ok": True, "ticket": body.ticket, "sl": body.sl, "tp": p.tp}


class PartialCloseIn(BaseModel):
    ticket: int
    volume: Optional[float] = None    # absolute lots to close
    fraction: Optional[float] = None  # 0<f<1 of the position volume


@app.post("/partial_close")
def partial_close(body: PartialCloseIn, x_bridge_token: Optional[str] = Header(None)):
    _auth(x_bridge_token)
    with _mt5_lock:
        _ensure()
        ps = mt5.positions_get(ticket=body.ticket) or ()
        if not ps:
            raise HTTPException(404, f"position {body.ticket} not found")
        p = ps[0]
        info = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if info is None or tick is None:
            raise HTTPException(503, f"symbol/tick unavailable for {p.symbol}")
        vol, closes_full, err = split_partial(body.volume, body.fraction,
                                              float(p.volume),
                                              float(info.volume_step or 0.01),
                                              float(info.volume_min or 0.01),
                                              float(info.volume_max or 100.0))
        if err:
            raise HTTPException(400, err)
        is_buy = p.type == 0
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
               "volume": vol,
               "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "position": body.ticket,
               "price": tick.bid if is_buy else tick.ask,
               "deviation": 30, "magic": p.magic, "comment": "fxm-manage",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(p.symbol)}
        res = mt5.order_send(req)
    if res is None:
        raise HTTPException(503, f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(400, f"MT5 retcode {res.retcode}: {res.comment}")
    return {"ok": True, "ticket": body.ticket, "closed_volume": vol,
            "remaining_volume": round(float(p.volume) - vol, 8),
            "closes_full": closes_full, "price": res.price}


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
