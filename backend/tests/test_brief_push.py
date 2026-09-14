"""Morning brief must push at most ONCE per user per day (regression: 8 dupes)."""
from datetime import datetime, timezone

from app.agent import brief as B
from app.db.store import LocalStore


def _setup(monkeypatch, tmp_path):
    st = LocalStore(path=str(tmp_path / "db.json"))
    st.create("agent_goals", {"userId": "u1", "account_balance": 100})
    monkeypatch.setattr(B, "get_store", lambda: st)
    sent = []
    monkeypatch.setattr(B, "notify", lambda uid, t, title, body: sent.append(uid))
    B._pushed_today.clear()
    return sent


def test_pushes_once_per_day(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path)
    now = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
    assert B.maybe_push_daily("u1", now=now) is True
    assert B.maybe_push_daily("u1", now=now) is False      # in-process guard
    assert B.maybe_push_daily("u1", now=now.replace(minute=59)) is False
    assert len(sent) == 1


def test_db_flag_survives_process_restart(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path)
    now = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
    assert B.maybe_push_daily("u1", now=now) is True
    sent.clear()
    B._pushed_today.clear()                                 # simulate restart
    assert B.maybe_push_daily("u1", now=now) is False       # DB flag by userId
    assert sent == []


def test_outside_window_noop(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path)
    now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    assert B.maybe_push_daily("u1", now=now) is False
    assert sent == []
