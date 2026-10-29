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
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import settings
from ..db.store import get_store
from ..notifications.service import notify
from .pretrade import planned_rr as _planned_rr, validate_order

MAGIC = 20260914          # identifies ForexMind trades on the MT5 account
CONNECTOR_TIMEOUT_S = 90  # connector considered offline after this many seconds
COMMAND_TTL_S = 900       # queued manual orders expire after 15 min offline

PIP_SIZE = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1,
            "XAGUSD": 0.01, "NAS100": 1.0}
# USD per pip per 1.0 lot. XAGUSD: 5,000 oz contract -> $50 per 0.01 pip.
# NAS100: Exness 1 lot = 1 index contract -> $1 per 1.0 point.
# An instrument missing from BOTH tables cannot be sized (calc_lot returns
# 0.0) and the pre-trade validator refuses the order loudly - never a
# silent 0.01-lot fallback.
PIP_VALUE = {"EURUSD": 10.0, "GBPUSD": 10.0, "XAUUSD": 10.0, "XAGUSD": 50.0,
             "NAS100": 1.0}   # USD per pip per 1.0 lot
LOT_STEP = 0.01
MODES = ("off", "manual", "vps")

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# lot sizing (same math as the app's Position Size card)
# ---------------------------------------------------------------------------
def calc_lot(market: str, entry: float, sl: float, balance: float, risk_pct: float) -> float:
    """Risk-% position size. Returns 0.0 when the instrument is unknown or an
    input is unusable - the caller (pre-trade validator) must REFUSE the
    order loudly instead of silently trading a 0.01-lot fallback."""
    pip = PIP_SIZE.get(market)
    if pip is None or entry <= 0 or sl <= 0 or balance <= 0 or risk_pct <= 0:
        return 0.0
    pip_value = PIP_VALUE.get(market)
    if pip_value is None and market == "USDJPY":
        pip_value = 1000.0 / entry
    if not pip_value:
        return 0.0
    pips = abs(entry - sl) / pip
    if pips <= 0:
        return 0.0
    raw = (balance * risk_pct / 100.0) / (pips * pip_value)
    # epsilon guards the float edge (0.049999... must floor to 0.05, not 0.04)
    return max(LOT_STEP, math.floor(raw * 100 + 1e-9) / 100)


# ---------------------------------------------------------------------------
# risk lot-mode (user setting: low / medium / high)
# medium = exactly the previous behavior; the multiplier scales the computed
# lot AFTER the risk-% math, floored to the broker step (never below 0.01)
# ---------------------------------------------------------------------------
LOT_MODE_MULT = {"low": 0.5, "medium": 1.0, "high": 1.5}

# Broker-validity guards (NOT strategy changes): orders the broker would
# reject anyway (stop too close -> retcode 10016 'Invalid stops', seen live
# on SIG-20260921-007 with a 2.2-pip SL) never go out; and the risk-% lot
# math on a tiny SL can explode (0.77 lots) - a hard cap keeps that sane.
# Trail strategies: exits are the managed trail + time exit - NO fixed TP
# by rule (owner Pine 2026-10-24). Orders for these go out without a TP
# (the bridge sends tp=0.0 to MT5) and the trade manager owns the exits.
TRAIL_ONLY_STRATEGIES = {"strategy_trendbreak_v2"}
# PINSTRIPE V6 (owner locked rules 2025): TP1 partials are ladder-managed;
# the broker ticket carries the FINAL target TP2.
PINSTRIPE_TP2_STRATEGIES = {"strategy_hybrid_crt_tbs", "strategy_fade_crt_ext"}
MIN_STOP_PIPS = float(os.getenv("EXECUTION_MIN_STOP_PIPS", "6"))
MAX_LOTS = float(os.getenv("EXECUTION_MAX_LOTS", "1.0"))


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
    """Execution mode for a user. VPS-bridge execution and the master
    account view are OWNER-ONLY (multi-user privacy, 2026-10-01): every
    other account is signals-only - the env default must never silently
    attach the shared bridge account to someone else's dashboard."""
    mode = (_goals_doc(user_id) or {}).get("execution_mode")
    if mode == "mt5_bridge":        # legacy env name
        mode = "vps"
    if mode in MODES:
        if mode == "vps" and user_id != settings.owner_user_id:
            return "off"
        return mode
    if user_id != settings.owner_user_id:
        return "off"                # env default is owner-only
    return "vps" if settings.execution_mode == "mt5_bridge" else "off"


