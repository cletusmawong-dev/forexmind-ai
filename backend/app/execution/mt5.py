"""Bridge client + execution policy + real deal sync.

All requests carry X-Bridge-Token. Nothing here can ever block or break
signal generation: every entrypoint fails soft with an honest activity log.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import settings
from ..db.store import get_store

MAGIC = 20260914   # identifies ForexMind trades on the MT5 account

# $ value per pip per 1.00 lot (mirrors frontend/src/lib/position.ts)
PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1, "NAS100": 1.0}
PIP_VALUE = {"EURUSD": 10.0, "GBPUSD": 10.0, "XAUUSD": 10.0}   # USD per pip per lot
LOT_STEP = 0.01

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# lot sizing (same math as the app's Position Size card)
# ---------------------------------------------------------------------------
def calc_lot(market: str, entry: float, sl: float, balance: float, risk_pct: float) -> float:
    pip = PIP_SIZE.get(market)
    if pip is None or entry <= 0 or sl <= 0 or balance <= 0 or risk_pct <= 0:
        return LOT_STEP
    pip_value = PIP_VALUE.get(market)
    if pip_value is None and market == "USDJPY":
        pip_value = 1000.0 / entry          # JPY quote conversion
    if not pip_value:
        return LOT_STEP
    pips = abs(entry - sl) / pip
    if pips <= 0:
        return LOT_STEP
    risk_amount = balance * risk_pct / 100.0
    raw = risk_amount / (pips * pip_value)
    # epsilon guards the float edge (0.049999... must floor to 0.05, not 0.04)
    return max(LOT_STEP, math.floor(raw * 100 + 1e-9) / 100)


# ---------------------------------------------------------------------------
# kill switch + counters (stored on the user's agent_goals doc)
# ---------------------------------------------------------------------------
def _goals_doc(user_id: str) -> Optional[dict]:
    docs = get_store().list("agent_goals", filters={"userId": user_id}, limit=1)
    return docs[0] if docs else None


def execution_enabled(user_id: str) -> bool:
    doc = _goals_doc(user_id)
    return bool((doc or {}).get("execution_enabled", True))


def set_execution_enabled(user_id: str, enabled: bool) -> None:
    store = get_store()
    doc = _goals_doc(user_id)
    if doc:
        store.update("agent_goals", doc["id"], {"execution_enabled": enabled})
    else:
        store.create("agent_goals", {"userId": user_id, "execution_enabled": enabled})


def _executed_today(user_id: str) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docs = get_store().list("signals", filters={"userId": user_id, "day": day}, limit=200)
    return sum(1 for d in docs if d.get("mt5_ticket"))


# ---------------------------------------------------------------------------
# bridge HTTP
# ---------------------------------------------------------------------------
def _headers() -> Dict[str, str]:
    return {"X-Bridge-Token": settings.bridge_token, "Content-Type": "application/json"}


def bridge_get(path: str, timeout: int = 8) -> Optional[dict]:
    if not settings.bridge_url:
        return None
    try:
        r = requests.get(f"{settings.bridge_url}{path}", headers=_headers(), timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def bridge_post(path: str, payload: dict, timeout: int = 15) -> Optional[dict]:
    if not settings.bridge_url:
        return None
    try:
        r = requests.post(f"{settings.bridge_url}{path}", headers=_headers(),
                          json=payload, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {"http_status": r.status_code}
    except Exception as exc:
        return {"error": f"bridge unreachable: {type(exc).__name__}"}


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def execute_signal(signal: dict, user_id: str) -> None:
    """Fire a market order at the bridge the moment a signal qualifies.

    Called from the signal engine right after the NEW_SIGNAL notification.
    Never raises; writes execution_status back onto the signal doc."""
    if settings.execution_mode != "mt5_bridge" or not settings.bridge_url:
        return   # execution off - signals stay advisory (honest default)

    store = get_store()
    log = lambda msg, kind="EXEC": store.create("agent_activity", {
        "userId": None, "kind": kind, "message": msg, "market": signal.get("market")})

    if not execution_enabled(user_id):
        log("Execution skipped - kill switch is ON (Settings). Signal is advisory only.")
        return
    if _executed_today(user_id) >= settings.execution_max_trades_per_day:
        log(f"Execution skipped - daily cap reached ({settings.execution_max_trades_per_day}/day).")
        return

    try:
        acct = bridge_get("/account")
        if not acct or "balance" not in acct:
            log("Execution skipped - MT5 bridge offline (signal NOT sent to broker).", kind="EXEC_WARN")
            store.update("signals", signal["id"], {"execution_status": "SKIPPED_BRIDGE_OFFLINE"})
            return
        balance = float(acct["balance"]) or 0.0

        entry, sl = float(signal["entry"]), float(signal["sl"])
        risk_pct = min(float(((_goals_doc(user_id) or {}).get("risk_per_trade_pct", 1.0)) or 1.0),
                       settings.execution_risk_pct_cap)
        lots = calc_lot(signal["market"], entry, sl, balance, risk_pct)

        tp = signal.get(f"tp{max(1, min(3, settings.execution_tp_level))}") or signal.get("tp1")
        res = bridge_post("/execute", {
            "signal_id": signal["signal_id"], "symbol": signal["market"],
            "direction": signal["direction"], "lots": lots,
            "sl": sl, "tp": tp, "magic": MAGIC,
        })
        if res and res.get("ok"):
            store.update("signals", signal["id"], {
                "execution_status": "SUBMITTED", "mt5_ticket": res.get("ticket"),
                "mt5_position_id": res.get("position_id"), "mt5_volume": res.get("volume"),
                "mt5_open_price": res.get("price"), "mt5_tp_used": tp,
                "mt5_executed_at": datetime.now(timezone.utc).isoformat(),
            })
            log(f"MT5 EXECUTED {signal['market']} {signal['direction']} "
                f"{res.get('volume')} lots @ {res.get('price')} (ticket {res.get('ticket')}).")
        else:
            err = (res or {}).get("error") or (res or {}).get("detail") or f"HTTP {(res or {}).get('http_status')}"
            store.update("signals", signal["id"], {"execution_status": "FAILED", "mt5_error": str(err)[:200]})
            log(f"Execution FAILED on bridge - {err}", kind="EXEC_WARN")
    except Exception as exc:
        try:
            store.update("signals", signal["id"], {"execution_status": "FAILED",
                                                   "mt5_error": f"{type(exc).__name__}"[:200]})
        except Exception:
            pass
        log(f"Execution error - {type(exc).__name__}", kind="EXEC_WARN")


# ---------------------------------------------------------------------------
# real W/L sync from broker deal history
# ---------------------------------------------------------------------------
def sync_deals(user_id: str) -> int:
    """Pull MT5 deal history since the last sync; confirm closed trades.

    Returns how many signals were updated with broker-confirmed results."""
    if settings.execution_mode != "mt5_bridge" or not settings.bridge_url:
        return 0
    store = get_store()
    doc = _goals_doc(user_id) or {}
    since = int(doc.get("mt5_deal_sync_ts") or (time.time() - 7 * 86400))
    data = bridge_get(f"/deals?since={since}", timeout=20)
    if not data:
        return 0
    deals: List[dict] = data.get("deals") or []

    # group by position: entry deal gives the comment (signal_id), exits give P/L
    positions: Dict[Any, Dict[str, Any]] = {}
    for d in deals:
        if int(d.get("magic") or 0) != MAGIC:
            continue
        pos = positions.setdefault(d.get("position_id"), {"entry": None, "exits": []})
        if d.get("entry") == 0:
            pos["entry"] = d
        else:
            pos["exits"].append(d)

    updated = 0
    now_ts = int(time.time())
    for pos_id, p in positions.items():
        if not p["exits"] or not p["entry"]:
            continue
        sig_id = (p["entry"].get("comment") or "").strip()
        if not sig_id:
            continue
        docs = store.list("signals", filters={"userId": user_id, "signal_id": sig_id}, limit=1)
        if not docs or docs[0].get("mt5_confirmed"):
            continue
        # entry deal carries (part of) the commission - it must count in P/L
        pl = round(sum(float(e.get("profit") or 0) + float(e.get("commission") or 0)
                       + float(e.get("swap") or 0) for e in [p["entry"]] + p["exits"]), 2)
        vol = sum(float(e.get("volume") or 0) for e in p["exits"])
        close_price = p["exits"][-1].get("price")
        store.update("signals", docs[0]["id"], {
            "completed": True, "status": "CLOSED_MT5",
            "outcome": "WIN" if pl > 0 else ("LOSS" if pl < 0 else "BREAK_EVEN"),
            "mt5_confirmed": True, "mt5_pl": pl, "mt5_close_price": close_price,
            "mt5_closed_at": datetime.now(timezone.utc).isoformat(),
        })
        updated += 1
        store.create("notifications", {
            "userId": user_id, "type": "TRADE_COMPLETED",
            "title": f"{'WIN' if pl > 0 else 'LOSS'} - {sig_id} (MT5)",
            "body": f"Broker-confirmed result: {pl:+.2f} USD on {vol} lots.",
            "signal_id": docs[0]["id"], "meta": {"mt5_pl": pl, "position_id": pos_id},
            "read": False,
        })

    gdoc = _goals_doc(user_id)
    if gdoc:
        store.update("agent_goals", gdoc["id"], {"mt5_deal_sync_ts": now_ts})
    return updated


def status(user_id: str) -> dict:
    """Honest execution status for Settings."""
    goals = _goals_doc(user_id) or {}
    mode_on = settings.execution_mode == "mt5_bridge" and bool(settings.bridge_url)
    acct = bridge_get("/account") if mode_on else None
    return {
        "mode": settings.execution_mode,
        "enabled": execution_enabled(user_id),
        "bridge_configured": mode_on,
        "bridge_online": bool(acct),
        "account": {k: acct.get(k) for k in ("login", "server", "currency", "balance",
                                             "equity", "leverage")} if acct else None,
        "trades_today": _executed_today(user_id) if mode_on else 0,
        "max_per_day": settings.execution_max_trades_per_day,
        "risk_cap_pct": settings.execution_risk_pct_cap,
        "tp_level": settings.execution_tp_level,
        "last_deal_sync": goals.get("mt5_deal_sync_ts"),
        "note": "Demo-account execution via your VPS bridge. Real fills, real W/L."
                if mode_on else "Execution off - signals are advisory only.",
    }
