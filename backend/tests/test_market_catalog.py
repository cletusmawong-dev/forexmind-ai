"""Market catalog: users may pick from the full curated list.

Rules under test:
 - the catalog covers Majors / Crosses / Metals / Crypto and EVERY symbol
   maps in the live provider (no catalog symbol can end up unscannable)
 - risk settings accept any catalog symbol (e.g. AUDUSD) and reject
   unknown junk (e.g. FAKEUSD) with an honest message
 - the risk payload serves the catalog to the UI (available_markets +
   grouped market_catalog)
 - scan_universe(): defaults + every user-selected market, capped by
   MAX_SCAN_MARKETS (defaults survive first, user picks fill the rest)
"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch, tmp_path):
    os.environ["REPLAY_ENABLED"] = "0"
    from app.db.store import LocalStore
    from app.db import store as store_mod
    store = LocalStore(path=str(tmp_path / "db.json"))
    monkeypatch.setattr(store_mod, "_store", store)
    from app.state import State
    State.store = store
    from app.config import settings
    monkeypatch.setattr(settings, "owner_user_id", "boss")
    monkeypatch.setattr(settings, "bridge_url", "")
    from app.main import app
    from app.api.deps import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_every_catalog_symbol_maps_in_provider():
    from app.config import MARKET_CATALOG, MARKET_CATALOG_ALL
    from app.market_data.live_provider import SYMBOL_MAP
    assert len(MARKET_CATALOG_ALL) >= 20
    assert set(MARKET_CATALOG_ALL) == set(MARKET_CATALOG["Majors"]) \
        | set(MARKET_CATALOG["Crosses"]) | set(MARKET_CATALOG["Metals"]) \
        | set(MARKET_CATALOG["Crypto"])
    missing = [m for m in MARKET_CATALOG_ALL if m not in SYMBOL_MAP]
    assert not missing, f"catalog symbols without a data mapping: {missing}"


def test_risk_accepts_catalog_market_and_persists(env):
    r = env
    cur = r.get("/api/settings").json()
    patch = dict(cur)
    patch["allowed_markets"] = ["XAUUSD", "EURUSD", "AUDUSD", "BTCUSD"]
    res = r.patch("/api/settings", json=patch)
    assert res.status_code == 200, res.text
    now = r.get("/api/settings").json()
    assert "AUDUSD" in now["allowed_markets"]
    assert "BTCUSD" in now["allowed_markets"]


def test_risk_rejects_unknown_market(env):
    r = env
    cur = r.get("/api/settings").json()
    patch = dict(cur)
    patch["allowed_markets"] = ["XAUUSD", "FAKEUSD"]
    res = r.patch("/api/settings", json=patch)
    assert res.status_code in (400, 422)
    assert "FAKEUSD" in res.text


def test_risk_payload_serves_catalog(env):
    r = env
    risk = r.get("/api/settings").json()
    assert "AUDUSD" in risk["available_markets"]
    assert "XAGUSD" in risk["available_markets"]
    groups = {g["group"]: g["markets"] for g in risk["market_catalog"]}
    assert set(groups) >= {"Majors", "Crosses", "Metals", "Crypto"}
    assert "XAUUSD" in groups["Metals"]


def test_scan_universe_union_and_cap(env, monkeypatch):
    from app.agent import core as agent_core
    from app.config import settings
    monkeypatch.setattr(settings, "max_scan_markets", 10)

    store = agent_core.get_store()
    store.create("users", {"id": "u_a", "email": "a@x.y"})
    store.create("users", {"id": "u_b", "email": "b@x.y"})
    agent_core.ensure_user_docs("u_a")
    agent_core.ensure_user_docs("u_b")
    agent_core.patch_risk("u_a", {"allowed_markets": ["EURUSD", "AUDUSD", "GBPJPY"]})
    agent_core.patch_risk("u_b", {"allowed_markets": ["EURUSD", "BTCUSD"]})

    uni = agent_core.scan_universe()
    # defaults first...
    from app.config import INITIAL_MARKETS
    assert uni[:len(INITIAL_MARKETS)] == INITIAL_MARKETS
    # ...then every user pick appears exactly once
    for m in ("AUDUSD", "GBPJPY", "BTCUSD"):
        assert m in uni
    assert len(uni) == len(set(uni))


def test_scan_universe_cap_keeps_defaults_first(env, monkeypatch):
    from app.agent import core as agent_core
    from app.config import settings, INITIAL_MARKETS
    monkeypatch.setattr(settings, "max_scan_markets", len(INITIAL_MARKETS) + 1)
    store = agent_core.get_store()
    store.create("users", {"id": "u_c", "email": "c@x.y"})
    agent_core.ensure_user_docs("u_c")
    agent_core.patch_risk("u_c", {"allowed_markets": ["EURUSD", "AUDUSD", "NZDJPY"]})
    uni = agent_core.scan_universe()
    assert len(uni) == len(INITIAL_MARKETS) + 1  # capped
    assert uni[:len(INITIAL_MARKETS)] == INITIAL_MARKETS
