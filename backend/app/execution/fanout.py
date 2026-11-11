"""Send one signal to every eligible account, in parallel.

The legacy path in mt5.execute_signal is used when the only eligible
account is slot 1 with no per-account risk, suffix, bridge URL, or
daily-loss cap. That path is not this module.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from ..config import settings
from ..db.store import get_store
from ..notifications.service import notify
from ..risk.book import combined_gold_cap_usd
from . import mt5 as mt5_mod
from .mt5 import (
    MAGIC, PIP_SIZE, _bridge_ctx, _goals_doc, _registry_unprovisioned,
    calc_lot, submit_order,
)
from .order_ledger import LedgerError, claim as claim_order

_locks_guard = threading.Lock()
_account_locks: dict = {}


def _lock(account_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _account_locks.get(account_id)
        if lock is None:
            lock = threading.Lock()
            _account_locks[account_id] = lock
        return lock


def _rows(user_id: str, store=None) -> Optional[list]:
    """None means the accounts table is missing (legacy single bridge)."""
    store = store or get_store()
    try:
        return store.list("trading_accounts", filters={"userId": user_id}, limit=10)
    except Exception as exc:
        if _registry_unprovisioned(exc):
            return None
        raise


def _eligible(account: dict, strategy_id: Optional[str]) -> bool:
    if account.get("mode") == "signals_only":
        return False
    if not account.get("trading_enabled"):
        return False
    if account.get("kill_switch"):
        return False
    if strategy_id:
        picks = account.get("strategy_ids") or []
        if strategy_id not in picks:
            return False
    return True


def _legacy_shape(account: dict) -> bool:
    """True when this account should keep today's single-bridge send."""
    try:
        slot = int(account.get("bridge_slot") or 1)
    except (TypeError, ValueError):
        slot = 1
    if slot != 1:
        return False
    if str(account.get("bridge_url") or "").strip():
        return False
    if str(account.get("symbol_suffix") or "").strip():
        return False
    if account.get("risk_per_trade_usd"):
        return False
    if account.get("max_daily_loss_usd"):
        return False
    return True


def decide(user_id: str, strategy_id: Optional[str], store=None) -> Tuple[str, list]:
    """'legacy' | 'skip' | 'fanout', plus the accounts fan-out would use."""
    rows = _rows(user_id, store)
    if rows is None or not rows:
        return "legacy", []
    # Nothing is switched on: the existing disabled check reports that.
    switched_on = [
        row for row in rows
        if row.get("trading_enabled") and row.get("mode") != "signals_only"
        and not row.get("kill_switch")
    ]
    if not switched_on:
        return "legacy", []
    eligible = [row for row in switched_on if _eligible(row, strategy_id)]
    if not eligible:
        return "skip", []
    if len(eligible) == 1 and _legacy_shape(eligible[0]):
        return "legacy", []
    return "fanout", eligible


def _risk_usd(user_id: str, signal: dict, account: dict, store) -> float:
    try:
        from ..risk.strategy_rules import rules_for
        rules = rules_for(signal.get("strategy_id")) or {}
        if rules.get("risk_usd"):
            return float(rules["risk_usd"])
    except Exception:
        pass
    if account.get("risk_per_trade_usd"):
        return float(account["risk_per_trade_usd"])
    goals = _goals_doc(user_id) or {}
    doc = (store.list("settings", filters={"userId": user_id, "kind": "risk"}, limit=1) or [{}])[0]
    return float(doc.get("risk_per_trade_usd", 0.0) or 0.0) \
        or float(goals.get("risk_per_trade_usd", 50.0) or 0.0)


def _symbol(market: str, account: dict) -> str:
    suffix = str(account.get("symbol_suffix") or "").strip()
    if not suffix:
        return market
    if market.upper().endswith(suffix.upper()):
        return market
    return f"{market}{suffix}"


def _is_gold(market: str) -> bool:
    name = (market or "").upper()
    return name.startswith("XAU") or name.startswith("GOLD")


def _daily_loss_blocks(account: dict, magic: int) -> Optional[str]:
    cap = account.get("max_daily_loss_usd")
    if not cap:
        return None
    from datetime import datetime, timezone
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5_mod.bridge_get(f"/deals?since={int(start.timestamp())}", timeout=5)
    positions = mt5_mod.bridge_get("/positions", timeout=5)
    if deals is None or positions is None:
        return "daily-loss cap is set and the terminal could not be read"
    total = 0.0
    for deal in deals.get("deals") or []:
        if int(deal.get("magic") or 0) != magic:
            continue
        total += float(deal.get("profit") or 0) + float(deal.get("commission") or 0) \
            + float(deal.get("swap") or 0)
    for pos in positions.get("positions") or []:
        if int(pos.get("magic") or 0) != magic:
            continue
        total += float(pos.get("profit") or 0)
    if total <= -abs(float(cap)):
        return f"daily loss {total:.2f} reached the {cap} cap"
    return None


def _reserve_gold(state: dict, risk_usd: float) -> Optional[str]:
    cap = combined_gold_cap_usd()
    if cap is None:
        return None
    with state["lock"]:
        if state["used"] + risk_usd > float(cap) + 1e-9:
            return f"combined gold cap {cap} would be exceeded"
        state["used"] += risk_usd
    return None


