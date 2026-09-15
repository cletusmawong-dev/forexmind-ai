"""Every user-visible string must be plain ASCII (fancy chars read as CJK)."""
from app.core.plain_text import to_plain


def test_fancy_chars_normalized():
    src = "\u2019\u201cquotes\u201d \u2014 dash \u00b7 dot \u2026 more \u2192 arrow \uff0b plus"
    out = to_plain(src)
    assert out == "'\"quotes\" - dash - dot ... more -> arrow + plus"
    assert all(ord(c) < 128 for c in out)


def test_ascii_untouched():
    assert to_plain("GBPUSD SELL 15M - TP2: +2.5R") == "GBPUSD SELL 15M - TP2: +2.5R"


def test_notify_sanitizes(monkeypatch, tmp_path):
    import os
    from app.db.store import LocalStore
    from app.notifications import service as S
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(S, "get_store", lambda: store)
    monkeypatch.setattr(S.settings, "telegram_bot_token", "")   # no push in test
    S.notify("u1", "NEW_SIGNAL", "WIN \u2014 EURUSD", "Body \u00b7 with \u2026 fancy \u2192 chars")
    doc = store.list("notifications", limit=1)[0]
    assert doc["title"] == "WIN - EURUSD"
    assert doc["body"] == "Body - with ... fancy -> chars"
    assert all(ord(c) < 128 for c in doc["title"] + doc["body"])
