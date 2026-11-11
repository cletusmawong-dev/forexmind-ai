"""The stop-distance gate after the OWNER'S 2026-11-11 directive: the
invented 6-pip minimum is GONE. A signal executes with its own SL/TP; the
only distance rule is the BROKER's own live stops level (retcode 10016
truth). No broker truth -> execute. The 2026-11-11 EURUSD SELL (5.94-pip
stop, broker minimum 1 pip) would now EXECUTE.
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


def test_the_invented_pip_minimum_is_gone():
    assert not hasattr(X, "MIN_STOP_PIPS")


def test_broker_minimum_1_pip_lets_yesterdays_refused_trade_execute(monkeypatch):
    # the 2026-11-11 case: broker live minimum 10 points = 1 pip, stop 5.94
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 10,
                         "stops_level_price": 0.0001})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is False and abs(min_pips - 1.0) < 1e-9   # EXECUTES


def test_refuses_only_when_the_broker_itself_would_bounce(monkeypatch):
    # broker widened to 8 pips (news) -> a 5.94-pip stop would bounce (10016)
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 80,
                         "stops_level_price": 0.0008})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is True and abs(min_pips - 8.0) < 1e-9


def test_no_broker_truth_means_execute_never_refuse(monkeypatch):
    # OWNER DIRECTIVE: the platform never invents a minimum. Bridge silent ->
    # execute; the broker's own /execute validation has the final word and
    # any bounce is announced loudly, never silent.
    _reset(monkeypatch, dead=True)
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is False and min_pips is None


def test_garbage_payload_means_execute(monkeypatch):
    _reset(monkeypatch, {"point": 0, "trade_stops_level": "nonsense"})
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, PIP, "EURUSD")
    assert tight is False and min_pips is None


def test_tiny_live_level_gives_a_tiny_real_minimum(monkeypatch):
    # broker says 5 points = 0.5 pips -> a 0.8-pip stop is legal
    _reset(monkeypatch, {"point": 1e-05, "trade_stops_level": 5,
                         "stops_level_price": 0.00005})
    tight, min_pips = X._stop_too_tight(1.11608, 1.11600, PIP, "EURUSD")
    assert tight is False and abs(min_pips - 0.5) < 1e-9


def test_unknown_pip_size_never_refuses_in_the_gate(monkeypatch):
    _reset(monkeypatch, dead=True)
    tight, min_pips = X._stop_too_tight(1.11608, 1.1166737, None, "EXOTIC")
    assert tight is False and min_pips is None


def test_validate_order_no_longer_carries_a_static_minimum():
    import inspect
    from app.execution import pretrade
    sig = inspect.signature(pretrade.validate_order)
    assert sig.parameters["min_stop_pips"].default is None