def set_mode(user_id: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    if mode == "vps":
        if user_id != settings.owner_user_id:
            raise PermissionError("VPS execution is reserved for the owner account")
        if not (settings.execution_mode == "mt5_bridge" and settings.bridge_url):
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


def _emit(user_id: Optional[str], kind: str, stage: str, market: Optional[str] = None,
          signal: Optional[dict] = None, ticket: Optional[int] = None,
          detail: str = "", latency_ms: Optional[float] = None) -> None:
    """Lifecycle-ledger append (Phase 4). Never raises, never blocks."""
    try:
        from .ledger import emit
        emit(user_id, kind, stage, market=market,
             signal_id=(signal or {}).get("signal_id"),
             signal_doc_id=(signal or {}).get("id"),
             ticket=ticket, detail=detail, latency_ms=latency_ms)
    except Exception:
        pass


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


# OWNER DIRECTIVE 2026-10-28 (supersedes the reject-don't-repair drift rule
# for market movement): "Once the signal is generated it should be entered.
# The app should just make sure to calculate the SL and TP the right way."
# Strategies fire rarely, so a generated signal is NEVER refused and NEVER
# cancelled for spread/slippage/market drift: the entry is re-anchored to
# the live price with the strategy's stop/TP distances preserved exactly
# (structure + R:R intact), and the lot is sized from the real fill price
# so the $ risk matches the configured risk-% precisely. Structural
# validation (sides, spec R:R, strategy lifecycle, caps) still refuses
# genuinely broken orders - that is correctness, not a missed trade.


def _live_quote(market: str) -> Optional[dict]:
    """Live bid/ask from the terminal, read at execution time (bridge /quote).
    None -> the caller falls back to signal-price sizing (old behaviour);
    a missing quote must never block the flow, the post-fill net still holds."""
    try:
        q = bridge_get(f"/quote?market={market}", timeout=5) or {}
        bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            return {"bid": bid, "ask": ask}
    except Exception:
        pass
    return None


def _reject_pretrade(store, signal: dict, user_id: str, market: str,
                     code: str, reason: str) -> None:
    """Record a pre-trade refusal loudly: signal status, ledger, activity,
    Telegram. The order is never sent and never silently repaired."""
    try:
        if signal.get("id"):
            store.update("signals", signal["id"], {
                "execution_status": "REJECTED_PRETRADE",
                "mt5_note": f"{code}: {reason}"[:200]})
        _log(f"Order REFUSED by pre-trade validation - {code}: {reason}",
             market, kind="EXEC_WARN")
        _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
              detail=f"pre-trade {code}: {reason}"[:160])
        notify(user_id, "EXECUTION_SKIPPED",
               f"ORDER REFUSED - {market} {signal.get('direction')}",
               f"Pre-trade check {code}: {reason}. No trade was placed.",
               meta={"telegram_important": True},
               signal_id=signal.get("id"))
    except Exception:
        pass


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
    # PINSTRIPE V6 rule 7 (owner 2026-10-25): strategies run INDEPENDENTLY
    # and may trade the same pair at the same time - the dedup key is
    # therefore per STRATEGY (same-setup re-sends of one strategy's signal
    # remain blocked; a different strategy's setup on the same candle is a
    # legitimate independent trade).
    docs = store.list("signals", filters={
        "market": market, "direction": direction, "candle_time": candle_time,
        "strategy_id": signal.get("strategy_id")},
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
    # Final-build section 19: the signal may only touch the user's SELECTED
    # account. Resolve it server-side (never from the frontend) and refuse
    # when that account is disabled or disconnected.
    account_id = None
    try:
        from ..api.routes_accounts import active_account
        acct = active_account(user_id)
        if acct:
            account_id = acct.get("id")
            if acct.get("mode") != "signals_only" and not acct.get("trading_enabled"):
                if signal.get("id"):
                    get_store().update("signals", signal["id"], {
                        "execution_status": "SKIPPED_ACCOUNT_DISABLED",
                        "account_id": account_id,
                        "mt5_note": f"Account '{acct.get('label')}' has trading disabled"})
                _emit(user_id, "ENTRY", "SKIPPED", market=signal.get("market"),
                      signal=signal, detail="account trading disabled")
                return
            signal["account_id"] = account_id
            if signal.get("id"):
                get_store().update("signals", signal["id"],
                                   {"account_id": account_id})
    except Exception:
        pass   # account registry is an isolation ADD-ON; base flow continues

    mode = user_mode(user_id)
    if mode == "off":
        if signal.get("id"):
            get_store().update("signals", signal["id"], {
                "execution_status": "SKIPPED_ADVISORY_MODE",
                "mt5_note": "Execution mode is OFF - signal kept as advisory"})
        _emit(user_id, "ENTRY", "SKIPPED", market=signal.get("market"),
              signal=signal, detail="execution mode OFF")
        return   # advisory signals - honest default

    # Single-account engine: the bridge drives ONE Exness account (the
    # owner's).  Engine-executed trades must ALWAYS land in the owner's
    # journal (standing directive) - scans for any other user (demo/test)
    # stay advisory so two user contexts can never race for one position.
    if user_id != settings.owner_user_id:
        from ..core.permissions import trading_allowed
        allowed, why = trading_allowed(user_id)
        if not allowed:
            if signal.get("id"):
                get_store().update("signals", signal["id"], {
                    "execution_status": "SKIPPED_NOT_AUTHORIZED",
                    "mt5_note": f"Auto trading not authorized for this user ({why})"})
                _log(f"Execution skipped - auto trading not authorized ({why}). "
                     "Signal kept as advisory.", signal.get("market"))
                _emit(user_id, "ENTRY", "SKIPPED", market=signal.get("market"),
                      signal=signal, detail=f"not authorized ({why})")
                notify(user_id, "EXECUTION_SKIPPED",
                       f"NOT EXECUTED - {signal.get('market')} {signal.get('direction')}",
                       f"Auto trading is not authorized for this account ({why}). "
                       "An admin must approve and verify MT5 first.",
                       signal_id=signal.get("id"))
            return

    store = get_store()
    market = signal.get("market")

    if not execution_enabled(user_id):
        if signal.get("id"):
            store.update("signals", signal["id"], {
                "execution_status": "SKIPPED_KILL_SWITCH",
                "mt5_note": "Kill switch is ON - signal kept as advisory"})
        _log("Execution skipped - kill switch is ON (Settings). Signal is advisory only.", market)
        _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
              detail="kill switch ON")
        return

    # Master spec sections 3/30: strategy lifecycle is a SERVER-side gate.
    # SLEEPING/RETIRED/CANDIDATE strategies may inform and research, but
    # NEVER touch MT5 - not via auto-execution, not via a manual take.
    from ..learning.versions import lifecycle_of
    _life = lifecycle_of(signal.get("strategy_id"))
    if _life != "LIVE":
        if signal.get("id"):
            store.update("signals", signal["id"], {
                "execution_status": "SKIPPED_LIFECYCLE",
                "mt5_note": f"Strategy lifecycle is {_life} - research only, execution blocked"})
        _log(f"Execution blocked - strategy lifecycle is {_life} (not LIVE). "
             "Signal is advisory/research only.", market)
        _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
              detail=f"strategy lifecycle {_life}")
        return

    # Owner directive 2026-10-25: per-strategy RECORD-ONLY (research) mode.
    # The strategy keeps generating + recording signals so the owner can
    # judge validity, but NOTHING is sent to MT5 until it is switched back
    # to live - server-side, per strategy, never client-trusted.
    _sdoc = store.list("strategies",
                       filters={"id": signal.get("strategy_id")}, limit=1)
    if _sdoc and _sdoc[0].get("execution") == "record_only":
        if signal.get("id"):
            store.update("signals", signal["id"], {
                "execution_status": "SKIPPED_RECORD_ONLY",
                "mt5_note": "Strategy is in record-only research mode - "
                            "signal recorded, not executed"})
        _log(f"Execution blocked - {signal.get('strategy_id')} is in "
             "record-only research mode. Signal recorded for review.", market)
        _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
              detail="strategy record-only (research)")
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

    cap = _max_trades_per_day(user_id)
    if _executed_today(user_id) >= cap:
        if signal.get("id"):
            store.update("signals", signal["id"], {
                "execution_status": "SKIPPED_EXEC_DAILY_CAP",
                "mt5_note": f"Execution daily cap reached ({cap}/day)"})
        _log(f"Execution skipped - daily cap reached ({cap}/day).", market)
        _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
              detail="daily cap reached")
        notify(user_id, "EXECUTION_SKIPPED", f"NOT EXECUTED - {signal.get('market')} {signal.get('direction')}",
               f"Daily execution cap reached ({cap}/day). "
               "The signal is tracked as research only - no trade was placed.",
               meta={"telegram_important": True},
               signal_id=signal.get("id"))
        return

    try:
        entry, sl = float(signal["entry"]), float(signal["sl"])
        pip = PIP_SIZE.get(market)
        if pip and abs(entry - sl) < MIN_STOP_PIPS * pip:
            store.update("signals", signal["id"], {
                "execution_status": "SKIPPED_STOP_TOO_TIGHT",
                "mt5_note": f"SL {abs(entry - sl) / pip:.1f} pips from entry - broker minimum "
                            f"is {MIN_STOP_PIPS:g} (signal kept as advisory)"})
            _log(f"Execution skipped - SL only {abs(entry - sl) / pip:.1f} pips from entry; "
                 f"the broker rejects stops under {MIN_STOP_PIPS:g} pips (retcode 10016). "
                 "Signal kept as advisory.", market, kind="EXEC_WARN")
            notify(user_id, "EXECUTION_SKIPPED", f"NOT EXECUTED - {signal.get('market')} {signal.get('direction')}",
                   f"Stop distance {abs(entry - sl) / pip:.1f} pips is under the broker minimum "
                   f"({MIN_STOP_PIPS:g} pips). Research only - no trade was placed.",
                   signal_id=signal.get("id"))
            _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
                  detail=f"stop too tight (< {MIN_STOP_PIPS:g} pips)")
            return
        # P10 deterministic pre-trade guards: news / spread / vol / correlation.
        # Every refusal is loud (status + ledger + log + notification).
        try:
            from ..risk_checks import check_entry
            ok, guard, gwhy = check_entry(user_id, market,
                                          direction=signal.get("direction"))
        except Exception as exc:
            # the guard chain itself must never fail-open (2026-09-29 lesson)
            ok, guard, gwhy = False, "GUARDS", (
                f"guard chain error ({type(exc).__name__}) - entry blocked (fail-closed)")
        if not ok:
            status_key = f"SKIPPED_RISK_{guard}"
            store.update("signals", signal["id"], {
                "execution_status": status_key,
                "mt5_note": f"{guard} guard: {gwhy}"})
            _log(f"Execution skipped - {guard} guard: {gwhy}. Signal kept as advisory.",
                 market, kind="EXEC_WARN")
            _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
                  detail=f"{guard} guard: {gwhy}")
            notify(user_id, "EXECUTION_SKIPPED",
                   f"NOT EXECUTED - {market} {signal.get('direction')}",
                   f"Risk guard {guard}: {gwhy}. The signal is tracked as "
                   "research only - no trade was placed.",
                   signal_id=signal.get("id"))
            return
        risk_pct = min(float(((_goals_doc(user_id) or {}).get("risk_per_trade_pct", 1.0)) or 1.0),
                       settings.execution_risk_pct_cap)
        # TP level: static (1/2/3) or AUTO = probability-driven selection from
        # the trade's context bucket (user directive 2026-09-25: "the TP
        # manager should work with probability"). Auditable record lands on
        # the signal doc either way.
        # OWNER BRIEF 2026-10-24 (R:R audit): a strategy-defined target WINS.
        # Signals with a single intended TP (tp2/tp3 empty - both live spec
        # strategies) are sent with THAT target. The TP-level manager only
        # applies to signals that genuinely define multiple targets. This is
        # the exact spot where intended-1:3 setups went out at 1:1 in
        # September (orderRR 1.0 vs rr_primary 3.0, prod evidence).
        _tp2, _tp3 = signal.get("tp2"), signal.get("tp3")
        if _tp2 in (None, "", 0) and _tp3 in (None, "", 0):
            tp_level = fallback_level = 1     # the strategy's own target
        elif signal.get("strategy_id") in PINSTRIPE_TP2_STRATEGIES:
            # PINSTRIPE V6: TP1 is the ladder-managed partial (50% + BE);
            # the broker ticket carries TP2 = the final target (locked spec)
            tp_level = fallback_level = 2
        else:
            tp_level = fallback_level = 2
            raw = str(settings.execution_tp_level).strip().upper()
            if raw.isdigit() and int(raw) in (1, 2, 3):
                tp_level = fallback_level = int(raw)
            else:   # AUTO
                try:
                    from .tp_picker import pick_tp_level
                    sel = pick_tp_level(signal, min_sample=int(os.getenv("TP_PROB_MIN_SAMPLE", "20")),
                                        fallback_level=2)
                    tp_level = int(sel.get("level") or 2)
                    fallback_level = tp_level
                    store.update("signals", signal["id"], {"tp_selection": sel})
                    if sel.get("source") == "probability":
                        _log(f"TP level {tp_level} selected by probability "
                             f"(P(1R)={sel.get('p1')}, P(2R)={sel.get('p2')}, P(3R)={sel.get('p3')}, "
                             f"n={sel.get('n')}).", market, kind="EXEC")
                except Exception:
                    tp_level = 2
        tp = signal.get(f"tp{max(1, min(3, tp_level))}") or signal.get("tp1")

        if mode == "vps":
            acct = bridge_get("/account")
            if not acct or "balance" not in acct:
                store.update("signals", signal["id"], {"execution_status": "SKIPPED_BRIDGE_OFFLINE"})
                _log("Execution skipped - VPS bridge offline (signal NOT sent to broker).",
                     market, kind="EXEC_WARN")
                _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
                      detail="bridge offline")
                notify(user_id, "EXECUTION_SKIPPED", f"NOT EXECUTED - {signal.get('market')} {signal.get('direction')}",
                       "VPS bridge offline - the order was NOT sent to the broker. "
                       "Signal kept as research only.",
                       meta={"telegram_important": True},
                       signal_id=signal.get("id"))
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
                    _emit(user_id, "ENTRY", "SKIPPED", market=market, signal=signal,
                          detail=f"duplicate setup (already via {dup})")
                    notify(user_id, "EXECUTION_SKIPPED", f"NOT EXECUTED - {signal.get('market')} {signal.get('direction')}",
                           "This exact setup is already on the account (one broker order "
                           "per setup). Research only - no second trade was placed.",
                           signal_id=signal.get("id"))
                    return
                store.update("signals", signal["id"], {"execution_status": "EXECUTING"})
            # SPREAD-AWARE SIZING (owner brief 2026-10-28): the order
            # fills at the live ask (BUY) / bid (SELL), so risk is
            # measured from THAT price, not the signal price. Levels are
            # never moved - sizing adapts, and a spread-broken R:R is
            # refused below instead of "repaired".
            _quote = _live_quote(market)
            _fill_px = None
            if _quote:
                _is_buy = signal["direction"].upper() == "BUY"
                _fill_px = _quote["ask"] if _is_buy else _quote["bid"]
                _spread = _fill_px - _quote["bid"] if _is_buy else _quote["ask"] - _fill_px
                _log(f"Live quote {market}: bid {_quote['bid']} / ask {_quote['ask']} "
                     f"(spread {_spread:.5g}) - sizing + R:R measured from the "
                     f"{'ask' if _is_buy else 'bid'}.", market)
            # ANCHOR FIRST (postmortem SIG-20261029-030, owner directive):
            # the stop the trade will actually CARRY must drive the lot size.
            # Sizing against the signal's original SL while the fill has
            # drifted toward it invents a tiny stop and explodes the size
            # (17-pip drift on GBPUSD sized 4.25 lots against a 1.2-pip gap
            # and got capped into a $182 refusal). Anchor, stop and ladder
            # are computed HERE, before any sizing math.
            _anchor = _fill_px or entry
            _delta = _anchor - entry
            sl_eff = sl + _delta
            # OWNER TP POLICY (directive 2026-10-28): risk is $50 per trade,
            # so the target ladder is fixed in R, not in strategy structure
            # points: tp1 = +1R (+$50), tp2 = +2R (+$100), tp3 = +3R (+$150),
            # measured from the re-anchored entry and stop. The BROKER order
            # carries TP = tp3; the trade manager works the ladder in between
            # (tp1 partial, tp2 SL lock at +1R, AI reversal protection).
            # The strategy's structure still sets the STOP; only the targets
            # are universal now. Trail strategies keep the managed trail
            # (no broker TP) untouched.
            _trail_only = signal.get("strategy_id") in TRAIL_ONLY_STRATEGIES
            tp1_eff = tp2_eff = tp3_eff = None
            if not _trail_only:
                _sign = 1.0 if str(signal.get("direction", "")).upper() == "BUY" else -1.0
                _stop_d = abs(_anchor - sl_eff)
                tp1_eff = round(_anchor + _sign * _stop_d, 5)
                tp2_eff = round(_anchor + _sign * 2.0 * _stop_d, 5)
                tp3_eff = round(_anchor + _sign * 3.0 * _stop_d, 5)
                tp_eff = tp3_eff
            else:
                tp_eff = (tp + _delta) if tp else None
            # OWNER 2026-10-29: the user's DOLLAR risk wins when set (> 0);
            # otherwise fall back to the % of balance. $50 is the default.
            _g = _goals_doc(user_id) or {}
            _risk_usd = float(_g.get("risk_per_trade_usd", 50.0) or 0.0)
            _bal = float(acct.get("balance") or 0.0)
            if _risk_usd > 0 and _bal > 0:
                risk_pct = (_risk_usd / _bal) * 100.0
            lots = calc_lot(market, _anchor, sl_eff, _bal, risk_pct)
            _mode = _lot_mode(user_id)
            lots = apply_lot_mode(lots, _mode)
            if lots > MAX_LOTS:
                _log(f"Lot size capped {lots} -> {MAX_LOTS} (EXECUTION_MAX_LOTS).", market,
                     kind="EXEC_WARN")
                lots = MAX_LOTS
            # PROP-FIRM BUDGET CAP (owner brief 2026-10-10, s.6): shrink-only
            # cap from the account's CONFIGURED challenge rules (an account
            # without prop_rules is not governed and passes straight through).
            # Never enlarges; refuses the entry when no risk budget remains.
            try:
                from ..risk.propfirm import cap_lots as _pf_cap
                from ..engine.daily import daily_state as _daily_state
                _acct_doc = None
                try:
                    from ..api.routes_accounts import active_account as _aa
                    _acct_doc = _aa(user_id)
                except Exception:
                    _acct_doc = None
                if _acct_doc:
                    _pip = PIP_SIZE.get(market, 0.1)
                    _pipv = PIP_VALUE.get(market, 10.0)
                    _stop_usd_per_lot = (abs(_anchor - sl_eff) / _pip) * _pipv
                    _d = _daily_state(user_id)
                    _pf = _pf_cap(_acct_doc, {
                        "daily_pl": float(_d.get("total_usd") or 0.0),
                        "consecutive_losses": int(_d.get("consecutive_losses") or 0),
                        "peak_equity": _acct_doc.get("peak_equity"),
                    }, lots, _stop_usd_per_lot)
                    if not _pf.get("allowed"):
                        store.update("signals", signal["id"], {
                            "execution_status": "SKIPPED_PROP_FIRM_BUDGET",
                            "mt5_note": str(_pf.get("reason"))[:200]})
                        _log(f"Execution skipped - prop-firm budget: "
                             f"{_pf.get('reason')}", market, kind="EXEC_WARN")
                        _emit(user_id, "ENTRY", "SKIPPED", market=market,
                              signal=signal, detail=str(_pf.get("reason"))[:120])
                        return
                    if _pf.get("shrunk"):
                        _log(f"Prop-firm cap: lot {lots} -> {_pf['lots']} "
                             f"(challenge {_pf.get('budgets', {}).get('label')}).",
                             market, kind="EXEC_WARN")
                        lots = float(_pf["lots"])
            except Exception as _pf_exc:
                _log(f"Prop-firm cap check failed ({type(_pf_exc).__name__}) - "
                     "continuing with the standard risk math.", market,
                     kind="EXEC_WARN")
            if _delta:
                _log(f"Re-anchored {market} {signal['direction']} to the live "
                     f"{'ask' if signal['direction'].upper() == 'BUY' else 'bid'} {_anchor} "
                     f"(signal entry {entry}): SL {sl}->{sl_eff}; ladder re-anchored "
                     f"1R/2R/3R = {tp1_eff}/{tp2_eff}/{tp3_eff}.", market)
            if tp1_eff is not None:
                store.update("signals", signal["id"], {
                    "order_entry": _anchor,
                    "strategy_tp1": signal.get("tp1"), "strategy_tp2": signal.get("tp2"),
                    "strategy_tp3": signal.get("tp3"),
                    "tp1": tp1_eff, "tp2": tp2_eff, "tp3": tp3_eff,
                    "mt5_note": (("entered at live "
                                  f"{'ask' if signal['direction'].upper() == 'BUY' else 'bid'} "
                                  f"{_anchor} (signal entry {entry}); " if _delta else "")
                                 + f"owner TP ladder: tp1 1R +$50, tp2 2R +$100, "
                                   f"tp3 3R +$150; order TP at tp3")[:250]})
            # UNIVERSAL PRE-TRADE VALIDATION (owner brief 2026-10-24, s.3,
            # amended 2026-10-28: market movement is re-anchored above, never
            # refused): the composed order must match the strategy's own rules
            # exactly - direction, sides, configured R:R, sizing. Refuse
            # genuinely broken orders, never repair their structure.
            store.update("signals", signal["id"],
                         {"order_rr": _planned_rr(_anchor, sl_eff, tp_eff), "order_tp": tp_eff})
            _ok, _code, _why = validate_order(
                market=market, direction=signal.get("direction"),
                entry=_anchor, sl=sl_eff, tp=tp_eff, lots=lots,
                intended_rr=signal.get("rr_primary"),
                declared_tps=([tp1_eff, tp2_eff, tp3_eff] if tp1_eff is not None
                              else [signal.get("tp1"), signal.get("tp2"), signal.get("tp3")]),
                symbol_known=market in PIP_SIZE,
                strategy_status=(store.list("strategies", filters={"id": signal.get("strategy_id")}, limit=1) or [{}])[0].get("status"),
                lifecycle=_life, pip_size=PIP_SIZE.get(market),
                pip_value=PIP_VALUE.get(market), min_stop_pips=MIN_STOP_PIPS,
                balance=float(acct.get("balance") or 0.0),
                eff_risk_pct=risk_pct * LOT_MODE_MULT.get(_mode, 1.0),
                lots_cap=MAX_LOTS, dup_checked=True, budgets_allowed=True,
                allow_no_tp=signal.get("strategy_id") in TRAIL_ONLY_STRATEGIES,
                reference_price=None)   # drift is re-anchored, never refused (owner 2026-10-28)
            if not _ok:
                _reject_pretrade(store, signal, user_id, market, _code, _why)
                return
            _emit(user_id, "ENTRY", "REQUESTED", market=market, signal=signal,
                  detail=f"{signal['direction']} {lots} lots @ {_anchor}, SL {sl_eff}, TP {tp_eff}")
            _t0 = time.time()
            res = bridge_post("/execute", {
                "signal_id": signal["signal_id"], "symbol": market,
                "direction": signal["direction"], "lots": lots,
                "sl": sl_eff, "tp": tp_eff, "magic": MAGIC})
            _lat = (time.time() - _t0) * 1000.0
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
                _emit(user_id, "ENTRY", "FAILED", market=market, signal=signal,
                      detail=str(err), latency_ms=_lat)

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
            if lots > MAX_LOTS:
                _log(f"Lot size capped {lots} -> {MAX_LOTS} (EXECUTION_MAX_LOTS).", market,
                     kind="EXEC_WARN")
                lots = MAX_LOTS
            # PROP-FIRM BUDGET CAP (owner brief 2026-10-10, s.6): shrink-only
            # cap from the account's CONFIGURED challenge rules (an account
            # without prop_rules is not governed and passes straight through).
            # Never enlarges; refuses the entry when no risk budget remains.
            try:
                from ..risk.propfirm import cap_lots as _pf_cap
                from ..engine.daily import daily_state as _daily_state
                _acct_doc = None
                try:
                    from ..api.routes_accounts import active_account as _aa
                    _acct_doc = _aa(user_id)
                except Exception:
                    _acct_doc = None
                if _acct_doc:
                    _pip = PIP_SIZE.get(market, 0.1)
                    _pipv = PIP_VALUE.get(market, 10.0)
                    _stop_usd_per_lot = (abs(entry - sl) / _pip) * _pipv
                    _d = _daily_state(user_id)
                    _pf = _pf_cap(_acct_doc, {
                        "daily_pl": float(_d.get("total_usd") or 0.0),
                        "consecutive_losses": int(_d.get("consecutive_losses") or 0),
                        "peak_equity": _acct_doc.get("peak_equity"),
                    }, lots, _stop_usd_per_lot)
                    if not _pf.get("allowed"):
                        store.update("signals", signal["id"], {
                            "execution_status": "SKIPPED_PROP_FIRM_BUDGET",
                            "mt5_note": str(_pf.get("reason"))[:200]})
                        _log(f"Execution skipped - prop-firm budget: "
                             f"{_pf.get('reason')}", market, kind="EXEC_WARN")
                        _emit(user_id, "ENTRY", "SKIPPED", market=market,
                              signal=signal, detail=str(_pf.get("reason"))[:120])
                        return
                    if _pf.get("shrunk"):
                        _log(f"Prop-firm cap: lot {lots} -> {_pf['lots']} "
                             f"(challenge {_pf.get('budgets', {}).get('label')}).",
                             market, kind="EXEC_WARN")
                        lots = float(_pf["lots"])
            except Exception as _pf_exc:
                _log(f"Prop-firm cap check failed ({type(_pf_exc).__name__}) - "
                     "continuing with the standard risk math.", market,
                     kind="EXEC_WARN")
            # UNIVERSAL PRE-TRADE VALIDATION (static checks at queue time;
            # the live entry-drift guard does not apply here - there is no
            # authoritative cloud-side price while the connector is offline;
            # the queue carries the signal's own levels untouched).
            # Refuse, never repair. (Dup protection: single authorized trader,
            # the connector acks each signal_id once - unchanged semantics.)
            _ref = None
            if signal.get("id"):
                store.update("signals", signal["id"],
                             {"order_rr": _planned_rr(entry, sl, tp), "order_tp": tp})
            _ok, _code, _why = validate_order(
                market=market, direction=signal.get("direction"),
                entry=entry, sl=sl, tp=tp, lots=lots,
                intended_rr=signal.get("rr_primary"),
                declared_tps=[signal.get("tp1"), signal.get("tp2"), signal.get("tp3")],
                symbol_known=market in PIP_SIZE,
                strategy_status=(store.list("strategies", filters={"id": signal.get("strategy_id")}, limit=1) or [{}])[0].get("status"),
                lifecycle=_life, pip_size=PIP_SIZE.get(market),
                pip_value=PIP_VALUE.get(market), min_stop_pips=MIN_STOP_PIPS,
                balance=float(bal or 0.0),
                eff_risk_pct=risk_pct * LOT_MODE_MULT.get(_mode, 1.0),
                lots_cap=MAX_LOTS, dup_checked=True, budgets_allowed=True,
                allow_no_tp=signal.get("strategy_id") in TRAIL_ONLY_STRATEGIES,
                reference_price=_ref)
            if not _ok:
                _reject_pretrade(store, signal, user_id, market, _code, _why)
                return
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
        if os.getenv("FXM_DEBUG"):
            import traceback
            traceback.print_exc()
        try:
            store.update("signals", signal["id"], {"execution_status": "FAILED",
                                                   "mt5_error": type(exc).__name__[:200]})
        except Exception:
            pass
        # 3.0 spec: EVERY failure is loud - ledger + Telegram, never silent.
        try:
            _emit(user_id, "ENTRY", "FAILED", market=signal.get("market"),
                  signal=signal,
                  detail=f"{type(exc).__name__}: {str(exc)[:120]}")
            notify(user_id, "EXECUTION_FAILED",
                   f"ENTRY FAILED - {signal.get('market')} {signal.get('direction')}",
                   f"Execution error: {type(exc).__name__}. No trade was opened. "
                   "The failure is recorded in the Execution ledger.",
                   signal_id=signal.get("id"))
        except Exception:
            pass
        _log(f"Execution error - {type(exc).__name__}", market, kind="EXEC_WARN")


