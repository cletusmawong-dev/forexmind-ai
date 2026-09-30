"""Volume Profile + Pivot Levels [ChartPrime] strategy tests.

The entry modes are user-selected (2026-09-30); the profile/pivot math must
match the Pine source EXACTLY (verified here against a naive literal
implementation of the source's loops), SL/TP must equal the 9/21 EMA
strategy's math, and everything must be causal (bar i sees only bars <= i).

The controlled frames below are hand-solved against the bin arithmetic:
window 100 bars, 5 bins, heavy node of closes at one price so the strongest
bins are known exactly, and a pivot placed within 1 bin_size of that node
(the source only volume-confirms pivots that sit ON the nodes).
"""
import numpy as np
import pandas as pd
import pytest

from app.strategies import all_strategies, get_strategy


# ----------------------------------------------------------------- helpers
def make_df(n=420, seed=7, volume=True):
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0, 0.25, n))
    r2 = np.random.default_rng(seed + 1)
    o = base + r2.normal(0, 0.05, n)
    c = base + r2.normal(0, 0.05, n)
    h = np.maximum(o, c) + np.abs(r2.normal(0, 0.18, n))
    l = np.minimum(o, c) - np.abs(r2.normal(0, 0.18, n))
    v = np.abs(r2.normal(1_000, 200, n)) if volume else np.zeros(n)
    idx = pd.date_range("2026-09-01", periods=n, freq="15min")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": v}, index=idx)


def naive_profile(df, i, start, bins):
    """Literal transcription of the Pine loops - the reference oracle."""
    w = df.iloc[i - start + 1: i + 1]
    H = w["high"].max(); L = w["low"].min()
    bs = (H - L) / bins
    Bins = [0.0] * bins
    for b in range(bins):
        blo = L + bs * b
        bhi = blo + bs
        for j in range(len(w)):
            if blo - bs <= w["close"].iloc[j] < bhi + bs:
                Bins[b] += w["volume"].iloc[j]
    return Bins


S = get_strategy("strategy_1_vp_pivots")
P = dict(S.base_params)


def node_frame(n=105, node=None, pivot_bar=None, signal=None):
    """105 bars: 100 tight node bars (closes clustered at one price), then an
    optional engineered pivot bar at 100 and a final signal bar at 104."""
    node = node or dict(o=49.999, h=50.01, l=49.99, c=49.99, v=1000.0)
    rows = [dict(o=node["o"], h=node["h"], l=node["l"], c=node["c"], v=node["v"])
            for _ in range(100)]
    rows.append(pivot_bar or dict(o=node["o"], h=node["h"], l=node["l"],
                                  c=node["c"], v=node["v"]))
    rows += [dict(o=node["o"], h=node["h"], l=node["l"], c=node["c"], v=node["v"])
             for _ in range(3)]
    rows.append(signal or dict(o=node["o"], h=node["h"], l=node["l"],
                               c=node["c"], v=node["v"]))
    df = pd.DataFrame([{ "open": r["o"], "high": r["h"], "low": r["l"],
                         "close": r["c"], "volume": r["v"]} for r in rows],
                      index=pd.date_range("2026-01-01", periods=105, freq="15min"))
    return df


CTRL = dict(P)
CTRL.update({"profile_period": 100, "profile_bins": 5, "pivot_length": 2,
             "pivot_filter": 20.0})


@pytest.fixture()
def store(fresh_store):
    from app.learning.versions import ensure_strategy_docs
    ensure_strategy_docs()
    return fresh_store


# ------------------------------------------------------------- metadata
def test_metadata_and_defaults():
    assert S.id == "strategy_1_vp_pivots"
    assert S.base_params["entry_mode"] == "hvn_rejection"   # user's pick
    assert S.base_params["profile_period"] == 200
    assert S.base_params["profile_bins"] == 50
    assert S.base_params["pivot_length"] == 10
    assert S.base_params["pivot_filter"] == 20.0
    # SL/TP inherited from the EMA strategy
    e = get_strategy("strategy_2_ema_atr")
    for k in ("atr_len", "sl_mult", "tp1_rr", "tp2_rr", "tp3_rr"):
        assert S.base_params[k] == e.base_params[k]
    assert "hvn_rejection" in S.experiment_variables["entry_mode"]["options"]


