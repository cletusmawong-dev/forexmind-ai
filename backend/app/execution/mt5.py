"""Execution routing: off | manual (user's PC connector) | vps bridge.

Two execution transports, one policy:
  * vps    - cloud calls the user's Windows-VPS bridge over HTTP (MT5_BRIDGE_URL).
  * manual - the user runs the ForexMind connector on ANY Windows PC where the
             MT5 terminal is open. The connector DIALS OUT to the cloud
             (no router ports needed), picks up queued orders, executes them
             through MT5 and pushes fills + deal history back. A pairing code
             (Settings) binds the connector to the account.

Shared safety rails: kill switch, daily cap, risk cap, TP level. Every
failure degrades to "signal only" with an honest activity log.
"""
from __future__ import annotations

import math
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import settings
from ..db.store import get_store

MAGIC = 20260914          # identifies ForexMind trades on the MT5 account
CONNECTOR_TIMEOUT_S = 90  # connector considered offline after this many seconds
COMMAND_TTL_S = 900       # queued manual orders expire after 15 min offline

PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1, "NAS100": 1.0}
PIP_VALUE = {"EURUSD": 10.0, "GBPUSD": 10.0, "XAUUSD": 10.0}   # USD per pip per 1.0 lot
LOT_STEP = 0.01
MODES = ("off", "manual", "vps")

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
        pip_value = 1000.0 / entry
    if not pip_value:
        return LOT_STEP
    pips = abs(entry - sl) / pip
    if pips <= 0:
        return LOT_STEP
    raw = (balance * risk_pct / 100.0) / (pips * pip_value)
    # epsilon guards the float edge (0.049999... must floor to 0.05, not 0.04)
    return max(LOT_STEP, math.floor(raw * 100 + 1e-9) / 100)


# ---------------------------------------------------------------------------
# risk lot-mode (user setting: low / medium / high)
# medium = exactly the previous behavior; the multiplier scales the computed
# lot AFTER the risk-% math, floored to the broker step (never below 0.01)
# ---------------------------------------------------------------------------
LOT_MODE_MULT = {"low": 0.5, "medium": 1.0, "high": 1.5}


def _lot_mode(user_id: str) -> str:
    try:
        docs = get_store().list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
        mode = str((docs[0] if docs else {}).get("lot_mode") or "medium").lower()
        return mode if mode in LOT_MODE_MULT else "medium"
    except Exception:
        return "medium"


def apply_lot_mode(lots: float, mode: str) -> float:
    mult = LOT_MODE_MULT.get(mode, 1.0)
    return max(LOT_STEP, math.floor(lots * mult * 100 + 1e-9) / 100)


# ---------------------------------------------------------------------------
# goals doc + kill switch + mode
# ---------------------------------------------------------------------------
def _goals_doc(user_id: str) -> Optional[dict]:
    docs = get_store().list("agent_goals", filters={"userId": user_id}, limit=1)
    return docs[0] if docs else None


def _goals_update(user_id: str, patch: dict) -> None:
    store = get_store()
    doc = _goals_doc(user_id)
    if doc:
        store.update("agent_goals", doc["id"], patch)
    else:
        store.create("agent_goals", {"userId": user_id, **patch})


def execution_enabled(user_id: str) -> bool:
    """Kill switch. SAFE DEFAULT: blocked (spec: EXECUTION_KILL_SWITCH=true).
    Users with no explicit setting are treated as killed."""
    return bool((_goals_doc(user_id) or {}).get(
        "execution_enabled", not settings.execution_kill_switch_default))


def set_execution_enabled(user_id: str, enabled: bool) -> None:
    _goals_update(user_id, {"execution_enabled": enabled})


def user_mode(user_id: str) -> str:
    mode = (_goals_doc(user_id) or {}).get("execution_mode")
    if mode == "mt5_bridge":        # legacy env name
        return "vps"
    if mode in MODES:
        return mode
    return "vps" if settings.execution_mode == "mt5_bridge" else "off"   # env default


