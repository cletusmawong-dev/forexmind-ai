"""The stop-tightness gate (retcode 10016 protection): broker-LIVE when the
bridge serves the symbol's trade_stops_level, static 6-pip rule as the
fail-closed fallback. Regression anchors the 2026-11-11 EURUSD SELL that was
refused at 5.94 pips under the old hardcoded rule.
"""
from __future__ import annotations

from app.execution import mt5 as X

PIP = 0.0001          # EURUSD pip


def _reset(monkeypatch, stops_payload=None, dead=False):
    if dead:
        monkeypatch.setattr(X, "bridge_get", lambda path, timeout=6: None)
    else:
        monkeypatch.setattr(X, "bridge_get",
                            lambda path, timeout=6: stops_payload or {})


def test_broker_live_level_allows_a_tight_but_legal_stop(monkeypatch):
    # broker says 30 points = 0.0030 = 3 pips minimum -> a 5.94-pip SL is legal
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 30,
                         "stops_level_price": 0.0003})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is False and abs(min_pips - 3.0) < 1e-9


def test_broker_live_level_refuses_below_the_broker_truth(monkeypatch):
    # broker says 80 points = 8 pips -> 5.94-pip SL must refuse
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 80,
                         "stops_level_price": 0.0008})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is True and abs(min_pips - 8.0) < 1e-9


def test_bridge_dead_falls_back_to_the_static_6_pip_rule(monkeypatch):
    # fail-closed: no broker truth -> the historic 6-pip rule stands
    _reset(monkeypatch, dead=True)
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is True and abs(min_pips - 6.0) < 1e-9
    # and a 6.5-pip stop passes under the same fallback
    tight2, _ = X._stop_too_tight(1.11608, 1.11543, PIP, "EURUSD")
    assert tight2 is False


def test_zero_stops_level_is_no_data_falls_back_fail_closed(monkeypatch):
    # stops_level 0 is indistinguishable from an OLD bridge without the
    # field -> no data -> static 6-pip rule (fail-closed, by design)
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 0,
                         "stops_level_price": 0.0})
    tight, min_pips = X._stop_too_tight(1.11608, 1.11600, PIP, "EURUSD")
    assert tight is True and abs(min_pips - 6.0) < 1e-9


def test_tiny_live_level_gives_a_tiny_real_minimum(monkeypatch):
    # broker says 5 points = 0.5 pips -> a 0.8-pip stop is legal
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 5,
                         "stops_level_price": 0.00005})
    tight, min_pips = X._stop_too_tight(1.11608, 1.11600, PIP, "EURUSD")
    assert tight is False and abs(min_pips - 0.5) < 1e-9


def test_garbage_payload_is_ignored_not_crashed(monkeypatch):
    _reset(monkeypatch, {"point": 0, "trade_stops_level": "nonsense"})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is True and abs(min_pips - 6.0) < 1e-9   # static fallback


def test_unknown_pip_size_never_refuses_in_the_gate(monkeypatch):
    _reset(monkeypatch, dead=True)
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, None, "EXOTIC")
    assert tight is False and min_pips is None
