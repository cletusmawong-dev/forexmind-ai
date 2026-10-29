"""Owner messaging directive (2026-10-25): Telegram carries EXACTLY four
messages - (1) the morning brief, (2) high-impact news, (3) trade ENTERED
(real broker fill), (4) trade hit TP or SL (+ live TP hits). Everything
else stays in-app only. No force flag, no env extension, no caller can
push anything else through. The morning brief goes out EVERY day (the old
s.8B actionable-only gate is gone); dedup (once per day) is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ===========================================================================
# the whitelist predicate
# ===========================================================================
def test_the_four_categories_are_telegram_worthy():
    from app.notifications.service import _telegram_worthy
    for t in ("MORNING_BRIEF", "NEWS_ALERT", "TRADE_ENTERED", "TRADE_COMPLETED"):
        assert _telegram_worthy(t, None) is True, t


def test_execution_failed_is_telegram_worthy_again():
    """Owner approval 2026-10-25: entry failures are loud on Telegram."""
    from app.notifications.service import _telegram_worthy
    assert _telegram_worthy("EXECUTION_FAILED", None) is True


def test_live_tp_hits_are_telegram_worthy():
    from app.notifications.service import _telegram_worthy
    assert _telegram_worthy("TP1_HIT", None)
    assert _telegram_worthy("TP3_HIT_LIVE", None)


def test_everything_else_is_silenced():
    """EXECUTION_SKIPPED left this list on 2026-10-29: a refused order is a
    MISSED trade (owner: "if we miss a trade I will deal with u") and the
    owner hears about it the moment it happens."""
    from app.notifications.service import _telegram_worthy
    for t in ("NEW_SIGNAL", "EXTRA_SIGNAL", "EXTRA_SIGNAL_TAKEN",
              "SIGNAL_SKIPPED",
              "MANAGEMENT_EVENT", "REGIME_CHANGE", "APPROVAL_REQUIRED",
              "RESEARCH_REVIEW", "STRATEGY_VERSION_UPDATED",
              "AI_ESCALATION_DOWN", "TELEGRAM_TEST", "MY_CUSTOM_TYPE"):
        assert _telegram_worthy(t, None) is False, t
    assert _telegram_worthy("EXECUTION_SKIPPED", None) is True


def test_tp_audit_leak_is_not_a_tp_hit():
    """Starts with TP but is an audit record, not a live TP event."""
    from app.notifications.service import _telegram_worthy
    assert _telegram_worthy("TP_AUDIT_LEAK", None) is False


def test_force_flag_cannot_bypass():
    from app.notifications.service import _telegram_worthy
    meta = {"telegram_important": True}
    assert _telegram_worthy("NEW_SIGNAL", meta) is False
    assert _telegram_worthy("EXECUTION_SKIPPED", meta) is True   # exact allowlist
    assert _telegram_worthy("MANAGEMENT_EVENT", meta) is False


def test_env_extension_cannot_bypass(monkeypatch):
    from app.notifications.service import _telegram_worthy
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_important_types", "MY_CUSTOM_TYPE")
    assert _telegram_worthy("MY_CUSTOM_TYPE", None) is False


def test_s8_denylist_still_applies_on_top():
    from app.notifications.service import _telegram_worthy
    assert _telegram_worthy("MARKET_SUMMARY", None) is False
    assert _telegram_worthy("AI_COMMENTARY", None) is False


# ===========================================================================
# the funnel: persist everything, telegram only the four
# ===========================================================================
@pytest.fixture()
def _tg(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr("app.notifications.telegram.send_telegram",
                        lambda uid, text: sent.append((uid, text)) or True)
    from app.db.store import LocalStore
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", LocalStore(path=str(tmp_path / "db.json")))
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_bot_token", "fake-token")
    return sent


def test_funnel_sends_the_approved_set(_tg):
    """The owner's four categories + EXECUTION_FAILED (owner approval
    2026-10-25: entry failures are loud again)."""
    from app.notifications import service as svc
    for t in ("MORNING_BRIEF", "NEWS_ALERT", "TRADE_ENTERED", "TRADE_COMPLETED",
              "EXECUTION_FAILED"):
        svc.notify("boss", t, f"go {t}", "body")
    for t in ("NEW_SIGNAL", "MANAGEMENT_EVENT",
              "REGIME_CHANGE", "RESEARCH_REVIEW", "APPROVAL_REQUIRED"):
        svc.notify("boss", t, f"stay {t}", "body")
    svc.notify("boss", "TP2_HIT_LIVE", "TP2 reached", "body")
    types = [x[1] for x in _tg]
    assert len(_tg) == 6, types
    for t in ("go MORNING_BRIEF", "go NEWS_ALERT", "go TRADE_ENTERED",
              "go TRADE_COMPLETED", "go EXECUTION_FAILED", "TP2 reached"):
        assert any(t in x for x in types), (t, types)
    assert not any("stay" in x for x in types)


def test_funnel_persists_everything_in_app(_tg):
    """Silenced types are NOT deleted - the Notifications screen keeps all."""
    from app.notifications import service as svc
    from app.db.store import get_store
    svc.notify("boss", "NEW_SIGNAL", "s", "b")
    svc.notify("boss", "MORNING_BRIEF", "m", "b")
    notes = get_store().list("notifications", limit=50)
    assert {n["type"] for n in notes} == {"NEW_SIGNAL", "MORNING_BRIEF"}


# ===========================================================================
# TRADE_ENTERED fires on the real broker fill only
# ===========================================================================
@pytest.fixture()
def _cap_tg(monkeypatch):
    sent = []
    monkeypatch.setattr("app.notifications.telegram.send_telegram",
                        lambda uid, text: sent.append((uid, text)) or True)
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_bot_token", "fake-token")
    return sent


def test_apply_fill_emits_trade_entered_with_tp(fresh_store, _cap_tg):
    from app.execution.mt5 import _apply_fill
    doc = fresh_store.create("signals", {
        "userId": "u1", "strategy_id": "strategy_trendbreak_h1",
        "strategy_name": "TrendBreak H1", "market": "EURUSD",
        "direction": "BUY", "entry": 100.0, "sl": 99.0, "tp1": 103.0})
    _apply_fill(fresh_store, {**doc}, {"ticket": 9, "price": 100.02,
                                       "volume": 0.12}, 0.12)
    assert len(_cap_tg) == 1, _cap_tg
    uid, text = _cap_tg[0]
    assert uid == "u1"
    assert "ENTERED" in text and "EURUSD" in text and "BUY" in text
    assert "100.02" in text                      # the FILL price, not ref entry
    assert "SL 99" in text
    assert "TP 103" in text and "RR 1:3" in text
    assert "0.12 lots" in text


def test_apply_fill_trail_variant_no_tp_line(fresh_store, _cap_tg):
    """The v2 trail strategy: the entered message carries the exit plan."""
    from app.execution.mt5 import _apply_fill
    doc = fresh_store.create("signals", {
        "userId": "u1", "strategy_id": "strategy_trendbreak_v2",
        "strategy_name": "H1 v2.0", "market": "XAUUSD",
        "direction": "SELL", "entry": 2400.0, "sl": 2412.0, "tp1": None})
    _apply_fill(fresh_store, {**doc}, {"ticket": 10, "price": 2399.4,
                                       "volume": 0.08}, 0.08)
    assert len(_cap_tg) == 1
    uid, text = _cap_tg[0]
    assert "2,399.4" in text and "SL 2,412" in text
    assert "No fixed TP by rule" in text and "trail" in text
    assert " - TP " not in text   # no target leg


def test_apply_fill_without_token_still_records(fresh_store):
    """No Telegram token (or send failure) never breaks the fill recorder."""
    from app.execution.mt5 import _apply_fill
    doc = fresh_store.create("signals", {
        "userId": "u1", "strategy_id": "strategy_trendbreak_h1",
        "market": "EURUSD", "direction": "BUY", "entry": 100.0,
        "sl": 99.0, "tp1": 103.0})
    _apply_fill(fresh_store, {**doc}, {"ticket": 11, "price": 100.0,
                                       "volume": 0.1}, 0.1)
    out = fresh_store.get("signals", doc["id"])
    assert out["mt5_ticket"] == 11
    assert out["intended_rr"] == pytest.approx(3.0)


# ===========================================================================
# the morning brief goes out EVERY day
# ===========================================================================
def test_morning_brief_pushes_on_a_quiet_day(fresh_store, monkeypatch):
    """No open positions, no signals, nothing actionable -> the brief STILL
    goes out (owner directive 2026-10-25). Once per day, unchanged."""
    from app.agent import brief as brief_mod
    monkeypatch.setattr(brief_mod, "build", lambda uid: "BRIEF TEXT")
    sent = []
    monkeypatch.setattr(brief_mod, "notify",
                        lambda uid, t, title, body: sent.append((t, title)))
    brief_mod._pushed_today.clear()
    now = datetime(2026, 10, 25, 7, 5, tzinfo=timezone.utc)
    assert brief_mod.maybe_push_daily("u1", now=now) is True
    assert sent == [("MORNING_BRIEF", "📰 Morning Brief")]
    # once per day
    assert brief_mod.maybe_push_daily("u1", now=now) is False
    assert len(sent) == 1


def test_morning_brief_outside_push_window_stays_silent(fresh_store, monkeypatch):
    from app.agent import brief as brief_mod
    monkeypatch.setattr(brief_mod, "build", lambda uid: "BRIEF TEXT")
    sent = []
    monkeypatch.setattr(brief_mod, "notify",
                        lambda uid, t, title, body: sent.append((t, title)))
    brief_mod._pushed_today.clear()
    now = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
    assert brief_mod.maybe_push_daily("u1", now=now) is False
    assert sent == []
