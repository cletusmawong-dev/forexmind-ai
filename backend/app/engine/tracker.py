"""Signal & trade follow-up (SPEC §11, §16, §17, §18).

Follows every active signal candle-by-candle and records the lifecycle:
ACTIVE -> TP1 HIT -> TP2 HIT -> TP3 HIT / SL HIT / EXPIRED, with outcome
WIN / LOSS / EXPIRED. Signal results and USER trade results are tracked
SEPARATELY (a signal can win even if the user skipped it).
"""
from __future__ import annotations

import pandas as pd
from typing import Dict, Optional

from ..db.store import get_store
from ..learning.analysis import analyze_single_result
from ..notifications.service import notify

ACTIVE_STATUSES = ("ACTIVE", "TP1_HIT", "TP2_HIT")
TF_MINUTES = {"5M": 5, "15M": 15, "1H": 60, "4H": 240, "1D": 1440}


class SignalTracker:
    def __init__(self, provider):
        self.provider = provider
        # MFE/MAE bookkeeping (analytics ONLY - never read by any decision):
        # running max favorable / min adverse excursion in R per open signal.
        # Stamped onto the doc at TP-hit updates and at completion (no extra
        # DB writes per tick). If the process restarts mid-trade the window
        # restarts from that point (documented limitation).
        self._exc: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    def update_market(self, market: str) -> None:
        store = get_store()
        active = store.list("signals", filters={"market": market, "completed": False},
                            limit=500)
        if not active:
            return
        by_tf: Dict[str, dict] = {}
        for sig in active:
            tf = sig["timeframe"]
            if tf not in by_tf:
                candles = self.provider.get_candles(market, tf, limit=3)
                by_tf[tf] = candles.iloc[-1].to_dict() if candles is not None and len(candles) else None
            candle = by_tf.get(tf)
            if candle is not None:
                self._update_signal(sig, candle)

    # ------------------------------------------------------------------
    def _update_signal(self, sig: dict, candle: dict) -> None:
        store = get_store()
        # expiry: exceeded expire window without resolution
        try:
            entry_ts = pd.Timestamp(sig["candle_time"])
            cur_ts = candle.name if hasattr(candle, "name") else pd.Timestamp(candle.get("timestamp"))
            elapsed_bars = (cur_ts - entry_ts).total_seconds() / 60.0 / TF_MINUTES.get(sig["timeframe"], 15)
            expire_bars = sig.get("params", {}).get("expire_bars", 200)
            if elapsed_bars > expire_bars:
                self._complete(sig, status="EXPIRED", r=0.0, exit_price=None,
                               outcome="EXPIRED")
                return
        except Exception:
            pass

        long = sig["direction"] == "BUY"
        hi, lo = float(candle["high"]), float(candle["low"])
        sl = float(sig["sl"])
        tps = [sig.get("tp1"), sig.get("tp2"), sig.get("tp3")]
        tps = [t for t in tps if t is not None]

        # ---- MFE/MAE accumulation (analytics only; normalized in R) -------
        try:
            risk = float(sig.get("risk") or 0)
            if risk > 0:
                entry = float(sig["entry"])
                fav = (hi - entry) if long else (entry - lo)
                adv = (entry - lo) if long else (hi - entry)
                e = self._exc.setdefault(sig["id"], {"mfe": 0.0, "mae": 0.0})
                e["mfe"] = max(e["mfe"], fav / risk)
                e["mae"] = min(e["mae"], -adv / risk)
        except Exception:
            pass

        # conservative intrabar rule: SL first
        if (lo <= sl) if long else (hi >= sl):
            banked = sig.get("tp_hits", 0)
            rrs = {1: self._rr(sig, 1), 2: self._rr(sig, 2), 3: self._rr(sig, 3)}
            if banked:
                self._complete(sig, status="SL_HIT", r=rrs.get(banked, 0.0),
                               exit_price=sl, outcome="WIN",
                               note="SL hit after TP{} was banked".format(banked))
            else:
                self._complete(sig, status="SL_HIT", r=-1.0, exit_price=sl, outcome="LOSS")
            return

        tp_hit = sig.get("tp_hits", 0)
        for k, tp in enumerate(tps, start=1):
            if k <= tp_hit:
                continue
            if (hi >= tp) if long else (lo <= tp):
                tp_hit = k
                is_last = k == len(tps)
                rr = self._rr(sig, k)
                if is_last:
                    self._complete(sig, status=f"TP{k}_HIT", r=rr, exit_price=tp,
                                   outcome="WIN", tp_hits=k)
                else:
                    patch_tp = {
                        "status": f"TP{k}_HIT", "tp_hits": k,
                        "r_multiple": rr, "outcome": "WIN",
                    }
                    e = self._exc.get(sig["id"])
                    if e:
                        patch_tp["mfe_r"] = round(e["mfe"], 3)
                        patch_tp["mae_r"] = round(e["mae"], 3)
                    store.update("signals", sig["id"], patch_tp)
                    notify(sig.get("userId"), f"TP{k}_HIT",
                           f"TP{k} hit - {sig['market']} {sig['direction']}",
                           f"{sig['strategy_name']} | {sig['signal_id']}\n"
                           f"Price reached TP{k} ({tp:,.5g}). Tracking TP{k+1}...",
                           signal_id=sig["id"])
                    return  # conservative: one event per candle

    @staticmethod
    def _rr(sig: dict, level: int) -> float:
        risk = float(sig.get("risk") or 0)
        if risk <= 0:
            return 0.0
        tp = sig.get(f"tp{level}")
        if tp is None:
            return 0.0
        move = (float(tp) - float(sig["entry"])) * (1 if sig["direction"] == "BUY" else -1)
        return round(move / risk, 3)

    # ------------------------------------------------------------------
    def _complete(self, sig: dict, status: str, r: float, exit_price,
                  outcome: str, tp_hits: Optional[int] = None, note: str = ""):
        store = get_store()
        patch = {
            "status": status, "r_multiple": round(float(r), 3),
            "outcome": outcome, "completed": True,
            "completed_at": pd.Timestamp.utcnow().isoformat(),
            "exit_price": exit_price, "note": note,
        }
        if tp_hits is not None:
            patch["tp_hits"] = tp_hits
        e = self._exc.pop(sig["id"], None)   # MFE/MAE final stamp (analytics)
        if e:
            patch["mfe_r"] = round(e["mfe"], 3)
            patch["mae_r"] = round(e["mae"], 3)
        updated = store.update("signals", sig["id"], patch)

        notify(sig.get("userId"), "TRADE_COMPLETED",
               f"{outcome} - {sig['market']} {sig['direction']}",
               f"{sig['strategy_name']} | {sig['signal_id']}\nResult: {status} "
               f"({r:+.1f}R).",
               signal_id=sig["id"])
        store.create("agent_activity", {
            "userId": None, "kind": "ANALYSIS",
            "message": f"{sig['signal_id']} completed: {status} ({r:+.1f}R). "
                       "Trade analyzed.",
        })
        if updated:
            analyze_single_result(sig.get("userId"), updated)
            try:  # forensic analysis from Signal DNA vs historical comparables
                from ..learning.forensics import analyze as forensic_analyze
                fresh = store.get("signals", sig["id"]) or sig
                store.update("signals", sig["id"],
                             {"forensics": forensic_analyze(fresh, store)})
            except Exception:
                pass
        try:  # Trade Autopsy engine: paper-stamp + knowledge base (analytics
              # only - observes and proposes, never changes any behavior)
            from ..learning.autopsy import on_trade_completed
            on_trade_completed(updated or sig)
        except Exception:
            pass

        # keep the user's own trade result in sync (SPEC §17)
        if sig.get("user_action") == "entered":
            store.update("signals", sig["id"], {
                "user_trade_result": {"status": status, "r_multiple": patch["r_multiple"],
                                      "outcome": outcome},
            })