def test_registry_new_strategy_registered_and_zero_lag_resolvable():
    ss = all_strategies()
    assert "strategy_1_vp_pivots" in ss
    # retired but resolvable: old signals / replay / executor need it
    assert "strategy_1_zero_lag" in ss


# ------------------------------------------------------- profile exactness
def test_profile_matches_source_loops_exactly():
    for seed in (3, 7, 11):
        df = make_df(seed=seed)
        p = dict(P); p["profile_period"] = 60; p["profile_bins"] = 12
        for i in (200, 260, len(df) - 1):
            prof = S._profile_at(df, i, p)
            ref = naive_profile(df, i, 60, 12)
            assert np.allclose(prof["acc"], ref, atol=1e-9)
            assert prof["max"] == pytest.approx(max(ref), rel=1e-12)


def test_poc_is_last_max_bin_and_mid_price():
    df = make_df(n=80, seed=5)
    p = dict(P); p["profile_period"] = 60; p["profile_bins"] = 10
    prof = S._profile_at(df, len(df) - 1, p)
    acc = prof["acc"]
    assert prof["poc_bin"] == int(np.where(acc == acc.max())[0][-1])
    bs = prof["bs"]
    assert prof["poc"] == pytest.approx(prof["L"] + bs * prof["poc_bin"] + bs / 2)


def test_profile_honest_on_zero_volume():
    df = make_df(volume=False)
    assert S._profile_at(df, len(df) - 1, P) is None
    # and the scan path refuses to invent a signal
    assert S.detect_signal(df, "EURUSD", "15M", params=dict(P)) is None


# ---------------------------------------------------------------- pivots
def test_pivot_detection_and_tie_policy():
    L = 2
    lows = [0.5, 1, 2, 1, 1, 1, 1]
    closes = [1, 2, 3, 4, 3, 2, 1]
    # bar 3 is the pivot high (10.0): left bars lower, right bars strictly lower
    piv = S._pivots(_tiny_df([1, 2, 3, 10, 4, 3, 2], lows, closes), L)
    assert (5, 10.0, 3) in [(c, v, p) for c, v, p, is_h in piv if is_h]
    # right-side TIE: the later equal high becomes the pivot (p=4), p=3 dies
    piv2 = S._pivots(_tiny_df([1, 2, 3, 10, 10, 3, 2], lows, closes), L)
    ph2 = [(c, v, p) for c, v, p, is_h in piv2 if is_h]
    assert (6, 10.0, 4) in ph2 and not any(p == 3 for c, v, p in ph2)
    # left-side TIE allowed: equal high BEFORE the candidate
    piv3 = S._pivots(_tiny_df([1, 10, 3, 10, 4, 3, 2], lows, closes), L)
    assert any(v == 10.0 and p == 3 for c, v, p, is_h in piv3 if is_h)


def _tiny_df(highs, lows, closes, volumes=None):
    n = len(closes)
    v = volumes or [1.0] * n
    return pd.DataFrame({"open": list(closes), "high": highs, "low": lows,
                         "close": list(closes), "volume": v},
                        index=pd.date_range("2026-01-01", periods=n, freq="15min"))


# ---------------------------------------------------------------- levels
def test_age_rule_and_volume_filter_on_random_data():
    df = make_df(n=120, seed=9)
    p = dict(P); p["profile_period"] = 90; p["pivot_length"] = 3
    i = 119
    prof = S._profile_at(df, i, p)
    state = S.compute(df, p)
    levels = S._active_levels(df, state, i, prof, p)
    for x in levels:
        assert i - x["pidx"] <= 90 - 54        # source label-offset rule
        assert x["strength"] >= p["pivot_filter"]
    p0 = dict(p); p0["pivot_filter"] = 0.0     # lower filter admits a superset
    assert len(S._active_levels(df, state, i, prof, p0)) >= len(levels)


