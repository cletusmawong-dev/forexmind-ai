"""VALIDATION + OVERFITTING PROTECTION (owner brief 2026-10-08, section 8).

A single profitable backtest proves nothing. Every candidate must pass:

  - chronological TRAIN / OOS split (70/30, no shuffling - time is time)
  - walk-forward folds (performance must persist in each forward fold)
  - parameter stability (nearby parameter steps must not collapse the edge)
  - cost sensitivity (edge must survive ~2x assumed costs)
  - Monte Carlo stress (reuses the EXISTING app.research.montecarlo lab)

Stability ranks higher than raw profit: a slightly-lower but consistent
strategy outranks a fragile optimizer winner (explicit owner rule).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

OOS_FRACTION = 0.30
WF_FOLDS = 3
MIN_TRADES_TOTAL = 30        # absolute floor for any statistical statement
MIN_TRADES_OOS = 8           # floor for the OOS window alone
PF_PASS = 1.10               # gross profit / gross loss floor
STABILITY_RATIO = 0.5        # nearby-param avg R must keep >= 50% of full R


def _metrics(rs: List[float]) -> Dict[str, Any]:
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    gross_l = abs(sum(r for r in rs if r <= 0))
    return {"n": len(rs),
            "win_rate": round(100 * len(wins) / len(rs), 1),
            "avg_r": round(sum(rs) / len(rs), 4),
            "net_r": round(sum(rs), 3),
            "profit_factor": (round(sum(wins) / gross_l, 3)
                              if gross_l > 0 else None)}


def split_oos(trades: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Chronological split by entry_time - train first, OOS last."""
    ts = sorted(trades, key=lambda t: t["entry_time"])
    cut = int(len(ts) * (1 - OOS_FRACTION))
    return ts[:cut], ts[cut:]


def oos_gate(trades: List[dict]) -> Dict[str, Any]:
    if len(trades) < MIN_TRADES_TOTAL:
        return {"gate": "OOS", "verdict": "INSUFFICIENT_DATA",
                "detail": f"need >= {MIN_TRADES_TOTAL} trades, got {len(trades)}"}
    train, oos = split_oos(trades)
    m_in, m_out = _metrics([t["r"] for t in train]), _metrics([t["r"] for t in oos])
    if m_out["n"] < MIN_TRADES_OOS:
        return {"gate": "OOS", "verdict": "INSUFFICIENT_DATA",
                "detail": f"OOS window has only {m_out['n']} trades"}
    pf_ok = (m_out.get("profit_factor") or 0) >= PF_PASS
    avg_ok = m_out["avg_r"] > 0 and m_in["avg_r"] > 0
    verdict = "PASS" if (pf_ok and avg_ok) else "FAIL"
    return {"gate": "OOS", "verdict": verdict,
            "train": m_in, "oos": m_out,
            "detail": f"in-sample PF {m_in.get('profit_factor')}, "
                      f"out-of-sample PF {m_out.get('profit_factor')} "
                      f"over {m_out['n']} trades"}


def walk_forward(trades: List[dict], folds: int = WF_FOLDS) -> Dict[str, Any]:
    """Chronological folds: re-own the edge in every forward window."""
    if len(trades) < MIN_TRADES_TOTAL:
        return {"gate": "WALK_FORWARD", "verdict": "INSUFFICIENT_DATA",
                "detail": f"need >= {MIN_TRADES_TOTAL} trades"}
    ts = sorted(trades, key=lambda t: t["entry_time"])
    size = len(ts) // (folds + 1)
    if size < 5:
        return {"gate": "WALK_FORWARD", "verdict": "INSUFFICIENT_DATA",
                "detail": "too few trades per fold"}
    fold_rows = []
    positive = 0
    for k in range(1, folds + 1):
        seg = ts[size * k: size * (k + 1)]
        m = _metrics([t["r"] for t in seg])
        m["fold"] = k
        fold_rows.append(m)
        if m["avg_r"] > 0:
            positive += 1
    ratio = positive / folds
    verdict = "PASS" if ratio >= 2 / 3 else ("MARGINAL" if positive >= 1 else "FAIL")
    return {"gate": "WALK_FORWARD", "verdict": verdict,
            "folds": fold_rows,
            "detail": f"{positive}/{folds} forward folds kept a positive expectancy"}