def _apply_fill(store, signal: dict, res: dict, lots: float) -> None:
    """Record the broker fill WITH the R:R truth (owner brief 2026-10-24):
    intended_rr = the strategy's configured multiple; executed_rr = the same
    levels measured from the ACTUAL fill; entry_slippage = fill - entry."""
    _int_rr = _exec_rr = _slip = None
    try:
        # the R:R truth follows the ORDER: the ticket TP (order_tp - equals
        # tp1 for single-target strategies, TP2 for the PINSTRIPE pair),
        # falling back to tp1; rr_primary was validated against that level
        _tp_raw = signal.get("order_tp") or signal.get("tp1")
        _tp = float(_tp_raw) if _tp_raw not in (None, "", 0) else None
        e, sl = float(signal["entry"]), float(signal["sl"])
        fill = res.get("price")
        if _tp is not None and abs(e - sl) > 0:
            _int_rr = round(abs(_tp - e) / abs(e - sl), 4)
        if fill and float(fill) > 0 and abs(float(fill) - sl) > 0:
            if _tp is not None:
                _exec_rr = round(abs(_tp - float(fill)) / abs(float(fill) - sl), 4)
            _slip = round(float(fill) - e, 5)
    except Exception:
        pass
    store.update("signals", signal["id"], {
        "execution_status": "SUBMITTED", "mt5_ticket": res.get("ticket"),
        "mt5_position_id": res.get("position_id"), "mt5_volume": res.get("volume") or lots,
        "mt5_open_price": res.get("price"),
        "mt5_symbol": res.get("symbol"),   # broker-resolved name (suffix/alias included)
        "intended_rr": _int_rr, "executed_rr": _exec_rr, "entry_slippage": _slip,
        "mt5_executed_at": datetime.now(timezone.utc).isoformat()})

    # Owner directive 2026-10-28: a filled trade is NEVER auto-closed for
    # slippage. The fill is recorded with its honest executed_rr; the live
    # SL/TP on the ticket (re-anchored at submission) manage the trade.

    # OWNER DIRECTIVE (2026-10-25): the "trade entered" Telegram message -
    # fired ONLY here, on a real broker fill (never on signal creation).
    try:
        _e = float(res.get("price") or signal.get("entry") or 0)
        _sl = float(signal.get("sl") or 0)
        _tp_raw = signal.get("order_tp") or signal.get("tp1")
        _tp = float(_tp_raw) if _tp_raw not in (None, "", 0) else None
        _dir = str(signal.get("direction") or "")
        _name = (signal.get("strategy_name") or signal.get("strategy_id") or "")
        _sid = signal.get("signal_id") or signal.get("id") or ""
        _vol = res.get("volume") or lots
        _body = (f"{_name} | {_sid}\n"
                 f"Filled {_vol} lots at {_e:,.5g} - SL {_sl:,.5g}")
        if _tp is not None:
            _body += (f" - TP {_tp:,.5g}"
                      + (f" (RR 1:{_int_rr:.2f})" if _int_rr else "") + ".")
        else:
            _body += ".\nNo fixed TP by rule - managed trail + time exit."
        notify(signal.get("userId") or settings.owner_user_id, "TRADE_ENTERED",
               f"{'🟢' if _dir.upper() == 'BUY' else '🔴'} ENTERED - "
               f"{signal.get('market')} {_dir}", _body,
               signal_id=signal.get("id"))
    except Exception:
        pass

    _emit(signal.get("userId") or settings.owner_user_id, "ENTRY", "CONFIRMED",
          market=signal.get("market"), signal=signal,
          ticket=res.get("ticket"),
          detail=f"broker ticket {res.get('ticket')} @ {res.get('price')}")


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
            _emit(user_id, "MODIFY_SL", "REQUESTED", ticket=ticket,
                  detail=f"SL -> {new_sl}{note}")
            _t0 = time.time()
            res = bridge_post("/modify_sl",
                              {"ticket": int(ticket), "sl": float(new_sl)}) or {}
            _lat = (time.time() - _t0) * 1000.0
            if res.get("ok"):
                _log(f"MT5 SL moved to {new_sl} on #{ticket}{note}.", kind="EXEC")
                _emit(user_id, "MODIFY_SL", "CONFIRMED", ticket=ticket,
                      detail=f"SL -> {new_sl}", latency_ms=_lat)
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 SL change FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
                _emit(user_id, "MODIFY_SL", "FAILED", ticket=ticket,
                      detail=str(err), latency_ms=_lat)
            return res
        _emit(user_id, "MODIFY_SL", "QUEUED", ticket=ticket,
              detail=f"SL -> {new_sl}{note} (PC connector)")
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
            _emit(user_id, "PARTIAL_CLOSE", "REQUESTED", ticket=ticket,
                  detail=f"partial {volume or fraction}{note}")
            _t0 = time.time()
            res = bridge_post("/partial_close", payload) or {}
            _lat = (time.time() - _t0) * 1000.0
            if res.get("ok"):
                _log(f"MT5 partial close #{ticket}: closed {res.get('closed_volume')} "
                     f"lots, {res.get('remaining_volume')} left{note}.", kind="EXEC")
                _emit(user_id, "PARTIAL_CLOSE", "CONFIRMED", ticket=ticket,
                      detail=f"closed {res.get('closed_volume')}, left "
                             f"{res.get('remaining_volume')}", latency_ms=_lat)
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 partial close FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
                _emit(user_id, "PARTIAL_CLOSE", "FAILED", ticket=ticket,
                      detail=str(err), latency_ms=_lat)
            return res
        _emit(user_id, "PARTIAL_CLOSE", "QUEUED", ticket=ticket,
              detail=f"partial {volume or fraction}{note} (PC connector)")
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
            _emit(user_id, "CLOSE_FULL", "REQUESTED", ticket=ticket, detail=f"close{note}")
            _t0 = time.time()
            res = bridge_post("/close", {"ticket": int(ticket)}) or {}
            _lat = (time.time() - _t0) * 1000.0
            if res.get("ok"):
                _log(f"MT5 position #{ticket} closed{note}.", kind="EXEC")
                _emit(user_id, "CLOSE_FULL", "CONFIRMED", ticket=ticket,
                      detail="position closed", latency_ms=_lat)
            else:
                err = res.get("error") or res.get("detail") or res.get("http_status")
                _log(f"MT5 close FAILED on #{ticket} - {err}{note}", kind="EXEC_WARN")
                _emit(user_id, "CLOSE_FULL", "FAILED", ticket=ticket,
                      detail=str(err), latency_ms=_lat)
            return res
        _emit(user_id, "CLOSE_FULL", "QUEUED", ticket=ticket,
              detail=f"close{note} (PC connector)")
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
            _emit(user_id, "ENTRY", "FAILED", market=sig.get("market"), signal=sig,
                  detail=f"PC connector: {res.get('error')}")
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
            _emit(c.get("userId"), "ENTRY", "EXPIRED",
                  market=(sig or {}).get("market"),
                  signal={"signal_id": c.get("signal_id"), "id": c.get("signal_doc_id")},
                  detail="PC offline - command TTL expired")
            n += 1
    return n


