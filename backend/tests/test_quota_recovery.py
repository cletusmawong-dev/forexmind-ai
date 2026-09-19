"""Quota latch must RECOVER: one probe every 10 min, instant resume on reset."""
import pytest

from app.db.store import FirestoreStore


def test_quota_gate_recovers_after_probe_interval():
    store = object.__new__(FirestoreStore)   # skip __init__ (no Firebase needed)
    store.quota_mode = False
    store.quota_entered_at = 0.0
    store.QUOTA_PROBE_INTERVAL = 600.0

    store._enter_quota_mode()
    with pytest.raises(FirestoreStore._QuotaExhausted):
        store._check_quota_gate()            # latched -> fail fast

    # simulate 11 minutes passing
    import time as t
    store.quota_entered_at = t.monotonic() - 660
    store._check_quota_gate()                # probe allowed -> clears the latch
    assert store.quota_mode is False

    # a fresh quota error re-latches with a NEW timestamp
    store._enter_quota_mode()
    with pytest.raises(FirestoreStore._QuotaExhausted):
        store._check_quota_gate()