def set_mode(user_id: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    if mode == "vps" and not (settings.execution_mode == "mt5_bridge" and settings.bridge_url):
        raise PermissionError("VPS bridge not configured yet - finish the VPS setup first")
    _goals_update(user_id, {"execution_mode": mode})
    return mode


# ---------------------------------------------------------------------------
# pairing (manual connector)
# ---------------------------------------------------------------------------
def ensure_pairing_code(user_id: str) -> str:
    doc = _goals_doc(user_id) or {}
    code = doc.get("mt5_pairing_code")
    if code:
        return code
    code = f"FXM-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    _goals_update(user_id, {"mt5_pairing_code": code})
    return code


def user_for_pairing(code: str) -> Optional[str]:
    if not code:
        return None
    docs = get_store().list("agent_goals", filters={"mt5_pairing_code": code}, limit=1)
    return docs[0].get("userId") if docs else None


def _connector_seen(user_id: str) -> Optional[float]:
    return (_goals_doc(user_id) or {}).get("mt5_connector_seen")


def connector_online(user_id: str) -> bool:
    seen = _connector_seen(user_id)
    return bool(seen and (time.time() - seen) < CONNECTOR_TIMEOUT_S)


def _touch_connector(user_id: str, machine: Optional[str] = None,
                     account: Optional[dict] = None) -> None:
    patch: Dict[str, Any] = {"mt5_connector_seen": time.time()}
    if machine:
        patch["mt5_connector_machine"] = machine[:80]
    if account:
        patch["mt5_account"] = account
    _goals_update(user_id, patch)


# ---------------------------------------------------------------------------
# caps
# ---------------------------------------------------------------------------
def _executed_today(user_id: str) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docs = get_store().list("signals", filters={"userId": user_id, "day": day}, limit=200)
    return sum(1 for d in docs if d.get("mt5_ticket") or d.get("mt5_command_id"))


def _log(msg: str, market: Optional[str] = None, kind: str = "EXEC") -> None:
    get_store().create("agent_activity", {"userId": None, "kind": kind,
                                          "message": msg, "market": market})


# ---------------------------------------------------------------------------
# bridge transport (vps mode)
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
# execution entry point (called by the signal engine)
# ---------------------------------------------------------------------------
_exec_lock = threading.Lock()


def _setup_already_sent(store, signal: dict) -> Optional[str]:
    """One broker order per unique setup (market + direction + candle), no
    matter how many user copies of the signal exist.

    2026-09-21 incident: multi-user signal delivery gives EVERY user their own
    signal doc (by design), and every VPS-mode copy executed on the ONE shared
    bridge account -> every Sunday-open signal was placed TWICE at the broker
    (7s apart, same signal_id comment). Only the account resource is shared:
    in manual-PC mode each user's own connector still executes their copy.
    """
    market, direction = signal.get("market"), signal.get("direction")
    candle_time = str(signal.get("candle_time") or "")
    if not market or not direction or not candle_time:
        return None     # incomplete key -> never block on guesses
    docs = store.list("signals", filters={
        "market": market, "direction": direction, "candle_time": candle_time},
        limit=20)
    for s in docs:
        if s.get("id") == signal.get("id"):
            continue
        if s.get("execution_status") in ("SUBMITTED", "FILLED", "EXECUTING"):
            return str(s.get("signal_id") or s.get("id"))
    return None


def execute_signal(signal: dict, user_id: str) -> None:
    """Route a qualifying signal to the active execution transport.

    Never raises; writes execution_status back onto the signal doc."""
    mode = user_mode(user_id)
    if mode == "off":
        return   # advisory signals - honest default

    store = get_store()
    market = signal.get("market")

    if not execution_enabled(user_id):
        _log("Execution skipped - kill switch is ON (Settings). Signal is advisory only.", market)
        return

    # SS22/SS26: daily walls block automatic NEW entries only - the signal was
    # already generated, recorded and notified (EXTRA SIGNAL). Never the reverse.
    try:
        from ..engine.daily import is_entry_blocked
        blocked, breason, st = is_entry_blocked(user_id)
    except Exception:
        blocked, breason, st = False, None, {}
    if blocked:
        store.update("signals", signal["id"], {
            "execution_status": "EXTRA_SIGNAL_NOT_ENTERED",
            "entry_blocked_reason": breason,
            "daily_pl_at_signal": st.get("total_usd")})
        label = "profit target" if "profit target" in (breason or "") else "loss limit"
        _log(f"Automatic entry disabled - daily {label} reached "
             f"(today {st.get('total_usd', 0):+,.2f} USD). Signal kept as EXTRA.",
             market, kind="EXEC_WARN")
        cancel_pending_entries(user_id, breason or "daily wall")
        _enforce_on_loss_limit(user_id, st)
        return

    if _executed_today(user_id) >= settings.execution_max_trades_per_day:
        _log(f"Execution skipped - daily cap reached ({settings.execution_max_trades_per_day}/day).", market)
        return

    try:
        entry, sl = float(signal["entry"]), float(signal["sl"])
        risk_pct = min(float(((_goals_doc(user_id) or {}).get("risk_per_trade_pct", 1.0)) or 1.0),
                       settings.execution_risk_pct_cap)
        tp = signal.get(f"tp{max(1, min(3, settings.execution_tp_level))}") or signal.get("tp1")

        if mode == "vps":
            acct = bridge_get("/account")
            if not acct or "balance" not in acct:
                store.update("signals", signal["id"], {"execution_status": "SKIPPED_BRIDGE_OFFLINE"})
                _log("Execution skipped - VPS bridge offline (signal NOT sent to broker).",
                     market, kind="EXEC_WARN")
                return
            with _exec_lock:
                dup = _setup_already_sent(store, signal)
                if dup:
                    store.update("signals", signal["id"], {
                        "execution_status": "SKIPPED_SETUP_ALREADY_EXECUTED",
                        "mt5_note": f"same setup already sent via {dup}"})
                    _log("Execution skipped - this exact setup (same market, "
                         f"direction and candle) is already on the account via {dup}. "
                         "One broker order per setup.", market, kind="EXEC_WARN")
                    return
                store.update("signals", signal["id"], {"execution_status": "EXECUTING"})
            lots = calc_lot(market, entry, sl, float(acct["balance"]) or 0.0, risk_pct)
            _mode = _lot_mode(user_id)
            lots = apply_lot_mode(lots, _mode)
            res = bridge_post("/execute", {
                "signal_id": signal["signal_id"], "symbol": market,
                "direction": signal["direction"], "lots": lots, "sl": sl, "tp": tp,
                "magic": MAGIC})
            if res and res.get("ok"):
                _apply_fill(store, signal, res, lots)
                _log(f"MT5 EXECUTED {market} {signal['direction']} {res.get('volume')} lots "
                     f"@ {res.get('price')} (ticket {res.get('ticket')})."
                     + (f" Risk level: {_mode}." if _mode != "medium" else ""), market)
            else:
                err = (res or {}).get("error") or (res or {}).get("detail") or \
                      f"HTTP {(res or {}).get('http_status')}"
                store.update("signals", signal["id"],
                             {"execution_status": "FAILED", "mt5_error": str(err)[:200]})
                _log(f"Execution FAILED on bridge - {err}", market, kind="EXEC_WARN")

        else:   # manual: queue for the PC connector
            if not connector_online(user_id) and not _connector_seen(user_id):
                store.update("signals", signal["id"], {"execution_status": "SKIPPED_PC_NEVER_CONNECTED"})
                _log("Execution skipped - no MT5 PC has ever connected (pair it in Settings).",
                     market, kind="EXEC_WARN")
                return
            bal = (float(((_goals_doc(user_id) or {}).get("mt5_account") or {}).get("balance") or 0)
                   or float(((_goals_doc(user_id) or {}).get("account_balance")) or 0))
            if bal <= 0:
                store.update("signals", signal["id"], {"execution_status": "SKIPPED_NO_BALANCE"})
                _log("Execution skipped - no MT5 account balance known yet (connect the PC once).",
                     market, kind="EXEC_WARN")
                return
            lots = calc_lot(market, entry, sl, bal, risk_pct)
            _mode = _lot_mode(user_id)
            lots = apply_lot_mode(lots, _mode)
            cmd = store.create("exec_commands", {
                "userId": user_id, "signal_doc_id": signal["id"],
                "signal_id": signal["signal_id"], "status": "PENDING",
                "payload": {"signal_id": signal["signal_id"], "symbol": market,
                            "direction": signal["direction"], "lots": lots,
                            "sl": sl, "tp": tp, "magic": MAGIC},
                "createdAt": datetime.now(timezone.utc).isoformat()})
            store.update("signals", signal["id"],
                         {"execution_status": "QUEUED_PC", "mt5_command_id": cmd["id"],
                          "mt5_volume": lots})
            _log(f"Order queued for your MT5 PC ({market} {signal['direction']} {lots} lots).",
                 market)
    except Exception as exc:
        try:
            store.update("signals", signal["id"], {"execution_status": "FAILED",
                                                   "mt5_error": type(exc).__name__[:200]})
        except Exception:
            pass
        _log(f"Execution error - {type(exc).__name__}", market, kind="EXEC_WARN")


def _apply_fill(store, signal: dict, res: dict, lots: float) -> None:
    store.update("signals", signal["id"], {
        "execution_status": "SUBMITTED", "mt5_ticket": res.get("ticket"),
        "mt5_position_id": res.get("position_id"), "mt5_volume": res.get("volume") or lots,
        "mt5_open_price": res.get("price"),
        "mt5_symbol": res.get("symbol"),   # broker-resolved name (suffix/alias included)
        "mt5_executed_at": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# position management primitives (Phase 3 - the AI trade manager drives these)
# ---------------------------------------------------------------------------
def modify_sl(user_id: str, ticket: int, new_sl: float, reason: str = "") -> dict:
    """Move the SL of an open position. vps: immediate bridge call.
    manual: queued for the PC connector (15-min TTL like entries).
    off: refused. Never raises; the caller decides what to do with the result."""
    mode = user_mode(user_id)
    store = get_store()
    try:
        if mode == "off":
            _log("SL change skipped - execution is off.", kind="EXEC_WARN")
            return {"ok": False, "error": "execution off"}
        note = f" ({reason})" if reason else ""
        if mode == "vps":
            res = bridge_post("/modify_sl",
                              {"ticket": int(ticket), "sl": float(new_sl)}) or {}
            if res.get("ok"):
                _log(f"MT5 SL moved to {new_sl} on #{ticket}{note}.", kind="EXEC")
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 SL change FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
            return res
        cmd = store.create("exec_commands", {
            "userId": user_id, "type": "modify_sl", "status": "PENDING",
            "payload": {"type": "modify_sl", "ticket": int(ticket),
                        "sl": float(new_sl), "reason": reason[:120]},
            "createdAt": datetime.now(timezone.utc).isoformat()})
        _log(f"SL change queued for your MT5 PC (#{ticket} -> {new_sl}){note}.", kind="EXEC")
        return {"ok": True, "queued": True, "command_id": cmd["id"]}
    except Exception as exc:
        _log(f"SL change error - {type(exc).__name__}", kind="EXEC_WARN")
        return {"ok": False, "error": type(exc).__name__}


def partial_close_position(user_id: str, ticket: int, volume: float = None,
                           fraction: float = None, reason: str = "") -> dict:
    """Close part of an open position (volume in lots, or fraction 0<f<1).
    Same transports as modify_sl. Never raises."""
    mode = user_mode(user_id)
    store = get_store()
    try:
        if mode == "off":
            _log("Partial close skipped - execution is off.", kind="EXEC_WARN")
            return {"ok": False, "error": "execution off"}
        payload = {"ticket": int(ticket)}
        if volume is not None:
            payload["volume"] = float(volume)
        if fraction is not None:
            payload["fraction"] = float(fraction)
        note = f" ({reason})" if reason else ""
        if mode == "vps":
            res = bridge_post("/partial_close", payload) or {}
            if res.get("ok"):
                _log(f"MT5 partial close #{ticket}: closed {res.get('closed_volume')} "
                     f"lots, {res.get('remaining_volume')} left{note}.", kind="EXEC")
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 partial close FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
            return res
        cmd = store.create("exec_commands", {
            "userId": user_id, "type": "partial_close", "status": "PENDING",
            "payload": {"type": "partial_close", **payload, "reason": reason[:120]},
            "createdAt": datetime.now(timezone.utc).isoformat()})
        _log(f"Partial close queued for your MT5 PC (#{ticket}){note}.", kind="EXEC")
        return {"ok": True, "queued": True, "command_id": cmd["id"]}
    except Exception as exc:
        _log(f"Partial close error - {type(exc).__name__}", kind="EXEC_WARN")
        return {"ok": False, "error": type(exc).__name__}


# ---------------------------------------------------------------------------
# daily-wall helpers (SS22 cancel pendings / SS27 optional close-all)
# ---------------------------------------------------------------------------
def cancel_pending_entries(user_id: str, reason: str) -> int:
    """Withdraw still-pending automatic ENTRY orders (SS22). Management
    commands (modify_sl / partial_close / close_all) are never touched."""
    store = get_store()
    n = 0
    for c in store.list("exec_commands", filters={"userId": user_id,
                                                  "status": "PENDING"}, limit=20):
        ctype = (c.get("type") or "execute").lower()
        if ctype != "execute":
            continue
        store.update("exec_commands", c["id"], {
            "status": "CANCELLED_DAILY_WALL", "cancel_reason": str(reason)[:120],
            "cancelledAt": datetime.now(timezone.utc).isoformat()})
        sig = store.get("signals", c.get("signal_doc_id") or "")
        if sig and sig.get("userId") == user_id:
            store.update("signals", sig["id"], {
                "execution_status": "CANCELLED_DAILY_WALL"})
        n += 1
    if n:
        _log(f"{n} pending entr{'y' if n == 1 else 'ies'} withdrawn "
             f"({reason}) - no duplicate orders.", kind="EXEC_WARN")
    return n


def _enforce_on_loss_limit(user_id: str, st: dict) -> None:
    """SS27 Option B (opt-in): close open positions when the daily LOSS limit
    is hit. Default is Option A (stop entries only). Runs once per day."""
    store = get_store()
    if not st.get("hit_loss"):
        return
    goals = _goals_doc(user_id) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if goals.get("on_loss_close_done_day") == today:
        return
    risk_doc = store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1)
    risk = risk_doc[0] if risk_doc else {}
    from ..engine.daily import on_loss_limit_mode
    if on_loss_limit_mode(risk) != "stop_and_close":
        return
    _goals_update(user_id, {"on_loss_close_done_day": today})   # once, even on failure
    mode = user_mode(user_id)
    closed = 0
    if mode == "vps":
        pos = bridge_get("/positions", timeout=8) or {}
        for p in (pos.get("positions") or []):
            if int(p.get("magic") or 0) != MAGIC:
                continue
            res = bridge_post("/close", {"ticket": p.get("ticket")}) or {}
            if res.get("ok"):
                closed += 1
            else:
                _log(f"Loss-limit close FAILED on #{p.get('ticket')} - "
                     f"{res.get('error') or res.get('detail')}", kind="EXEC_WARN")
    elif mode == "manual":
        if connector_online(user_id):
            store.create("exec_commands", {
                "userId": user_id, "type": "close_all", "status": "PENDING",
                "payload": {"type": "close_all", "reason": "daily loss limit"},
                "createdAt": datetime.now(timezone.utc).isoformat()})
            _log("Close-all queued for your MT5 PC (daily loss limit protection).",
                 kind="EXEC_WARN")
            closed = -1   # queued, not confirmed
        else:
            _log("Loss limit: close-all NOT possible - PC connector offline.",
                 kind="EXEC_WARN")
    if closed > 0:
        _log(f"Loss-limit protection closed {closed} position(s).", kind="EXEC_WARN")


def close_position(user_id: str, ticket: int, reason: str = "") -> dict:
    """Close a full open position (AI EXIT + loss-limit protection path).
    Same transports as modify_sl. Never raises."""
    mode = user_mode(user_id)
    store = get_store()
    try:
        if mode == "off":
            _log("Close skipped - execution is off.", kind="EXEC_WARN")
            return {"ok": False, "error": "execution off"}
        note = f" ({reason})" if reason else ""
        if mode == "vps":
            res = bridge_post("/close", {"ticket": int(ticket)}) or {}
            if res.get("ok"):
                _log(f"MT5 position #{ticket} closed{note}.", kind="EXEC")
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 close FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
            return res
        cmd = store.create("exec_commands", {
            "userId": user_id, "type": "close_full", "status": "PENDING",
            "payload": {"type": "close_full", "ticket": int(ticket),
                        "reason": reason[:120]},
            "createdAt": datetime.now(timezone.utc).isoformat()})
        _log(f"Close queued for your MT5 PC (#{ticket}){note}.", kind="EXEC")
        return {"ok": True, "queued": True, "command_id": cmd["id"]}
    except Exception as exc:
        _log(f"Close error - {type(exc).__name__}", kind="EXEC_WARN")
        return {"ok": False, "error": type(exc).__name__}


# ---------------------------------------------------------------------------
# connector command lifecycle (manual mode)
# ---------------------------------------------------------------------------
def pull_commands(user_id: str) -> List[dict]:
    """Hand the connector its pending orders (oldest first) and mark them SENT."""
    store = get_store()
    docs = store.list("exec_commands", filters={"userId": user_id, "status": "PENDING"}, limit=5)
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for c in docs:
        store.update("exec_commands", c["id"], {"status": "SENT", "sentAt": now})
        out.append({"command_id": c["id"], **(c.get("payload") or {})})
    return out


def ack_command(user_id: str, command_id: str, ok: bool, res: dict) -> Optional[dict]:
    store = get_store()
    cmd = store.get("exec_commands", command_id)
    if not cmd or cmd.get("userId") != user_id:
        return None
    store.update("exec_commands", command_id, {
        "status": "DONE" if ok else "FAILED",
        "result": {k: res.get(k) for k in ("ticket", "position_id", "volume", "price", "error")},
        "ackedAt": datetime.now(timezone.utc).isoformat()})
    sig = store.get("signals", cmd.get("signal_doc_id") or "")
    if sig and sig.get("userId") == user_id:
        if ok:
            _apply_fill(store, sig, res, (cmd.get("payload") or {}).get("lots", 0))
            _log(f"MT5 PC EXECUTED {sig.get('market')} {sig.get('direction')} "
                 f"{res.get('volume')} lots @ {res.get('price')} (ticket {res.get('ticket')}).",
                 sig.get("market"))
        else:
            store.update("signals", sig["id"], {"execution_status": "FAILED",
                                                "mt5_error": str(res.get("error"))[:200]})
            _log(f"MT5 PC order FAILED - {res.get('error')}", sig.get("market"), kind="EXEC_WARN")
    return cmd


def expire_stale_commands() -> int:
    """Honest expiry: orders queued while the PC was away are marked expired."""
    store = get_store()
    cutoff = (datetime.now(timezone.utc).timestamp() - COMMAND_TTL_S) * 1
    n = 0
    for c in store.list("exec_commands", filters={"status": "PENDING"}, limit=50):
        try:
            ts = datetime.fromisoformat(str(c.get("createdAt", "")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts < cutoff:
            store.update("exec_commands", c["id"], {"status": "EXPIRED_OFFLINE"})
            sig = store.get("signals", c.get("signal_doc_id") or "")
            if sig and sig.get("userId") == c.get("userId"):
                store.update("signals", sig["id"], {"execution_status": "EXPIRED_PC_OFFLINE"})
            n += 1
    return n


# ---------------------------------------------------------------------------
# real W/L confirmation from MT5 deal history (shared by both transports)
# ---------------------------------------------------------------------------
def apply_deals(user_id: str, deals: List[dict]) -> int:
    """Confirm closed trades from broker deals (entry deal comment = signal_id)."""
    store = get_store()
    positions: Dict[Any, Dict[str, Any]] = {}
    for d in deals or []:
        if int(d.get("magic") or 0) != MAGIC:
            continue
        pos = positions.setdefault(d.get("position_id"), {"entry": None, "exits": []})
        if d.get("entry") == 0:
            pos["entry"] = d
        else:
            pos["exits"].append(d)

    updated = 0
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
        store.update("signals", docs[0]["id"], {
            "completed": True, "status": "CLOSED_MT5",
            "outcome": "WIN" if pl > 0 else ("LOSS" if pl < 0 else "BREAK_EVEN"),
            "mt5_confirmed": True, "mt5_pl": pl,
            "mt5_close_price": p["exits"][-1].get("price"),
            "mt5_closed_at": datetime.now(timezone.utc).isoformat()})
        updated += 1
        store.create("notifications", {
            "userId": user_id, "type": "TRADE_COMPLETED",
            "title": f"{'WIN' if pl > 0 else 'LOSS'} - {sig_id} (MT5)",
            "body": f"Broker-confirmed result: {pl:+.2f} USD on {vol} lots.",
            "signal_id": docs[0]["id"], "meta": {"mt5_pl": pl, "position_id": pos_id},
            "read": False})
    return updated


def sync_deals(user_id: str) -> int:
    """VPS mode: pull deals since the last sync from the bridge.

    BUGFIX (2026-09-19): the first sweep used to start 'now - 7 days', so if
    that one sweep failed (e.g. during a quota latch) and a later sweep
    advanced the cursor, the skipped trades were NEVER confirmed. Now the
    first successful sweep backfills the WHOLE history (since=0) and only
    then sets mt5_backfill_done; failures leave the flag unset so the next
    run retries the full window. apply_deals is idempotent (skips already-
    confirmed docs)."""
    if user_mode(user_id) != "vps" or not settings.bridge_url:
        return 0
    goals = _goals_doc(user_id) or {}
    since = int(goals.get("mt5_deal_sync_ts") or 0)
    if not goals.get("mt5_backfill_done"):
        since = 0
    data = bridge_get(f"/deals?since={since}", timeout=20)
    if not data:
        return 0
    n = apply_deals(user_id, data.get("deals") or [])
    _goals_update(user_id, {"mt5_deal_sync_ts": int(time.time()),
                            "mt5_backfill_done": True})
    return n


def connector_push_deals(user_id: str, deals: List[dict]) -> int:
    """Manual mode: the connector pushes fresh deals."""
    n = apply_deals(user_id, deals or [])
    _goals_update(user_id, {"mt5_deal_sync_ts": int(time.time())})
    return n


# ---------------------------------------------------------------------------
# honest status for Settings
# ---------------------------------------------------------------------------
def status(user_id: str) -> dict:
    goals = _goals_doc(user_id) or {}
    mode = user_mode(user_id)
    acct = goals.get("mt5_account")
    bridge_acct = bridge_get("/account") if mode == "vps" else None
    if bridge_acct:
        acct = bridge_acct
    return {
        "mode": mode,
        "enabled": execution_enabled(user_id),
        "bridge": {
            "configured": bool(settings.bridge_url),
            "online": bool(bridge_acct and "balance" in bridge_acct),
            "account": bridge_acct,
            "note": None if settings.bridge_url else "MT5_BRIDGE_URL not set - finish the VPS setup first.",
        },
        "pairing_code": ensure_pairing_code(user_id) if mode == "manual" else (goals.get("mt5_pairing_code")),
        "connector_online": connector_online(user_id),
        "connector_last_seen": goals.get("mt5_connector_seen"),
        "connector_machine": goals.get("mt5_connector_machine"),
        "account": acct,
        "trades_today": _executed_today(user_id) if mode != "off" else 0,
        "max_per_day": settings.execution_max_trades_per_day,
        "risk_cap_pct": settings.execution_risk_pct_cap,
        "tp_level": settings.execution_tp_level,
        "last_deal_sync": goals.get("mt5_deal_sync_ts"),
        "note": {
            "off": "Execution off - signals are advisory only.",
            "manual": "Your MT5 PC executes queued orders via the ForexMind connector.",
            "vps": "Demo-account execution via your VPS bridge. Real fills, real W/L.",
        }[mode],
    }