# ---------------------------------------------------------------------------
# real W/L confirmation from MT5 deal history (shared by both transports)
# ---------------------------------------------------------------------------
# position_ids already confirmed and written back - skipped before ANY store
# read so the 48h deal window never re-processes the same trades all day
# (Firestore free-tier quota: the re-reads alone burned ~50k reads/day and
# latched the whole app into quota mode).
_CONFIRMED_POSITIONS: set = set()


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
        if pos_id in _CONFIRMED_POSITIONS:
            continue
        sig_id = (p["entry"].get("comment") or "").strip()
        if not sig_id:
            continue
        # PARTIAL-EXIT GUARD (PINSTRIPE ladders, 2026-10-25): a TP1 partial
        # (e.g. 50%) also writes an exit deal. Confirming on it would close
        # the signal early, report a half-trade P/L as the final result and
        # permanently skip the REAL close (position_id would be marked
        # confirmed). A trade is complete only when the exit volume covers
        # the entry volume (5% tolerance for volume rounding).
        entry_vol = float(p["entry"].get("volume") or 0)
        exit_vol = sum(float(e.get("volume") or 0) for e in p["exits"])
        if entry_vol > 0 and exit_vol + 1e-9 < 0.95 * entry_vol:
            continue                         # wait for the remaining exits
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
        _CONFIRMED_POSITIONS.add(pos_id)
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
    confirmed docs).

    BUGFIX (2026-09-24): an incremental 5-minute window can contain a
    position's EXIT deal but not its ENTRY deal (opened hours earlier) -
    apply_deals needs the entry deal (it carries the signal_id comment) so
    the close was silently skipped and the app showed ghost "ACTIVE"
    signals for trades the broker had already closed. The window now always
    overlaps MT5_DEAL_LOOKBACK_S (default 48h); idempotency makes the
    overlap free."""
    if user_mode(user_id) != "vps" or not settings.bridge_url:
        return 0
    goals = _goals_doc(user_id) or {}
    since = int(goals.get("mt5_deal_sync_ts") or 0)
    if not goals.get("mt5_backfill_done"):
        since = 0
    lookback = int(os.getenv("MT5_DEAL_LOOKBACK_S", str(48 * 3600)))
    since = max(0, since - max(0, lookback))
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
def _max_trades_per_day(user_id: str) -> int:
    """Effective execution daily cap: the user's settings doc first (kind=risk,
    key max_trades_per_day), env fallback. Read through THIS module's store
    binding so tests/instrumented stores stay consistent."""
    try:
        docs = get_store().list("settings", filters={"userId": user_id, "kind": "risk"},
                                limit=1)
        v = int(docs[0].get("max_trades_per_day") or 0) if docs else 0
        if v > 0:
            return v
    except Exception:
        pass
    return settings.execution_max_trades_per_day


def status(user_id: str) -> dict:
    goals = _goals_doc(user_id) or {}
    mode = user_mode(user_id)
    # configured TP level for display (env: "AUTO" | "1" | "2" | "3")
    tp_level = str(settings.execution_tp_level or "2").strip().upper()
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
        "max_per_day": _max_trades_per_day(user_id),
        "risk_cap_pct": settings.execution_risk_pct_cap,
        "tp_level": tp_level,
        "last_deal_sync": goals.get("mt5_deal_sync_ts"),
        "note": {
            "off": "Execution off - signals are advisory only.",
            "manual": "Your MT5 PC executes queued orders via the ForexMind connector.",
            "vps": "Demo-account execution via your VPS bridge. Real fills, real W/L.",
        }[mode],
    }
