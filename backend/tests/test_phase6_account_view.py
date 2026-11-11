"""Phase 6: the view account does not change who gets the order."""
from fastapi.testclient import TestClient

from app.config import settings
from app.db.store import LocalStore


def test_summary_kill_and_scoped_positions_do_not_send(monkeypatch, tmp_path):
    store = LocalStore(path=str(tmp_path / "db.json"))
    from app.db import store as store_mod
    monkeypatch.setattr(store_mod, "_store", store)
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "http://slot1:8700")
    monkeypatch.setattr(settings, "bridge_token", "tok")
    store.create("users", {"id": "boss", "email": "b@x.io"}, doc_id="boss")
    store.create("trading_accounts", {
        "userId": "boss", "label": "Primary", "mode": "vps_bridge",
        "is_active": True, "trading_enabled": True, "bridge_slot": 1,
        "magic": 20260914, "strategy_ids": ["strategy_mrms_scalper"],
        "kill_switch": False,
    }, doc_id="a1")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    posts = []
    import app.execution.mt5 as mt5
    monkeypatch.setattr(mt5, "bridge_post", lambda *a, **k: posts.append(1))
    monkeypatch.setattr(mt5, "bridge_get", lambda path, timeout=8: {
        "positions": [{
            "ticket": 3, "magic": 20260914, "symbol": "EURUSD", "type": "BUY",
            "volume": 0.1, "price_open": 1.1, "price_current": 1.2,
            "profit": 12.5, "comment": "SIG",
        }]
    })
    monkeypatch.setattr(mt5, "user_mode", lambda uid: "vps")
    try:
        client = TestClient(app)
        summary = client.get("/api/accounts/summary")
        assert summary.status_code == 200
        assert summary.json()["accounts"][0]["label"] == "Primary"
        killed = client.post("/api/accounts/a1/kill", json={"enabled": True})
        assert killed.status_code == 200
        assert killed.json()["account"]["kill_switch"] is True
        scoped = client.get("/api/positions/live?account_id=a1")
        assert scoped.status_code == 200
        body = scoped.json()
        assert body["count"] == 1
        assert body["day_pl"] == 12.5
        assert body["positions"][0]["account_label"] == "Primary"
        plain = client.get("/api/positions/live")
        assert plain.status_code == 200
        assert "account_id" not in plain.json()
        assert posts == []
    finally:
        app.dependency_overrides.pop(get_user_id, None)