def test_engulfment_removes_level_but_span_free_bar_does_not():
    """Built on the known rejection frame (support level 49.9855 at pidx=100,
    right-side validity bars 101/102 untouched). Bar 103 sits inside the
    (pidx, i) engulf window but outside pivot validity - isolating the
    span rule."""
    base = node_frame(
        node=dict(o=49.99, h=50.01, l=49.99, c=49.99, v=1000.0),
        pivot_bar=dict(o=49.99, h=50.01, l=49.9855, c=49.99, v=1000.0),
        signal=dict(o=49.99, h=50.005, l=49.98, c=50.00, v=1000.0))
    i = 104
    lvl = 49.9855

    def has_level(df):
        prof = S._profile_at(df, i, CTRL)
        st = S.compute(df, CTRL)
        return any(x["level"] == pytest.approx(lvl)
                   for x in S._active_levels(df, st, i, prof, CTRL))

    assert has_level(base)
    # a bar that dips toward the level but stays fully ABOVE it: not engulfed
    df2 = base.copy()
    df2.iloc[103, df2.columns.get_loc("low")] = lvl + 0.001
    df2.iloc[103, df2.columns.get_loc("high")] = lvl + 0.004
    assert has_level(df2)
    # a bar that SPANS the level (high > level AND low < level) kills it
    df3 = base.copy()
    df3.iloc[103, df3.columns.get_loc("low")] = lvl - 0.004
    df3.iloc[103, df3.columns.get_loc("high")] = lvl + 0.004
    assert not has_level(df3)


# ------------------------------------------------------------ entry modes
def test_hvn_rejection_buy():
    """Hand-solved frame: node closes at 49.99 (bin 1 of 5), pivot low at
    49.9855 sits on the node (bin 0 mid 49.983, |delta| = 0.0025 <= bs 0.006)
    -> volume-confirmed support; signal bar wicks to 49.98 and closes 50.00."""
    df = node_frame(
        node=dict(o=49.99, h=50.01, l=49.99, c=49.99, v=1000.0),
        pivot_bar=dict(o=49.99, h=50.01, l=49.9855, c=49.99, v=1000.0),
        signal=dict(o=49.99, h=50.005, l=49.98, c=50.00, v=1000.0))
    ev = S.detect_on_bar(S.compute(df, CTRL), 104)
    assert ev and ev["direction"] == "BUY"
    assert ev["trigger"]["kind"] == "hvn_rejection"
    assert ev["trigger"]["level"] == pytest.approx(49.9855)
    assert ev["trigger"]["strength"] > 20.0


def test_hvn_rejection_sell():
    df = node_frame(
        node=dict(o=49.989, h=49.99, l=49.985, c=49.99, v=1000.0),
        pivot_bar=dict(o=49.99, h=49.995, l=49.985, c=49.99, v=1000.0),
        signal=dict(o=49.99, h=49.999, l=49.98, c=49.985, v=1000.0))
    ev = S.detect_on_bar(S.compute(df, CTRL), 104)
    assert ev and ev["direction"] == "SELL"
    assert ev["trigger"]["kind"] == "hvn_rejection"
    assert ev["trigger"]["level"] == pytest.approx(49.995)


def test_rejection_close_through_is_not_a_rejection():
    df = node_frame(
        node=dict(o=49.99, h=50.01, l=49.99, c=49.99, v=1000.0),
        pivot_bar=dict(o=49.99, h=50.01, l=49.9855, c=49.99, v=1000.0),
        signal=dict(o=49.99, h=50.005, l=49.98, c=49.982, v=1000.0))  # CLOSES below
    ev = S.detect_on_bar(S.compute(df, CTRL), 104)
    assert ev is None or ev["direction"] != "BUY"


def test_breakout_mode_requires_cross_of_prev_close():
    df = node_frame(
        node=dict(o=49.992, h=49.994, l=49.99, c=49.993, v=1000.0),
        pivot_bar=dict(o=49.993, h=49.995, l=49.99, c=49.993, v=1000.0),
        signal=dict(o=49.993, h=49.999, l=49.99, c=49.998, v=1000.0))
    p = dict(CTRL); p["entry_mode"] = "breakout"
    ev = S.detect_on_bar(S.compute(df, p), 104)
    assert ev and ev["direction"] == "BUY"
    assert ev["trigger"]["kind"] == "breakout"
    assert ev["trigger"]["level"] == pytest.approx(49.995)
    # no cross when the PREVIOUS close was already above the level
    df2 = df.copy()
    df2.iloc[103, df2.columns.get_loc("close")] = 49.997   # already above 49.995
    ev2 = S.detect_on_bar(S.compute(df2, p), 104)
    assert ev2 is None or ev2["direction"] != "BUY"


