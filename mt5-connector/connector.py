"""ForexMind AI - MT5 PC connector (runs on YOUR Windows PC, next to MT5).

This is the "Manual" execution mode. The connector makes ONLY outbound
HTTPS calls to the cloud - no router ports, no fixed IP needed:

    1. announces itself (hello) so the app shows "PC connected",
    2. polls for queued orders and executes them in your MT5 terminal,
    3. reports each fill back,
    4. pushes deal history so the app can confirm real W/L.

Setup (see README.md): MT5 open + logged in, Python installed, then
    set PAIRING_CODE=FXM-XXXX-XXXX      (Settings -> Order execution)
    set CLOUD_URL=https://forexmind-ai-api.onrender.com
    python connector.py
"""
from __future__ import annotations

import os
import platform
import time
from datetime import datetime, timezone
from typing import Optional

import requests

CLOUD = os.getenv("CLOUD_URL", "https://forexmind-ai-api.onrender.com").rstrip("/")
CODE = os.getenv("PAIRING_CODE", "")
POLL = int(os.getenv("POLL_SECONDS", "20"))
DEALS_EVERY = int(os.getenv("DEALS_EVERY_SECONDS", "60"))
MAGIC_DEFAULT = 20260914


def _h() -> dict:
    return {"X-Pairing": CODE, "Content-Type": "application/json"}


def _post(path: str, body: dict, timeout: int = 30) -> Optional[dict]:
    try:
        r = requests.post(f"{CLOUD}/api/execution/connector/{path}",
                          headers=_h(), json=body, timeout=timeout)
        if r.status_code != 200:
            print(f"[{path}] HTTP {r.status_code}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as exc:
        print(f"[{path}] {type(exc).__name__}: {exc}")
        return None


def _get(path: str, timeout: int = 30) -> Optional[dict]:
    try:
        r = requests.get(f"{CLOUD}/api/execution/connector/{path}",
                         headers=_h(), timeout=timeout)
        if r.status_code != 200:
            print(f"[{path}] HTTP {r.status_code}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as exc:
        print(f"[{path}] {type(exc).__name__}: {exc}")
        return None


# --------------------------- MT5 side ---------------------------
import MetaTrader5 as mt5  # noqa: E402

ALIASES = {
    "NAS100": ["NAS100", "USTEC", "US100", "NAS100.cash", "USTEC.cash", "NDX100"],
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.m"],
}
_symbol_cache: dict = {}
_last_deal_ts = int(time.time() - 7 * 86400)


def mt5_account() -> Optional[dict]:
    a = mt5.account_info()
    if a is None:
        return None
    return {"login": a.login, "server": a.server, "currency": a.currency,
            "balance": a.balance, "equity": a.equity, "leverage": a.leverage}


def resolve_symbol(market: str) -> str:
    if market in _symbol_cache:
        return _symbol_cache[market]
    want = market.upper()
    if mt5.symbol_info(want) is not None:
        _symbol_cache[market] = want
        return want
    names = [s.name for s in (mt5.symbols_get() or [])]
    for cand in ALIASES.get(want, [want]):
        for n in names:
            if n.upper() == cand.upper():
                _symbol_cache[market] = n
                return n
    for cand in ALIASES.get(want, [want]):
        for n in names:
            if n.upper().startswith(cand.upper()):
                _symbol_cache[market] = n
                return n
    raise RuntimeError(f"no MT5 symbol matches {market}")


def normalize_lots(symbol: str, lots: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return lots
    step = info.volume_step or 0.01
    lots = max(info.volume_min or 0.01, min(info.volume_max or 100.0, lots))
    return round(int(lots / step + 1e-9) * step, 8)


def filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    allowed = getattr(info, "filling_mode", 2) if info else 2
    if allowed & 2:
        return mt5.ORDER_FILLING_IOC
    if allowed & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def execute(cmd: dict) -> dict:
    symbol = resolve_symbol(cmd["symbol"])
    info = mt5.symbol_info(symbol)
    if info is not None and not info.visible:
        mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"ok": False, "error": f"no tick for {symbol} (market closed?)"}
    is_buy = cmd["direction"].upper() == "BUY"
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
           "volume": normalize_lots(symbol, float(cmd["lots"])),
           "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
           "price": tick.ask if is_buy else tick.bid,
           "sl": cmd.get("sl"), "tp": cmd.get("tp"),
           "deviation": 30, "magic": int(cmd.get("magic") or MAGIC_DEFAULT),
           "comment": str(cmd.get("signal_id", "fxm"))[:31],
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(symbol)}
    res = mt5.order_send(req)
    if res is None:
        return {"ok": False, "error": f"order_send None: {mt5.last_error()}"}
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        return {"ok": False, "error": f"MT5 retcode {res.retcode}: {res.comment}"}
    return {"ok": True, "ticket": res.order, "position_id": getattr(res, "position", None),
            "volume": res.volume, "price": res.price}


def push_deals() -> None:
    global _last_deal_ts
    ds = mt5.history_deals_get(datetime.fromtimestamp(_last_deal_ts, tz=timezone.utc),
                               datetime.now(timezone.utc)) or ()
    out = [{"time": d.time, "symbol": d.symbol, "type": d.type, "entry": d.entry,
            "volume": d.volume, "price": d.price, "profit": d.profit,
            "commission": d.commission, "swap": d.swap, "comment": d.comment,
            "magic": d.magic, "position_id": d.position_id} for d in ds]
    if not out:
        return
    r = _post("deals", {"deals": out})
    if r is not None:
        _last_deal_ts = int(time.time()) - 5


def main() -> None:
    if not CODE:
        raise SystemExit("Set PAIRING_CODE (see Settings -> Order execution in the app)")
    if not mt5.initialize():
        raise SystemExit(f"MT5 terminal not reachable: {mt5.last_error()}")
    print(f"ForexMind connector - cloud {CLOUD}")
    acct = mt5_account()
    machine = f"{platform.node()} ({platform.system()} {platform.release()})"
    hello = _post("hello", {"machine": machine, "account": acct})
    print(f"paired OK. Account #{acct['login']} on {acct['server']}. Polling every {POLL}s...")
    poll = (hello or {}).get("poll_seconds", POLL)
    last_deals = 0.0
    while True:
        try:
            data = _get("pull")
            if data:
                for cmd in data.get("commands") or []:
                    print(f"-> executing {cmd['signal_id']} {cmd['direction']} "
                          f"{cmd['lots']} {cmd['symbol']}")
                    res = execute(cmd)
                    print(f"   {'FILLED' if res.get('ok') else 'FAILED'}: {res}")
                    _post("ack", {"command_id": cmd["command_id"], **res})
            if time.time() - last_deals > DEALS_EVERY:
                last_deals = time.time()
                push_deals()
        except KeyboardInterrupt:
            print("bye"); return
        except Exception as exc:
            print(f"loop error: {type(exc).__name__}: {exc}")
        time.sleep(poll)


if __name__ == "__main__":
    main()
