"""ensure_strategy_docs must be idempotent and starve-proof.

Prod incident 2026-10-10: strategy_1_vp_pivots had a strategy_versions row
flipped to active:false; the old reconciliation tried to re-create the same
doc_id on every startup, the Supabase create 409ed, and the loop aborted
before reaching later strategies - so strategy_2_supply_demand_fvg never got
a doc and could not be paused (PATCH -> 404 "Unknown strategy").
"""
from app.learning import versions as vc


def test_ensure_survives_stale_version_row_and_creates_missing_docs(monkeypatch, tmp_path):
    from app.db.store import LocalStore
    store = LocalStore(str(tmp_path / "fx.json"))

    # vp_pivots-like: doc exists, its only version row is active:false
    store.create("strategies", {"status": "PAUSED"}, doc_id="strategy_1_vp_pivots")
    store.create("strategy_versions",
                 {"strategy_id": "strategy_1_vp_pivots", "version": "1.0",
                  "active": False, "params": {}, "changes": []},
                 doc_id="strategy_1_vp_pivots-v1.0")
    # supply_demand_fvg-like: no doc, no version row at all
    monkeypatch.setattr(vc, "get_store", lambda: store)

    vc.ensure_strategy_docs()
    vc.ensure_strategy_docs()  # second run must be a clean no-op (idempotent)

    # the previously starved strategy now has a doc...
    assert store.list("strategies", filters={"id": "strategy_2_supply_demand_fvg"})
    # ...and the stale row was reactivated in place, not duplicated
    rows = store.list("strategy_versions",
                      filters={"strategy_id": "strategy_1_vp_pivots"})
    active = [r for r in rows if r.get("active")]
    assert len(rows) == 1 and len(active) == 1