def test_poc_bounce_mode():
    base = node_frame(node=dict(o=49.999, h=50.005, l=49.995, c=50.0, v=1000.0))
    buy = base.copy()
    buy.iloc[104] = [49.99, 50.02, 49.98, 50.01, 500.0]    # o h l c v
    ev = S.detect_on_bar(S.compute(buy, CTRL | {"entry_mode": "poc_bounce"}), 104)
    assert ev and ev["direction"] == "BUY"
    assert ev["trigger"]["kind"] == "poc_bounce"

    sell = base.copy()
    sell.iloc[104] = [50.01, 50.02, 49.98, 49.99, 500.0]
    ev2 = S.detect_on_bar(S.compute(sell, CTRL | {"entry_mode": "poc_bounce"}), 104)
    assert ev2 and ev2["direction"] == "SELL"

    # a bar fully ABOVE the PoC never "bounces" off it
    above = base.copy()
    above.iloc[104] = [50.012, 50.02, 50.01, 50.015, 500.0]
    assert S.detect_on_bar(S.compute(above, CTRL | {"entry_mode": "poc_bounce"}), 104) is None


def test_default_mode_is_rejection_when_param_missing():
    df = node_frame(
        node=dict(o=49.99, h=50.01, l=49.99, c=49.99, v=1000.0),
        pivot_bar=dict(o=49.99, h=50.01, l=49.9855, c=49.99, v=1000.0),
        signal=dict(o=49.99, h=50.005, l=49.98, c=50.00, v=1000.0))
    p = dict(CTRL); p.pop("entry_mode")
    ev = S.detect_on_bar(S.compute(df, p), 104)
    assert ev and ev["direction"] == "BUY"      # rejection default produced it


# --------------------------------------------------------- SL/TP parity
def test_sl_tp_equals_ema_strategy_math():
    e = get_strategy("strategy_2_ema_atr")
    df = make_df(n=300, seed=51)
    i = len(df) - 1
    for direction in ("BUY", "SELL"):
        rk_v = S.calculate_risk(df, S.compute(df, P), i, direction, P)
        rk_e = e.calculate_risk(df, e.compute(df, e.base_params), i, direction,
                                e.base_params)
        assert rk_v["risk"] == pytest.approx(rk_e["risk"])
        assert rk_v["sl"] == pytest.approx(rk_e["sl"])
        assert rk_v["entry"] == pytest.approx(rk_e["entry"])
        tv = S.calculate_targets(rk_v["entry"], rk_v["risk"], direction, P)
        te = e.calculate_targets(rk_e["entry"], rk_e["risk"], direction, e.base_params)
        assert tv == pytest.approx(te)
        sign = 1 if direction == "BUY" else -1
        assert sign * (tv[0] - rk_v["entry"]) < sign * (tv[1] - rk_v["entry"]) \
               < sign * (tv[2] - rk_v["entry"])


def test_full_candidate_path_matches_ema_risk():
    node = dict(o=49.99, h=50.01, l=49.99, c=49.99, v=1000.0)
    rows = [node] * 255
    rows += [dict(o=49.99, h=50.01, l=49.9855, c=49.99, v=1000.0),   # pivot bar
             node, node, node,                                       # confirmers
             dict(o=49.99, h=50.005, l=49.98, c=50.00, v=1000.0)]    # signal bar
    big = pd.DataFrame([{ "open": r["o"], "high": r["h"], "low": r["l"],
                          "close": r["c"], "volume": r["v"]} for r in rows],
                       index=pd.date_range("2026-01-01", periods=260, freq="15min"))
    cand = S.detect_signal(big, "EURUSD", "15M", params=dict(CTRL))
    assert cand is not None and cand.direction == "BUY"
    assert cand.risk > 0 and len(cand.tps) == 3
    e = get_strategy("strategy_2_ema_atr")
    i = len(big) - 1
    rk_e = e.calculate_risk(big, e.compute(big, e.base_params), i, "BUY",
                            e.base_params)
    assert cand.risk == pytest.approx(rk_e["risk"])          # inherited SL model
    assert cand.sl == pytest.approx(rk_e["sl"])
    assert cand.rr_primary == 3.0
    assert cand.score >= 0 and "Node strength" in cand.score_components
    assert any(c["label"] == "Risk/reward acceptable" and c["ok"] for c in cand.checks)