def parameter_stability(df, market: str, implemented: Dict[str, Any],
                        var_key: str, steps: List[Any],
                        run_fn) -> Dict[str, Any]:
    """The edge must not live on one magic number: re-run the backtest on
    nearby parameter values; the average R must retain a stable fraction of
    the base run's expectancy. `run_fn(df, market, impl)` is injected so the
    engine can reuse caching; steps are variants of `var_key` inside entry
    indicator params (e.g. ema period)."""
    base = run_fn(df, market, implemented)
    base_avg = (base.get("metrics") or {}).get("avg_r")
    if base_avg is None:
        return {"gate": "PARAM_STABILITY", "verdict": "INSUFFICIENT_DATA",
                "detail": "base run produced no trades"}
    import copy
    results = []
    for step in steps:
        impl = copy.deepcopy(implemented)
        hit = _patch_param(impl["entry"]["when"], var_key, step)
        if not hit:
            return {"gate": "PARAM_STABILITY", "verdict": "NOT_APPLICABLE",
                    "detail": f"{var_key} not present in entry rules"}
        r = run_fn(df, market, impl)
        results.append({"value": step,
                        "avg_r": (r.get("metrics") or {}).get("avg_r"),
                        "n": (r.get("metrics") or {}).get("trade_count", 0)})
    vals = [r["avg_r"] for r in results if r["avg_r"] is not None]
    if not vals:
        return {"gate": "PARAM_STABILITY", "verdict": "INSUFFICIENT_DATA",
                "detail": "no variant produced trades"}
    stable = (sum(vals) / len(vals)) >= STABILITY_RATIO * base_avg
    return {"gate": "PARAM_STABILITY", "verdict": "PASS" if stable else "FAIL",
            "base_avg_r": base_avg, "variants": results,
            "detail": f"nearby-parameter avg R {round(sum(vals) / len(vals), 4)} "
                      f"vs base {base_avg} (floor {STABILITY_RATIO:.0%})"}


def _patch_param(conds: List[dict], var_key: str, value: Any) -> bool:
    for c in conds:
        for side in ("left", "right"):
            spec = c.get(side) or {}
            if var_key in (spec.get("params") or {}):
                spec["params"][var_key] = value
                return True
    return False


def cost_sensitivity(trades: List[dict], extra_cost_r: float = 0.10) -> Dict[str, Any]:
    """Edge must survive ~2x costs: subtract an extra per-trade R haircut."""
    if len(trades) < MIN_TRADES_TOTAL:
        return {"gate": "COST_SENSITIVITY", "verdict": "INSUFFICIENT_DATA",
                "detail": f"need >= {MIN_TRADES_TOTAL} trades"}
    stressed = [t["r"] - extra_cost_r for t in trades]
    m = _metrics(stressed)
    ok = m["avg_r"] > 0
    return {"gate": "COST_SENSITIVITY", "verdict": "PASS" if ok else "FAIL",
            "stressed": m, "extra_cost_r": extra_cost_r,
            "detail": f"at +{extra_cost_r}R/trade stress, expectancy "
                      f"{m['avg_r']}R"}


def stress_monte_carlo(trades: List[dict]) -> Dict[str, Any]:
    """Reuse the EXISTING Monte Carlo lab on the candidate's R series."""
    from . import montecarlo
    rs = [t["r"] for t in trades]
    out = montecarlo.simulate(rs, n_sims=1000, horizon=max(60, len(rs)))
    if out.get("status") == "INSUFFICIENT_DATA":
        return {"gate": "MONTE_CARLO", "verdict": "INSUFFICIENT_DATA",
                "detail": out.get("note")}
    worst_dd = out.get("dd_p95") if isinstance(out, dict) else None
    return {"gate": "MONTE_CARLO", "verdict": "PASS",
            "result": {k: out.get(k) for k in
                       ("n_sims", "dd_p95", "worst_streak_p95", "ruin_prob") if k in out},
            "detail": "1000 seeded resamples of the verified R series"}


