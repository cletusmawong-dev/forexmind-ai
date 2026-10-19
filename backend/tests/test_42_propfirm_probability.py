"""PROP-FIRM engine + PROBABILITY calibration tests (sections 5 & 6).

Drawdown limits (static & trailing), safe sizing, revenge-risk rejection,
multi-account isolation, and probability honesty (insufficient evidence
never becomes a number).
"""
from app.risk import propfirm
from app.learning import probability as prob


def acct(balance=100000.0, equity=None, **rules):
    a = {"balance": balance, "equity": equity if equity is not None else balance,
         "prop_rules": {"governed": True, **rules}}
    return a


# ------------------------------------------------------------------ rules
def test_ungoverned_account_passes_through():
    r = propfirm.rules_for({"balance": 1, "prop_rules": {"governed": False}})
    assert not r["governed"]
    chk = propfirm.pre_trade_check({"balance": 1, "equity": 1}, {}, 100.0)
    assert chk["governed"] is False and chk["allowed"] is True


def test_rules_without_limits_are_not_governed():
    r = propfirm.rules_for(acct(profit_target_pct=8))
    assert not r["governed"]


# ------------------------------------------------------------ drawdowns
def test_static_daily_dd_budget():
    a = acct(daily_dd_pct=5.0, max_dd_pct=10.0)          # $5k daily line
    b = propfirm.budgets(a, {"daily_pl": -2000.0})
    assert b["governed"] and b["daily_dd_line_usd"] == -5000.0
    assert b["daily_left_usd"] == 3000.0                 # headroom left today
    assert not b["breach_now"]


def test_daily_breach_blocks_new_risk():
    a = acct(daily_dd_pct=5.0)
    b = propfirm.budgets(a, {"daily_pl": -5100.0})
    assert b["breach_now"]
    chk = propfirm.pre_trade_check(a, {"daily_pl": -5100.0}, 100.0)
    assert chk["governed"] and not chk["allowed"]
    assert "exhausted" in chk["reason"]


def test_trailing_max_dd_tracks_peak():
    a = acct(balance=100000.0, max_dd_pct=10.0, dd_type="trailing")
    b = propfirm.budgets(a, {"daily_pl": 0.0, "peak_equity": 110000.0})
    assert b["max_dd_line_usd"] == -11000.0              # 10% of the PEAK
    s = propfirm.rules_for(a)
    assert s["dd_type"] == "trailing"


def test_static_max_dd_uses_balance():
    a = acct(balance=100000.0, max_dd_pct=10.0, dd_type="static")
    b = propfirm.budgets(a, {"daily_pl": 0.0, "peak_equity": 120000.0})
    assert b["max_dd_line_usd"] == -10000.0              # static: of balance


# --------------------------------------------------------------- sizing
def test_lot_cap_shrinks_never_enlarges():
    a = acct(daily_dd_pct=5.0)
    # $2,500 daily loss so far -> $2,500 left; usable = 2500*0.5*0.85 ≈ 1062
    chk = propfirm.cap_lots(a, {"daily_pl": -2500.0}, lots=5.0,
                            stop_usd_per_lot=1000.0)
    assert chk["allowed"] and chk["lots"] <= 1.06
    chk2 = propfirm.cap_lots(a, {"daily_pl": -2500.0}, lots=0.5,
                             stop_usd_per_lot=1000.0)
    assert chk2["lots"] == 0.5 and not chk2["shrunk"]    # never enlarges


def test_consecutive_losses_tighten_the_cap():
    a = acct(daily_dd_pct=10.0)
    loose = propfirm.cap_lots(a, {"daily_pl": 0.0, "consecutive_losses": 0},
                              lots=10.0, stop_usd_per_lot=1000.0)
    tight = propfirm.cap_lots(a, {"daily_pl": 0.0, "consecutive_losses": 4},
                              lots=10.0, stop_usd_per_lot=1000.0)
    assert tight["lots"] < loose["lots"]                 # anti-recovery


def test_open_positions_reduce_the_budget():
    a = acct(daily_dd_pct=5.0)
    alone = propfirm.pre_trade_check(a, {"daily_pl": 0.0}, 1000.0)
    with_open = propfirm.pre_trade_check(a, {"daily_pl": 0.0}, 1000.0,
                                         existing_open_risk_usd=1500.0)
    assert with_open["cap_usd"] < alone["cap_usd"]


def test_target_progress():
    a = acct(balance=100000.0, equity=104000.0, profit_target_pct=8.0,
             daily_dd_pct=5.0)
    b = propfirm.budgets(a, {"daily_pl": 4000.0, "peak_equity": 104000.0})
    assert b["profit_target_usd"] == 8000.0
    assert b["target_progress_pct"] == 50.0


# ----------------------------------------------------------- probability
def _closed(n_win, n_loss, strat="s", mkt="XAUUSD", direction="BUY"):
    rows = []
    for _ in range(n_win):
        rows.append({"strategy_id": strat, "market": mkt,
                     "direction": direction, "outcome": "WIN",
                     "atr_pct": 0.6})
    for _ in range(n_loss):
        rows.append({"strategy_id": strat, "market": mkt,
                     "direction": direction, "outcome": "LOSS",
                     "atr_pct": 0.6})
    return rows


def test_insufficient_evidence_publishes_no_number():
    est = prob.estimate("s", "XAUUSD", "BUY", 0.6, _closed(3, 2))
    assert est["status"] == "INSUFFICIENT_EVIDENCE" and "p_tp" not in est


def test_calibrated_estimate_with_sample_and_interval():
    est = prob.estimate("s", "XAUUSD", "BUY", 0.6, _closed(45, 15))
    assert est["status"] == "OK" and est["n"] == 60
    assert 0.6 < est["p_tp"] < 0.85                      # shrunk toward 0.5
    assert est["wilson90"]["low"] < est["p_tp"] < est["wilson90"]["high"]


def test_strata_do_not_leak():
    """A SELL estimate must not count BUY history (direction stratum)."""
    est = prob.estimate("s", "XAUUSD", "SELL", 0.6, _closed(50, 10))
    assert est["status"] == "INSUFFICIENT_EVIDENCE"