def _place(signal: dict, user_id: str, account: dict, store, gold_state: dict) -> dict:
    label = account.get("label") or account.get("id")
    base = {"account_id": account.get("id"), "label": label}
    url = str(account.get("bridge_url") or "").strip() or settings.bridge_url
    magic = int(account.get("magic") or MAGIC)
    token = _bridge_ctx.set({"url": url, "token": settings.bridge_token})
    try:
        blocked = _daily_loss_blocks(account, magic)
        if blocked:
            return {**base, "status": "SKIPPED", "detail": blocked}
        market = signal.get("market")
        quote = mt5_mod.bridge_get(f"/quote?market={market}", timeout=5) or {}
        entry = float(signal["entry"])
        sl = float(signal["sl"])
        fill = None
        if quote.get("bid") and quote.get("ask"):
            fill = float(quote["ask"] if str(signal.get("direction")).upper() == "BUY" else quote["bid"])
        anchor = fill or entry
        sl_eff = sl + (anchor - entry)
        acct = mt5_mod.bridge_get("/account", timeout=5) or {}
        balance = float(acct.get("balance") or 0)
        if balance <= 0:
            return {**base, "status": "FAILED", "detail": "no balance from this bridge"}
        risk = _risk_usd(user_id, signal, account, store)
        risk_pct = (risk / balance) * 100.0 if risk > 0 else 1.0
        lots = calc_lot(market, anchor, sl_eff, balance, risk_pct)
        if lots <= 0 or market not in PIP_SIZE:
            return {**base, "status": "FAILED",
                    "detail": f"lot size refused lots={lots} market={market} bal={balance} risk={risk}"}
        if _is_gold(market):
            gold_block = _reserve_gold(gold_state, risk)
            if gold_block:
                return {**base, "status": "SKIPPED", "detail": gold_block}
        sign = 1.0 if str(signal.get("direction")).upper() == "BUY" else -1.0
        stop_d = abs(anchor - sl_eff)
        tp_eff = round(anchor + sign * 3.0 * stop_d, 5)
        broker_symbol = _symbol(market, account)
        payload = {
            "signal_id": signal.get("signal_id"),
            "symbol": broker_symbol,
            "direction": signal.get("direction"),
            "lots": lots,
            "sl": sl_eff,
            "tp": tp_eff,
            "magic": magic,
        }
        with _lock(str(account.get("id"))):
            try:
                claimed = claim_order(store, user_id, signal, account.get("id"),
                                       detail=f"{label} {lots} lots")
            except LedgerError as exc:
                return {**base, "status": "FAILED", "detail": f"ledger {exc}"}
            if claimed["action"] == "exists" and claimed["row"].get("stage") in ("CONFIRMED", "FAILED"):
                return {**base, "status": "SKIPPED", "detail": "already sent for this account",
                        "lots": lots}
            result = submit_order(payload, signal.get("signal_id"), magic)
        if result and result.get("ok"):
            from .ledger import emit
            emit(user_id, "ENTRY", "CONFIRMED", market=signal.get("market"),
                 signal_id=signal.get("signal_id"), signal_doc_id=signal.get("id"),
                 ticket=result.get("ticket"), detail=f"{label} filled",
                 idempotency_key=claimed["key"])
            return {**base, "status": "FILLED", "ticket": result.get("ticket"),
                    "lots": lots, "price": result.get("price"), "symbol": broker_symbol}
        if result and result.get("unknown"):
            return {**base, "status": "UNKNOWN", "detail": result.get("error") or "unknown",
                    "lots": lots}
        detail = (result or {}).get("detail") or (result or {}).get("error") or "rejected"
        return {**base, "status": "FAILED", "detail": str(detail)[:200], "lots": lots}
    except Exception as exc:
        return {**base, "status": "FAILED", "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        _bridge_ctx.reset(token)


def fanout_signal(signal: dict, user_id: str, store, accounts: List[dict]) -> list:
    gold_state = {"used": 0.0, "lock": threading.Lock()}
    results = []
    workers = max(1, min(5, len(accounts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_place, dict(signal), user_id, account, store, gold_state)
            for account in accounts
        ]
        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=20))
            except Exception as exc:
                results.append({"status": "FAILED", "detail": str(exc), "label": "?"})
    filled = [row for row in results if row.get("status") == "FILLED"]
    failed = [row for row in results if row.get("status") in ("FAILED", "UNKNOWN")]
    if filled and not failed:
        status = "SUBMITTED"
    elif filled:
        status = "PARTIAL"
    elif results and all(row.get("status") == "SKIPPED" for row in results):
        status = "SKIPPED_NO_ELIGIBLE_ACCOUNT"
    else:
        status = "FAILED"
    if signal.get("id"):
        store.update("signals", signal["id"], {
            "execution_status": status,
            "fanout_results": results,
        })
    lines = [f"{signal.get('market')} {signal.get('direction')}"]
    for row in results:
        lines.append(
            f"- {row.get('label') or row.get('account_id')}: {row.get('status')}"
            + (f" ticket {row.get('ticket')}" if row.get("ticket") else "")
            + (f" {row.get('lots')} lots" if row.get("lots") else "")
            + (f" ({row.get('detail')})" if row.get("detail") else "")
        )
    notify(user_id, "EXECUTION_FANOUT",
           f"{signal.get('market')} {signal.get('direction')} — {len(results)} accounts",
           "\n".join(lines),
           meta={"telegram_important": True},
           signal_id=signal.get("id"))
    return results
