"""TP-leak audit (Master Upgrade Phase 4 / Stage 3).

Reconciles what the system CLAIMED (signals, TP ladder, journal) against
BROKER TRUTH (open positions from the bridge). Findings:

  TP2_LOCK_MISSING   leak   price is beyond TP2 but the SL was never moved
                            to TP1 (the deterministic lock should have)
  ORPHAN_POSITION    warn   engine-magic position with no linked signal doc
  CLOSED_UNTRACKED   warn   broker position gone but the journal entry was
                            never finalized (tracker missed it)
  TP_MISMATCH        info   broker TP on the ticket differs from the TP the
                            signal says was chosen

Every scan is throttled per user; NEW findings are appended to the execution
lifecycle ledger (kind=TP_AUDIT) and leak-severity findings notify the user.
The scan NEVER modifies anything - it is an honest audit surface.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..db.store import get_store

SCAN_INTERVAL_S = 1800          # per-user throttle (background loop)
_CLOSED_GRACE_S = 12 * 3600     # closed-position journal grace before warn


def _finding(type_: str, severity: str, detail: str,
             signal_id: Optional[str] = None,
             ticket: Optional[int] = None) -> dict:
    return {"type": type_, "severity": severity, "detail": detail,
            "signal_id": signal_id, "ticket": ticket}


def scan_user(user_id: str, positions: Optional[List[dict]] = None,
              now: Optional[float] = None) -> List[dict]:
    """Pure reconciliation pass. positions can be injected (tests / reuse)."""
    store = get_store()
    now = now or time.time()

    from .mt5 import MAGIC, bridge_get, user_mode
    if user_mode(user_id) != "vps":
        return []
    if positions is None:
        positions = (bridge_get("/positions", timeout=8) or {}).get("positions") or []
    mine = [p for p in positions if int(p.get("magic") or 0) == MAGIC]

    sigs = store.list("signals", filters={"userId": user_id}, limit=500)
    by_sid = {str(s["signal_id"]): s for s in sigs if s.get("signal_id")}

    findings: List[dict] = []
    live_tickets = set()
    for p in mine:
        try:
            ticket = int(p.get("ticket") or 0)
        except Exception:
            continue
        live_tickets.add(ticket)
        sid = str(p.get("comment") or "").strip()
        sig = by_sid.get(sid)
        if not sig:
            findings.append(_finding("ORPHAN_POSITION", "warn",
                                     f"position #{ticket} has no linked signal "
                                     f"(comment {sid[:40]!r})", ticket=ticket))
            continue

        is_buy = str(p.get("type", "")).upper() == "BUY"
        price = float(p.get("price_current") or 0)
        sl = p.get("sl")
        tp1, tp2 = sig.get("tp1"), sig.get("tp2")

        # 1) TP2 hard-lock verification (deterministic rule must hold live)
        if tp1 and tp2 and price and sl is not None:
            beyond_tp2 = price >= float(tp2) if is_buy else price <= float(tp2)
            sl_looser = ((float(sl) < float(tp1) - 1e-9) if is_buy
                         else (float(sl) > float(tp1) + 1e-9))
            if beyond_tp2 and sl_looser:
                findings.append(_finding(
                    "TP2_LOCK_MISSING", "leak",
                    f"#{ticket} {sig.get('market')} is beyond TP2 but SL "
                    f"{sl} is still looser than TP1 {tp1} (lock not applied)",
                    signal_id=sid, ticket=ticket))

        # 2) broker TP vs the TP level the signal says was chosen
        sel = sig.get("tp_selection") or {}
        level = int(sel.get("level") or 2)
        chosen = sig.get(f"tp{max(1, min(3, level))}")
        broker_tp = p.get("tp")
        if chosen and broker_tp:
            try:
                # brokers round TP to the symbol's digits (XAUUSD 4199.47175099
                # -> 4199.472 is the SAME level); compare at 3 significant
                # decimals of the instrument's tick, not float-exact
                digits = len(str(broker_tp).split(".")[-1]) if "." in str(broker_tp) else 0
                tol = 10.0 ** (-max(0, min(digits, 6)) - 1)   # one ulp beyond broker precision
                if abs(float(broker_tp) - float(chosen)) > tol:
                    findings.append(_finding(
                        "TP_MISMATCH", "info",
                        f"#{ticket} broker TP {broker_tp} != signal TP{level} "
                        f"{chosen}", signal_id=sid, ticket=ticket))
            except Exception:
                pass

    # 3) closed on the broker but the journal never finalized
    for s in sigs:
        t = s.get("mt5_ticket")
        if not t or s.get("completed") or s.get("status") in ("WIN", "LOSS"):
            continue
        try:
            if int(t) in live_tickets:
                continue
        except Exception:
            continue
        done_at = s.get("mt5_executed_at")
        try:
            age = now - datetime.fromisoformat(
                str(done_at).replace("Z", "+00:00")).timestamp()
        except Exception:
            age = None
        if age is None or age > _CLOSED_GRACE_S:
            findings.append(_finding(
                "CLOSED_UNTRACKED", "warn",
                f"signal {s.get('signal_id')} ticket #{t} is no longer on the "
                "broker but the journal entry was never finalized",
                signal_id=str(s.get("signal_id")), ticket=int(t)))
    return findings


# ---------------------------------------------------------------------------
_last_scan: Dict[str, float] = {}
_last_signatures: Dict[str, set] = {}


def run_throttled(user_id: str, min_interval_s: int = SCAN_INTERVAL_S) -> List[dict]:
    """Background entry: throttled scan; NEW findings -> ledger + notify on
    leaks. Never raises, never modifies trading state."""
    now = time.time()
    if now - _last_scan.get(user_id, 0.0) < min_interval_s:
        return []
    _last_scan[user_id] = now
    try:
        findings = scan_user(user_id)
    except Exception:
        return []
    from .ledger import emit
    seen = _last_signatures.get(user_id, set())
    fresh = [f for f in findings
             if (f["type"], f.get("ticket"), f.get("signal_id")) not in seen]
    for f in fresh:
        emit(user_id, "TP_AUDIT", "FAILED" if f["severity"] == "leak" else "SKIPPED",
             signal_id=f.get("signal_id"), ticket=f.get("ticket"),
             detail=f"{f['type']}: {f['detail']}",
             extra={"severity": f["severity"], "audit": True})
    if fresh:
        _last_signatures[user_id] = {(f["type"], f.get("ticket"),
                                      f.get("signal_id")) for f in findings}
    leaks = [f for f in fresh if f["severity"] == "leak"]
    if leaks:
        try:
            from ..notifications.service import notify
            notify(user_id, "TP_AUDIT_LEAK",
                   f"TP AUDIT - {len(leaks)} leak(s) detected",
                   "\n".join(f["detail"] for f in leaks[:5]))
        except Exception:
            pass
    return fresh