# ------------------------------------------------------------- causality
def test_detect_is_causal_future_bars_cannot_create_past_events():
    df = make_df(n=400, seed=61)
    p = dict(P); p["profile_period"] = 90; p["profile_bins"] = 20
    p["pivot_length"] = 3; p["entry_mode"] = "poc_bounce"
    st = S.compute(df, p)
    found = None
    for i in range(300, len(df)):
        ev = S.detect_on_bar(st, i)
        if ev:
            found = i
            break
    assert found is not None
    st2 = S.compute(df.iloc[: found + 1], p)
    ev1 = S.detect_on_bar(st, found)
    ev2 = S.detect_on_bar(st2, found)
    assert (ev2 is None) == (ev1 is None)
    if ev1:
        assert ev1["direction"] == ev2["direction"]


# ------------------------------------------------- version control / API
def test_retired_zero_lag_docs_are_created_disabled(store):
    doc = store.get("strategies", "strategy_1_zero_lag")
    assert doc["status"] == "DISABLED"
    newdoc = store.get("strategies", "strategy_1_vp_pivots")
    assert newdoc["status"] == "ACTIVE"


def test_set_param_direct_versions_and_audits(store):
    from app.learning.versions import (active_params, active_version,
                                       set_param_direct)
    v0 = active_version("strategy_1_vp_pivots")
    doc = set_param_direct("boss", "strategy_1_vp_pivots", "entry_mode", "breakout")
    assert doc["version"] != v0
    assert active_params("strategy_1_vp_pivots")["entry_mode"] == "breakout"
    changes = doc["changes"][0]
    assert changes == {"variable": "entry_mode",
                       "old_value": "hvn_rejection", "new_value": "breakout"}
    audits = store.list("audit_log", filters={"action": "strategy.param.set"}, limit=5)
    assert audits and audits[0]["actor"] == "boss"
    vs = store.list("strategy_versions", filters={"strategy_id": "strategy_1_vp_pivots"})
    assert len(vs) == 2 and sum(1 for v in vs if v["active"]) == 1


def test_set_param_direct_validates(store):
    from app.learning.versions import set_param_direct
    with pytest.raises(ValueError):     # not one of the select options
        set_param_direct("boss", "strategy_1_vp_pivots", "entry_mode", "moon")
    with pytest.raises(ValueError):     # out of range
        set_param_direct("boss", "strategy_1_vp_pivots", "sl_mult", 9.9)
    with pytest.raises(KeyError):       # unknown variable
        set_param_direct("boss", "strategy_1_vp_pivots", "nope", 1)
    with pytest.raises(ValueError):     # no-op change
        set_param_direct("boss", "strategy_1_vp_pivots", "entry_mode",
                         "hvn_rejection")


def test_params_route_patch():
    from fastapi.testclient import TestClient
    from app.db.store import LocalStore
    from app.db import store as store_mod
    from app.state import State
    from app.config import settings
    from app.main import app
    from app.api.deps import get_user_id
    import tempfile

    tmp = tempfile.mkdtemp()
    st = LocalStore(path=f"{tmp}/db.json")
    store_mod._store = st
    prev = State.store
    State.store = st
    settings.owner_user_id = "boss"
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        from app.learning.versions import ensure_strategy_docs
        ensure_strategy_docs()
        with TestClient(app) as c:
            r = c.patch("/api/strategies/strategy_1_vp_pivots/params",
                        json={"variable": "entry_mode", "value": "poc_bounce"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["params"]["entry_mode"] == "poc_bounce"
            bad = c.patch("/api/strategies/strategy_1_vp_pivots/params",
                          json={"variable": "entry_mode", "value": "moon"})
            assert bad.status_code == 422
    finally:
        State.store = prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)
