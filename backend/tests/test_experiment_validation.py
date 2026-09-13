"""One-variable experiment restriction (SPEC §21, §28, §58) - HARD validation.

The spec-mandated attack: attempt to change Fast EMA 9 -> 10 AND ATR 14 -> 15
at the same time. The backend MUST reject it.
"""
import pytest

from app.learning.experiments import ExperimentError, validate_one_variable


def test_two_variables_must_be_rejected():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    attack = dict(base, fast_len=10, atr_len=15)   # TWO variables
    with pytest.raises(ExperimentError) as e:
        validate_one_variable("strategy_2_ema_atr", base, attack)
    msg = str(e.value)
    assert "rejected" in msg.lower()
    assert "more than one strategy variable" in msg
    assert "Only one strategy variable may be changed per experiment" in msg


def test_three_variables_also_rejected():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    attack = dict(base, fast_len=10, sl_mult=1.8, tp2_rr=2.5)
    with pytest.raises(ExperimentError):
        validate_one_variable("strategy_2_ema_atr", base, attack)


def test_no_change_rejected():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    with pytest.raises(ExperimentError) as e:
        validate_one_variable("strategy_2_ema_atr", base, dict(base))
    assert "no strategy variable" in str(e.value)


def test_single_change_allowed():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    changed = validate_one_variable("strategy_2_ema_atr", base, dict(base, fast_len=10))
    assert changed == "fast_len"


def test_unknown_variable_rejected():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    with pytest.raises(ExperimentError):
        validate_one_variable("strategy_2_ema_atr", base, dict(base, moon_factor=3))


def test_invalid_relation_rejected():
    base = {"fast_len": 9, "slow_len": 21, "atr_len": 14, "sl_mult": 1.5,
            "tp1_rr": 1.0, "tp2_rr": 2.0, "tp3_rr": 3.0, "expire_bars": 200}
    with pytest.raises(ExperimentError):
        validate_one_variable("strategy_2_ema_atr", base, dict(base, fast_len=50))


def test_strategy1_single_change_allowed():
    base = {"length": 70, "band_mult": 1.2, "tp1_rr": 1.5, "tp2_rr": 2.5,
            "expire_bars": 200}
    changed = validate_one_variable("strategy_1_zero_lag", base,
                                    dict(base, band_mult=1.1))
    assert changed == "band_mult"


def test_strategy1_two_variables_rejected():
    base = {"length": 70, "band_mult": 1.2, "tp1_rr": 1.5, "tp2_rr": 2.5,
            "expire_bars": 200}
    with pytest.raises(ExperimentError):
        validate_one_variable("strategy_1_zero_lag", base,
                              dict(base, length=75, tp1_rr=2.0))
