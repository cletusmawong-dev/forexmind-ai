"""CONSEC_LOSSES wall (2026-10-02 incident): the Settings value
'max_consecutive_losses' was stored and displayed but never consulted -
the owner ate 4 straight SLs overnight (Oct 1 22:xx - Oct 2 03:05 Accra)
and the engine still placed trade #6 at 03:15. The wall now gates
automatic EXECUTION; advisory signals keep flowing (SS22)."""
from __future__ import annotations

import pytest

from app.risk_checks import _consecutive_losses_ok, check_entry


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Real LocalStore on disk, clean globals - mirrors pipeline tests."""
    from app.db.store import LocalStore
    from app.db import store as store_mod
    st = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", st)
    yield st


from datetime import datetime, timezone as _tz, timedelta as _td
_TODAY = datetime.now(_tz.utc).strftime("%Y-%m-%d")
_YESTERDAY = (datetime.now(_tz.utc) - _td(days=1)).strftime("%Y-%m-%d")


def _sig(store, i, pl, day=None, created=None, status="CLOSED_MT5",
         completed=True):
    store.create("signals", {
        "userId": "boss", "market": "EURUSD", "direction": "BUY",
        "status": status, "completed": completed, "mt5_pl": pl,
        "day": day or _TODAY,
        "createdAt": created or f"{day or _TODAY}T0{i % 10}:00:00Z",
    }, doc_id=f"sig{i}")


def test_blocks_at_limit(env):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    for i, pl in enumerate([-50.65, -80.52, -42.10, -54.81]):
        _sig(env, i, pl)
    ok, why = _consecutive_losses_ok("boss")
    assert not ok and "4 consecutive losing" in why


def test_allows_below_limit(env):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    for i, pl in enumerate([-50.65, -80.52, -42.10]):
        _sig(env, i, pl)
    ok, why = _consecutive_losses_ok("boss")
    assert ok, why


def test_win_resets_streak(env):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    # oldest -> newest: 3 losses then a WIN -> trailing streak is 0
    for i, pl in enumerate([-50.0, -60.0, -70.0, 40.0]):
        _sig(env, i, pl, created=f"{_TODAY}T0{i+1}:00:00Z")
    ok, why = _consecutive_losses_ok("boss")
    assert ok, why


def test_yesterday_losses_do_not_count(env):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    for i in range(4):
        _sig(env, i, -50.0, day=_YESTERDAY,
             created=f"{_YESTERDAY}T0{i}:00:00Z")
    ok, why = _consecutive_losses_ok("boss")
    assert ok, why


def test_disabled_when_zero(env):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 0},
               doc_id="s1")
    for i in range(5):
        _sig(env, i, -50.0)
    ok, why = _consecutive_losses_ok("boss")
    assert ok, why


def test_unconfirmed_loss_stops_counting_conservatively(env):
    """A closed-but-unconfirmed deal must NOT be counted as a loss
    (broker-truth rule) - streak stops there (conservative in the user's
    favor for the wall, never fabricates)."""
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 2},
               doc_id="s1")
    _sig(env, 1, -50.0, created=f"{_TODAY}T02:00:00Z")
    _sig(env, 2, None, created=f"{_TODAY}T01:00:00Z")   # unconfirmed
    ok, why = _consecutive_losses_ok("boss")
    assert ok, why


def test_fail_closed_when_store_broken(env, monkeypatch):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    from app.db import store as store_mod
    def boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(store_mod, "get_store", boom)
    ok, why = _consecutive_losses_ok("boss")
    assert not ok and "fail-closed" in why


def test_check_entry_runs_consec_guard_first(env, monkeypatch):
    env.create("settings", {"userId": "boss", "max_consecutive_losses": 4},
               doc_id="s1")
    for i in range(4):
        _sig(env, i, -50.0)
    import app.risk_checks as rc
    monkeypatch.setattr(rc, "_news_ok", lambda m: (True, ""))
    monkeypatch.setattr(rc, "_spread_ok", lambda m: (True, ""))
    monkeypatch.setattr(rc, "_vol_ok", lambda m: (True, ""))
    monkeypatch.setattr(rc, "_correlation_ok", lambda uid, m: (True, ""))
    monkeypatch.setattr(rc, "_stacking_ok", lambda uid, m, d: (True, ""))
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "risk_guards_enabled", True)
    ok, guard, why = check_entry("boss", "EURUSD", direction="BUY")
    assert not ok and guard == "CONSEC_LOSSES"
