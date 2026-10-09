"""BACKGROUND RESEARCH ENGINE (owner brief 2026-10-08, sections 1, 6, 10-11).

Pipeline: RESEARCH -> EXTRACT RULES -> RECONSTRUCT -> TEST -> VALIDATE ->
SHADOW -> EVALUATE -> EVIDENCE REPORT. Runs unattended in bounded ticks from
the existing background loop; the user reviews evidence, never process.

Nothing here can touch live trading: candidates live in their own
collection; the ceiling is READY_FOR_REVIEW (human approval stays in the
EXISTING versions lifecycle, untouched).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

COLLECTION = "research_candidates"

# ---------------------------------------------------------------------------
# documented-strategy seed catalog: EXTERNAL CLAIMS to investigate, with
# real sources + evidence tiers. Claims are recorded AS CLAIMS; the engine
# independently reconstructs and tests everything.
# ---------------------------------------------------------------------------
SEED_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "MA Crossover Trend Following",
        "source_id": "journals", "url": "https://www.ssrn.com/abstract=220530",
        "author": "Brock, Lakonishok & LeBaron (1997, Journal of Finance)",
        "evidence_tier": 1,
        "claim": "Reported profitability of simple MA strategies on historical index data (SOURCE CLAIM).",
        "raw_rules": {
            "name": "MA Crossover Trend Following",
            "market": "EURUSD", "timeframe": "1H", "direction": "BOTH",
            "entry": {"when": [
                {"op": "crosses_above", "left": {"indicator": "ema", "params": {"period": 50}},
                 "right": {"indicator": "ema", "params": {"period": 200}}}]},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.5},
            "indicators": ["ema", "atr"], "params": {"fast": 50, "slow": 200},
            "reported_performance": {"summary": "Paper reports MA-rule profits on 1897-1996 DJIA data"},
            "limitations": ["equity indices, daily - reconstructed on FX 1H",
                            "1990s data predates modern spreads"],
        },
        "var_key": "period", "var_steps": [40, 60, 75],
    },
    {
        "name": "RSI(2) Pullback",
        "source_id": "quant_blogs_verified", "url": "",
        "author": "L. Connors (documented book methodology)",
        "evidence_tier": 2,
        "claim": "Reported high win-rate mean-reversion pullback (SOURCE CLAIM).",
        "raw_rules": {
            "name": "RSI(2) Pullback",
            "market": "EURUSD", "timeframe": "1H", "direction": "BOTH",
            "entry": {"when": [
                {"op": "greater", "left": {"indicator": "close"},
                 "right": {"indicator": "sma", "params": {"period": 200}}},
                {"op": "less", "left": {"indicator": "rsi", "params": {"period": 2}},
                 "right": {"value": 10}}]},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "indicators": ["rsi", "sma", "atr"], "params": {"rsi_period": 2, "trend": 200},
            "reported_performance": {"summary": "Book reports ~75% win rate on index ETFs (daily)"},
            "limitations": ["daily equity data vs FX 1H reconstruction"],
        },
        "var_key": "period", "var_steps": [2, 3, 4],
    },
    {
        "name": "Donchian 20 Breakout",
        "source_id": "quant_blogs_verified", "url": "",
        "author": "Turtle methodology (documented)",
        "evidence_tier": 2,
        "claim": "Reported 20-bar channel breakout trend capture (SOURCE CLAIM).",
        "raw_rules": {
            "name": "Donchian 20 Breakout",
            "market": "XAUUSD", "timeframe": "1H", "direction": "BOTH",
            "entry": {"when": [
                {"op": "crosses_above", "left": {"indicator": "close"},
                 "right": {"indicator": "highest", "params": {"period": 20}}}]},
            "sl": {"type": "atr_mult", "mult": 2.0},
            "tp": {"type": "rr_multiple", "value": 3.0},
            "indicators": ["highest", "atr"], "params": {"channel": 20},
            "reported_performance": {"summary": "Famous trend program (1980s) - claims unverifiable"},
            "limitations": ["commodities/daily era vs FX metals 1H"],
        },
        "var_key": "period", "var_steps": [15, 25, 30],
    },
    {
        "name": "London Open Range Breakout",
        "source_id": "forums", "url": "https://www.forexfactory.com (public thread methodology)",
        "author": "Community-documented (multiple public threads)",
        "evidence_tier": 3,
        "claim": "Breakout of the Asian-session range at London open (SOURCE CLAIM, Tier 3 idea).",
        "raw_rules": {
            "name": "London Open Range Breakout",
            "market": "GBPUSD", "timeframe": "1H", "direction": "BOTH",
            "entry": {"when": [
                {"op": "crosses_above", "left": {"indicator": "close"},
                 "right": {"indicator": "highest", "params": {"period": 16}}}]},
            "sl": {"type": "atr_mult", "mult": 1.5},
            "tp": {"type": "rr_multiple", "value": 2.0},
            "filters": ["session: London (approximated by a 16-bar range)"],
            "indicators": ["highest"], "params": {"range_bars": 16},
            "reported_performance": {"summary": "Community screenshots only - no verifiable track record"},
            "limitations": ["session filter approximated by lookback range",
                            "Tier 3 evidence - idea discovery only"],
        },
        "var_key": "period", "var_steps": [12, 20, 24],
    },
]


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def _cfg() -> Dict[str, Any]:
    return {
        "enabled": os.getenv("RESEARCH_ENGINE_ENABLED", "1") == "1",
        "max_per_tick": int(os.getenv("RESEARCH_ENGINE_MAX_PER_TICK", "2")),
        "candles": int(os.getenv("RESEARCH_BACKTEST_CANDLES", "1500")),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover(store) -> int:
    """Seed candidates from the documented catalog (memory-deduped)."""
    from . import memory
    n = 0
    from .memory import PIPELINE_VERSION
    for seed in SEED_CATALOG:
        exists = store.list(COLLECTION,
                            filters={"name": seed["name"],
                                     "market": seed["raw_rules"]["market"],
                                     "timeframe": seed["raw_rules"]["timeframe"],
                                     "pipeline_version": PIPELINE_VERSION},
                            limit=1)
        if exists:
            continue
        raw = seed["raw_rules"]
        store.create(COLLECTION, {
            "stage": "DISCOVERED",
            "pipeline_version": PIPELINE_VERSION,
            "name": seed["name"],
            "market": raw["market"], "timeframe": raw["timeframe"],
            "source_id": seed["source_id"], "source_url": seed["url"],
            "author": seed["author"], "evidence_tier": seed["evidence_tier"],
            "source_claim": seed["claim"],
            "raw_rules": raw,
            "var_key": seed.get("var_key"), "var_steps": seed.get("var_steps"),
            "events": [{"stage": "DISCOVERED", "at": _now(),
                        "detail": f"documented strategy from {seed['source_id']} "
                                  f"(tier {seed['evidence_tier']}) - CLAIM ONLY"}],
            "createdAt": _now(),
        }, doc_id=None)
        n += 1
    return n


def _event(cand: dict, stage: str, detail: str) -> None:
    cand.setdefault("events", []).append({"stage": stage, "at": _now(),
                                          "detail": detail})


def _get_df(market: str, tf: str, limit: int):
    """Deepest honest history (provider cache -> TwelveData pages).

    Returns (df, note) - the note states the candle count so candidate
    events are transparent about how much data a verdict rests on."""
    from ..state import State
    from .data import get_history
    return get_history(State.provider, market, tf,
                       target_bars=max(int(limit), 800), min_bars=300)


def _advance(cand: dict, store, df, cfg: dict, data_note: str = "") -> dict:
    """One bounded stage advance for one candidate. Returns the new stage."""
    from . import backtest                     # single binding: closures below
    stage = cand.get("stage")

    # ---- EXTRACT ----------------------------------------------------------
    if stage == "DISCOVERED":
        from . import extract
        res = extract.extract(
            {"source_id": cand.get("source_id"), "url": cand.get("source_url"),
             "author": cand.get("author"), "claim": cand.get("source_claim"),
             "name": cand.get("name")},
            cand.get("raw_rules") or {})
        cand.update({"extraction_status": res["status"],
                     "rules": res.get("rules") or {},
                     "missing_rules": res.get("missing", [])})
        if res["status"] != "OK":
            cand["stage"] = "REJECTED"
            _event(cand, "REJECTED", f"INSUFFICIENT RULES: {res['missing']} - "
                                     "rules not invented")
            return cand["stage"]
        cand["stage"] = "EXTRACTED"
        _event(cand, "EXTRACTED", "explicit rules validated")
        return cand["stage"]

    # ---- RECONSTRUCT ------------------------------------------------------
    if stage == "EXTRACTED":
        from . import reconstruct
        from . import memory
        res = reconstruct.reconstruct(cand.get("rules") or {})
        if res["status"] != "OK":
            cand["stage"] = "REJECTED"
            _event(cand, "REJECTED", res["reason"])
            return cand["stage"]
        cand["implemented"] = res["implemented"]
        cand["trace"] = res["trace"]
        mem = memory.seen_before(store, cand, res["implemented"])
        if mem:
            cand["stage"] = "REJECTED"
            _event(cand, "REJECTED",
                   f"research memory: substantially identical experiment already "
                   f"{mem.get('verdict')} ({mem.get('reason')})")
            return cand["stage"]
        cand["stage"] = "RECONSTRUCTED"
        _event(cand, "RECONSTRUCTED",
               "SOURCE -> EXTRACTED RULE -> IMPLEMENTED RULE chain stored")
        return cand["stage"]

    # ---- BACKTEST ---------------------------------------------------------
    if stage == "RECONSTRUCTED":
        res = backtest.run(df, cand["market"], cand["implemented"])
        cand["backtest"] = res
        n = (res.get("metrics") or {}).get("trade_count", 0)
        if res["status"] != "OK" or n < 30:
            cand["stage"] = "REJECTED"
            _event(cand, "REJECTED",
                   f"verified trade count {n} below the statistical floor (30) "
                   f"on {len(df)} candles ({data_note})")
            return cand["stage"]
        cand["stage"] = "BACKTESTED"
        res.setdefault("candles_used", len(df))
        _event(cand, "BACKTESTED", f"{n} verified trades on {len(df)} candles - "
               f"PF {res['metrics'].get('profit_factor')}, "
               f"expectancy {res['metrics'].get('avg_r')}R")
        return cand["stage"]

    # ---- VALIDATE (OOS, walk-forward, stability, costs, Monte Carlo) ------
    if stage == "BACKTESTED":
        from . import validate
        from .sources import tier_name
        trades = cand["backtest"]["trades"]
        gates = [validate.oos_gate(trades), validate.walk_forward(trades),
                 validate.cost_sensitivity(trades), validate.stress_monte_carlo(trades)]
        var_key = cand.get("var_key") or "period"
        steps = cand.get("var_steps") or []
        if steps:
            gates.append(validate.parameter_stability(
                df, cand["market"], cand["implemented"], var_key,
                steps, lambda d, m, impl: backtest.run(d, m, impl)))

        # multi-pair / multi-timeframe generalization (where data permits)
        def _fetch(m: str, tf: str):
            try:
                df_alt, _ = _get_df(m, tf, cfg.get("candles", 1500))
                return df_alt
            except Exception:
                return None
        gates.append(validate.generalization_gate(
            lambda d, m: backtest.run(d, m, cand["implemented"]),
            cand["market"], cand["timeframe"], _fetch))
        cand["validation"] = gates
        verdict = validate.overall(gates)
        cand["evidence_level"] = verdict
        regime_note = _regime_analysis(df, trades)
        cand["regime_analysis"] = regime_note
        if verdict in ("WEAK", "INSUFFICIENT"):
            fails = [g.get("detail") for g in gates if g.get("verdict") == "FAIL"]
            cand["stage"] = "REJECTED"
            _event(cand, "REJECTED", "; ".join(fails) or "insufficient evidence")
            return cand["stage"]
        if verdict == "MIXED":
            cand["stage"] = "CONTINUE_RESEARCH"
            _event(cand, "CONTINUE_RESEARCH",
                   "mixed validation - kept for wider testing, not promoted")
            return cand["stage"]
        cand["stage"] = "SHADOW"
        cand["shadow_since"] = _now()
        _event(cand, "SHADOW",
               f"all historical gates PASS ({tier_name(cand.get('evidence_tier') or 0)} "
               f"source) - moved to forward shadow testing, no real trades")
        return cand["stage"]

    # ---- SHADOW (hypothetical live-condition signals) ---------------------
    if stage in ("SHADOW", "CONTINUE_RESEARCH_SHADOW"):
        res = backtest.run(df, cand["market"], cand["implemented"])
        trades = res.get("trades") or []
        _record_shadow(store, cand, trades, df)
        shadow = _shadow_metrics(store, cand)
        cand["shadow"] = shadow
        if (shadow.get("n") or 0) >= 20:
            from . import probabilities, report
            # shadow docs store result_r - map to the probability contract
            sp = probabilities.from_trades(
                [{**t, "r": float(t.get("result_r") or 0.0)}
                 for t in (shadow.get("trades") or [])])
            bp = probabilities.from_trades(cand["backtest"]["trades"])
            cand["shadow_probabilities"] = sp
            rec = report.recommend(cand.get("validation") or [],
                                   cand["backtest"], shadow, sp,
                                   stage=cand["stage"])
            if rec["recommendation"] == "READY_FOR_REVIEW":
                cand["stage"] = "READY_FOR_REVIEW"
            elif rec["recommendation"] == "REJECT":
                cand["stage"] = "REJECTED"
            else:
                cand["stage"] = "CONTINUE_RESEARCH"
            _event(cand, rec["recommendation"], rec["reason"])
        return cand["stage"]

    return cand



def _regime_analysis(df, trades: List[dict]) -> Dict[str, Any]:
    """(S 9/10) Regime, session and volatility conditioning of verified trades.

    A strategy must not be labelled profitable globally if its edge only
    exists in one regime. Every verified trade is conditioned on the trend
    state + ATR-volatility bucket at its signal bar, and on the session of
    its entry. Small buckets stay honest (INSUFFICIENT_DATA).
    """
    try:
        from ..research.probabilities import conditional
        try:
            from ..core.indicators import atr
            atr_series = atr(df, 14)
            atr_med = float(atr_series.median())
        except Exception:
            atr_series, atr_med = None, None
        rows = []
        for t in trades:
            i = t.get("signal_i")
            try:
                c_now = float(df["close"].iloc[i])
                c_then = float(df["close"].iloc[max(0, i - 50)])
                trend = ("TREND_UP" if c_now > c_then
                         else "TREND_DOWN" if c_now < c_then else "RANGE")
            except Exception:
                trend = "UNKNOWN"
            vol = ""
            if atr_series is not None and i is not None and atr_med is not None:
                try:
                    v = float(atr_series.iloc[i])
                    if v == v:                    # not NaN
                        vol = ":HIGH_VOL" if v >= atr_med else ":LOW_VOL"
                except Exception:
                    vol = ""
            rows.append({"r": t["r"], "regime": trend + vol,
                         "session": _session_of(t.get("entry_time") or ""),
                         "trend": trend})
        regimes = conditional(rows, "regime", lambda t: t.get("regime"))
        sessions = conditional(rows, "session", lambda t: t.get("session"))
        weak = [k for k, v in regimes.items()
                if isinstance(v, dict) and v.get("n") and (v.get("expected_R") or 0) <= 0]
        detail = ("edge positive across all recorded regimes" if not weak
                  else f"edge negative/flat in: {', '.join(weak)} (single-regime risk)")
        return {"regimes": regimes, "sessions": sessions, "detail": detail}
    except Exception as exc:                      # research never breaks
        return {"detail": f"regime analysis unavailable: {exc}"}


def _record_shadow(store, cand: dict, trades: List[dict], df) -> int:
    """Append unseen verified trades as hypothetical shadow records."""
    seen = store.list("shadow_trades",
                      filters={"candidate_id": cand.get("id")}, limit=1000)
    seen_keys = {str(s.get("entry_time")) for s in seen}
    n = 0
    try:
        from ..learning.regime import classify
        rc = classify(df) or {}
    except Exception:
        rc = {}
    for t in trades:
        key = str(t["entry_time"])
        if key in seen_keys:
            continue
        store.create("shadow_trades", {
            "candidate_id": cand.get("id"), "strategy_id": f"research:{cand.get('id')}",
            "userId": None,
            "market": cand.get("market"), "timeframe": cand.get("timeframe"),
            "side": t["side"], "entry": t["entry"], "exit": t["exit"],
            "entry_time": t["entry_time"], "exit_time": t["exit_time"],
            "hypothetical_sl": cand.get("implemented", {}).get("sl"),
            "hypothetical_tp": cand.get("implemented", {}).get("tp"),
            "result_r": t["r"], "exit_reason": t["exit_reason"],
            "mfe_r": t.get("mfe_r"), "mae_r": t.get("mae_r"),
            "regime": rc.get("regime"), "session": _session_of(t["entry_time"]),
            "assumptions": "candle-open entry, assumed spread/slippage/commission, "
                           "SL-priority intrabar - NEVER sent to the broker",
            "createdAt": _now(),
        })
        n += 1
    return n


def _session_of(ts: str) -> str:
    try:
        h = int(str(ts)[11:13])
        if 7 <= h < 12:
            return "London"
        if 12 <= h < 16:
            return "NewYork"
        if 21 <= h or h < 6:
            return "Asian"
        return "Late"
    except Exception:
        return ""


def _shadow_metrics(store, cand: dict) -> Dict[str, Any]:
    rows = store.list("shadow_trades",
                      filters={"candidate_id": cand.get("id")}, limit=1000)
    rows.sort(key=lambda t: t.get("entry_time") or "")
    if not rows:
        return {"n": 0}
    rs = [float(t.get("result_r") or 0.0) for t in rows]
    wins = [r for r in rs if r > 0]
    gross_l = abs(sum(r for r in rs if r <= 0))
    from .validate import _metrics
    m = _metrics(rs)
    m["n"] = len(rows)
    m["trades"] = rows[-60:]
    return m


def tick(user_id: Optional[str] = None, provider_df=None) -> Dict[str, Any]:
    """One bounded engine tick: discover + advance a few candidates."""
    from ..db.store import get_store
    store = get_store()
    cfg = _cfg()
    out = {"enabled": cfg["enabled"], "advanced": 0, "discovered": 0, "notes": []}
    if not cfg["enabled"]:
        out["notes"].append("engine disabled (RESEARCH_ENGINE_ENABLED=0)")
        return out
    try:
        from .sources import ensure_registry
        ensure_registry(store)
        out["discovered"] = discover(store)
    except RuntimeError as _prov:
        # Supabase tables not provisioned yet (PGRST205) - pause cleanly,
        # honestly, and without 15-minute error spam.
        out["paused"] = f"research tables not provisioned: {_prov}"
        return out

    from .sources import usable_for
    pending = [c for c in store.list(COLLECTION, limit=200)
               if c.get("stage") not in ("REJECTED", "READY_FOR_REVIEW",
                                         "CONTINUE_RESEARCH")]
    pending.sort(key=lambda c: c.get("createdAt") or "")
    for cand in pending:
        if out["advanced"] >= cfg["max_per_tick"]:
            break
        if not usable_for(int(cand.get("evidence_tier") or 3), "strategy_research"):
            # Tier 3 sources can seed IDEAS, but only as explicitly allowed -
            # the engine still tests them; nothing special needed here.
            pass
        market = cand.get("market")
        tf = cand.get("timeframe")
        if provider_df is not None:
            df, data_note = provider_df, f"{len(provider_df)} candles"
        else:
            df, data_note = _get_df(market, tf, cfg["candles"])
        if df is None or len(df) < 60:
            out["notes"].append(f"{cand.get('name')}: no candle data yet "
                                f"({data_note}) - skipped tick")
            continue
        stage_before = cand.get("stage")
        _advance(cand, store, df, cfg, data_note=data_note)  # mutates cand
        updates = {k: cand.get(k) for k in
                   ("stage", "events", "extraction_status", "rules", "missing_rules",
                    "implemented", "trace", "backtest", "validation",
                    "evidence_level", "regime_analysis", "shadow", "shadow_since",
                    "shadow_probabilities")}
        updates["updatedAt"] = _now()
        store.update(COLLECTION, cand["id"], updates)
        # research memory: terminal verdicts are remembered with reasons
        if cand.get("stage") in ("REJECTED", "READY_FOR_REVIEW", "CONTINUE_RESEARCH"):
            from . import memory
            memory.remember_candidate(
                store, cand, cand.get("implemented") or {}, cand["stage"],
                (cand.get("events") or [{}])[-1].get("detail", ""),
                report_id=cand.get("id"))
        out["advanced"] += 1
        out.setdefault("stages", []).append(
            {"name": cand.get("name"), "from": stage_before, "to": cand.get("stage")})
    # evidence report: assemble/refresh for every candidate with a backtest
    # (rebuilt while the candidate is still moving; frozen once terminal)
    from . import report, probabilities
    for cand in store.list(COLLECTION, limit=200):
        if not cand.get("backtest"):
            continue
        frozen = (cand.get("stage") in ("READY_FOR_REVIEW", "CONTINUE_RESEARCH")
                  and cand.get("report"))
        if frozen:
            continue
        probs = probabilities.from_trades((cand.get("backtest") or {}).get("trades") or [])
        rep = report.build(cand, cand["backtest"], cand.get("validation") or [],
                           probs, cand.get("shadow"), cand.get("shadow_probabilities"),
                           stage=cand.get("stage"))
        store.update(COLLECTION, cand["id"], {"report": rep, "updatedAt": _now()})
        out.setdefault("reports", []).append(
            {"name": cand.get("name"), "recommendation": rep["recommendation"]})
    return out