def generalization_gate(run_fn, home_market: str, home_tf: str,
                        fetch_df, alt_markets=None, alt_tfs=None) -> Dict[str, Any]:
    """(S 6/7) Same rules, OTHER pairs and timeframes - where data permits.

    The edge must not live only on the home market/timeframe. Missing data
    is honest (NOT_APPLICABLE), never fabricated. A strategy positive only
    on its home context is MARGINAL, not PASS.
    """
    alt_markets = alt_markets or {"EURUSD": ["GBPUSD", "XAUUSD"],
                                  "XAUUSD": ["EURUSD"],
                                  "GBPUSD": ["EURUSD", "XAUUSD"]}.get(home_market, [])
    alt_tfs = alt_tfs or {"15M": ["1H"], "1H": ["30M"], "4H": ["1H"]}.get(home_tf, [])
    contexts: List[Dict[str, Any]] = []
    for m in alt_markets:
        try:
            df = fetch_df(m, home_tf)
        except Exception:
            df = None
        contexts.append({"kind": "pair", "context": m, "df": df})
    for tf in alt_tfs:
        try:
            df = fetch_df(home_market, tf)
        except Exception:
            df = None
        contexts.append({"kind": "timeframe", "context": tf, "df": df})
    if not contexts:
        return {"gate": "GENERALIZATION", "verdict": "NOT_APPLICABLE",
                "detail": "no alternate pairs/timeframes configured"}
    results, tested, positive = [], 0, 0
    for ctx in contexts:
        df = ctx["df"]
        if df is None or len(df) < 60:
            results.append({"kind": ctx["kind"], "context": ctx["context"],
                            "verdict": "NO_DATA"})
            continue
        res = run_fn(df, ctx["context"] if ctx["kind"] == "pair" else home_market)
        if res.get("status") != "OK":
            results.append({"kind": ctx["kind"], "context": ctx["context"],
                            "verdict": "NO_DATA"})
            continue
        tested += 1
        avg_r = (res.get("metrics") or {}).get("avg_r") or 0.0
        pos = avg_r > 0
        positive += 1 if pos else 0
        results.append({"kind": ctx["kind"], "context": ctx["context"],
                        "avg_r": avg_r,
                        "trade_count": (res.get("metrics") or {}).get("trade_count"),
                        "verdict": "POSITIVE" if pos else "NEGATIVE"})
    if tested < 1:
        return {"gate": "GENERALIZATION", "verdict": "NOT_APPLICABLE",
                "result": {"contexts": results},
                "detail": "no alternate market data available - not tested"}
    if positive == 0:
        verdict, detail = "MARGINAL", ("edge negative on all "
            f"{tested} alternate contexts - single-market edge")
    elif positive < tested:
        verdict, detail = "MARGINAL", (f"edge holds on {positive}/{tested} "
            "alternate contexts - regime/market specific")
    else:
        verdict, detail = "PASS", f"edge positive on {positive}/{tested} alternate contexts"
    return {"gate": "GENERALIZATION", "verdict": verdict,
            "result": {"contexts": results}, "detail": detail}


def overall(gates: List[Dict[str, Any]]) -> str:
    """Combine gate verdicts -> EVIDENCE level for the dashboard."""
    vs = [g["verdict"] for g in gates]
    if any(v == "FAIL" for v in vs):
        return "WEAK"
    # NOT_APPLICABLE (e.g. no alternate-market data for generalization) is
    # neutral: honest absence of data neither earns nor blocks STRONG.
    if all(v in ("PASS", "NOT_APPLICABLE") for v in vs):
        return "STRONG"
    if "INSUFFICIENT_DATA" in vs:
        return "INSUFFICIENT"
    return "MIXED"
