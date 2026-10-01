"""Stage 8 build-everything: Supabase migration PREP (§21.9).

Rules under test:
 - SupabaseStore implements the EXACT shared interface with correct
   PostgREST request construction (filters/operators/order/limit/count)
 - DATABASE selector: firestore default; supabase -> SupabaseStore;
   dual -> DualStore (write BOTH, read PRIMARY); supabase misconfig falls
   back honestly to LocalStore
 - DualStore: shadow failures are logged, never raised, primary untouched
 - NOTHING flips by default: without DATABASE env the app keeps Firestore
"""
import json

import pytest


@pytest.fixture()
def clean_store_global(monkeypatch):
    """Isolate the module-level _store between tests."""
    from app.db import store as store_mod
    prev, prev_err = store_mod._store, store_mod.store_init_error
    store_mod._store = None
    yield store_mod
    store_mod._store = prev
    store_mod.store_init_error = prev_err


class FakeResp:
    def __init__(self, status_code=200, rows=None, text="", headers=None):
        self.status_code = status_code
        self._rows = rows if rows is not None else []
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._rows


class FakeHTTP:
    """Records PostgREST calls; canned row store for get/list semantics."""
    def __init__(self):
        self.calls = []
        self.rows = {}          # coll -> [row dicts]

    def _q(self, url):
        from urllib.parse import urlparse, parse_qsl
        u = urlparse(url)
        return u.path.rsplit("/", 1)[-1], dict(parse_qsl(u.query))

    def request(self, method, url, data=None, headers=None, timeout=None):
        coll, params = self._q(url)
        self.calls.append({"method": method, "coll": coll, "params": params,
                           "data": json.loads(data) if data else None,
                           "headers": headers})
        FIELD = "data->>"

        def field_of(k):
            return k[len(FIELD):] if k.startswith(FIELD) else None

        if method == "POST":
            d = json.loads(data) if data else {}
            rows = d if isinstance(d, list) else [d]
            for x in rows:
                assert set(x.keys()) == {"id", "data"}, "narrow row contract"
            self.rows.setdefault(coll, []).extend(rows)
            return FakeResp(201, rows)
        if method == "GET":
            out = list(self.rows.get(coll, []))
            for k, v in params.items():
                f = field_of(k)
                if k == "order" or f is None:
                    continue
                if v.startswith("eq."):
                    out = [r for r in out if str(r["data"].get(f)) == v[3:]]
            if "order" in params:
                spec = params["order"]
                field = spec.split("data->>")[-1].split(".")[0]
                desc = ".desc" in spec
                out.sort(key=lambda r: str(r["data"].get(field) or ""), reverse=desc)
            total = len(out)
            if "limit" in params:
                out = out[:int(params["limit"])]
            prefer = (headers or {}).get("Prefer", "")
            code = 206 if "count=exact" in prefer else 200
            return FakeResp(code, out,
                            headers={"Content-Range": f"0-{max(0, total - 1)}/{total}"})
        if method == "PATCH":
            d = json.loads(data) if data else {}
            matched = []
            for r in self.rows.get(coll, []):
                if str(r.get("id")) == params.get("id", "").replace("eq.", ""):
                    assert set(d.keys()) <= {"data"}, "update merges via data column"
                    r["data"].update(d["data"])
                    matched = [r]
            return FakeResp(200, matched)
        if method == "DELETE":
            before = len(self.rows.get(coll, []))
            self.rows[coll] = [r for r in self.rows.get(coll, [])
                               if str(r.get("id")) != params.get("id", "").replace("eq.", "")]
            return FakeResp(204 if len(self.rows.get(coll, [])) < before else 404)
        return FakeResp(405, text="bad method")


@pytest.fixture()
def sb(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
    from app.config import settings
    monkeypatch.setattr(settings, "supabase_url", "https://fake.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "svc-key")
    from app.db.supabase_store import SupabaseStore
    store = SupabaseStore()
    http = FakeHTTP()
    monkeypatch.setattr(store, "_url", store._url)  # keep real URL builder
    import app.db.supabase_store as S
    monkeypatch.setattr(S.requests, "post",
                        lambda url, **kw: http.request("POST", url, **kw))
    monkeypatch.setattr(S.requests, "get",
                        lambda url, **kw: http.request("GET", url, **kw))
    monkeypatch.setattr(S.requests, "patch",
                        lambda url, **kw: http.request("PATCH", url, **kw))
    monkeypatch.setattr(S.requests, "delete",
                        lambda url, **kw: http.request("DELETE", url, **kw))
    return store, http, monkeypatch


def test_supabase_interface_create_get_update_delete(sb):
    store, http, _ = sb
    doc = store.create("signals", {"userId": "u1", "market": "EURUSD"},
                       doc_id="sig1")
    assert doc["id"] == "sig1" and doc["userId"] == "u1"
    assert "createdAt" in doc                       # interface parity with other stores
    assert store.get("signals", "sig1")["market"] == "EURUSD"
    upd = store.update("signals", "sig1", {"outcome": "WIN"})
    assert upd["outcome"] == "WIN"
    assert store.delete("signals", "sig1") is True
    assert store.get("signals", "sig1") is None


def test_supabase_list_filters_order_limit_and_count(sb):
    store, http, _ = sb
    for i in range(5):
        store.create("signals", {"userId": "u1", "market": f"M{i}", "n": i},
                     doc_id=f"s{i}")
    rows = store.list("signals", filters={"userId": "u1"},
                      order_by="createdAt", desc=True, limit=2)
    assert len(rows) == 2
    # correct PostgREST query shape
    last = http.calls[-1]
    assert last["params"]["data->>userId"] == "eq.u1"      # jsonb text filter
    assert last["params"]["order"] == "data->>createdAt.desc.nullslast,id.asc"  # id tiebreaker for paging
    assert last["params"]["limit"] == "2"
    # operator filters translate
    store.list("signals", filters={
        "market": ("in", ["M1", "M2"]), "n": ("gte", 1), "n2": ("lte", 9)})
    p = http.calls[-1]["params"]
    assert p["data->>market"] == "in.(M1,M2)"
    assert p["data->>n"] == "gte.1" and p["data->>n2"] == "lte.9"
    # arrows survive URL building (PostgREST operators must not be encoded away)
    from urllib.parse import urlparse
    assert "data->>" in urlparse(http.calls[-1]["params"] and "https://x/?"
                                 + "&".join(f"{k}={v}" for k, v in p.items())).query
    # count uses Prefer: count=exact
    n = store.count("signals", filters={"userId": "u1"})
    assert n == 5
    assert http.calls[-1]["headers"]["Prefer"] == "count=exact"


def test_database_selector_defaults_to_supabase(clean_store_global, monkeypatch):
    """User directive 2026-09-30: Supabase is the default backend - even an
    unset/garbage DATABASE must never resurrect the Firestore quota path."""
    store_mod = clean_store_global
    from app.config import settings
    monkeypatch.setattr(settings, "database_backend", "")
    monkeypatch.setattr(settings, "supabase_url", "https://fake.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "svc-key")
    s = store_mod.get_store()
    assert type(s).__name__ == "SupabaseStore"


def test_database_selector_firestore_still_explicit(clean_store_global, monkeypatch):
    """Explicit rollback mode keeps working."""
    store_mod = clean_store_global
    from app.config import settings
    monkeypatch.setattr(settings, "database_backend", "firestore")
    monkeypatch.setattr(settings, "firebase_project_id", "")   # no creds in tests
    s = store_mod.get_store()
    assert type(s).__name__ in ("LocalStore", "FirestoreStore")
    assert type(s).__name__ != "SupabaseStore"


def test_database_selector_supabase(clean_store_global, monkeypatch):
    store_mod = clean_store_global
    from app.config import settings
    monkeypatch.setattr(settings, "database_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://fake.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "svc-key")
    s = store_mod.get_store()
    assert type(s).__name__ == "SupabaseStore"


def test_dual_store_writes_both_reads_primary(sb, clean_store_global, monkeypatch):
    from app.db import store as store_mod
    from app.db.supabase_store import DualStore
    from app.db.store import LocalStore
    sb_store, http, _ = sb
    primary = LocalStore(path="/tmp/never.json")
    dual = DualStore(primary=primary, shadow=sb_store)
    doc = dual.create("signals", {"userId": "u1", "market": "XAUUSD"}, doc_id="d1")
    assert doc["id"] == "d1"
    assert primary.get("signals", "d1")["market"] == "XAUUSD"     # primary has it
    assert sb_store.get("signals", "d1")["market"] == "XAUUSD"    # shadow has it
    # shadow failure is swallowed LOUDLY; primary keeps working
    def boom(*a, **k):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(sb_store, "update", boom)
    out = dual.update("signals", "d1", {"outcome": "WIN"})
    assert out["outcome"] == "WIN"                                # primary fine
    # reads always primary
    primary.create("signals", {"userId": "u1"}, doc_id="d2")
    assert dual.get("signals", "d2")["id"] == "d2"
    assert len(dual.list("signals", filters={"userId": "u1"})) == 2
    assert dual.count("signals") == 2


def test_supabase_misconfig_falls_back_honestly(clean_store_global, monkeypatch):
    store_mod = clean_store_global
    from app.config import settings
    monkeypatch.setattr(settings, "database_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_key", "")
    s = store_mod.get_store()
    assert type(s).__name__ != "SupabaseStore"                    # never half-broken
    assert store_mod.store_init_error and "supabase" in store_mod.store_init_error


def test_update_preserves_body_id_invariant(sb):
    """data->>id filters (engine walls, version control, status routes) need
    id INSIDE the jsonb - update() must never strip it (2026-09-30 incident:
    a status update silently broke every subsequent id-filter for the row)."""
    store, http, _ = sb
    store.create("strategies", {"id": "s1", "status": "ACTIVE"}, doc_id="s1")
    store.update("strategies", "s1", {"status": "DISABLED"})
    rows = store.list("strategies", filters={"id": "s1"})
    assert len(rows) == 1 and rows[0]["status"] == "DISABLED"
    patch_call = [c for c in http.calls if c["method"] == "PATCH"][-1]
    body = patch_call["data"]
    if isinstance(body, str):
        body = __import__("json").loads(body)
    assert body["data"]["id"] == "s1"


def test_invite_gated_registration():
    """Multi-user: only owner-minted invites can register; new users are
    born signals-only (trading_permission locked)."""
    from fastapi.testclient import TestClient
    from app.db.store import LocalStore
    from app.db import store as store_mod
    from app.state import State
    from app.config import settings
    from app.main import app
    from app.api.deps import get_user_id
    import tempfile

    tmp = tempfile.mkdtemp()
    st = LocalStore(path=f"{tmp}/db.json")
    store_mod._store = st
    prev = State.store
    State.store = st
    settings.owner_user_id = "boss"
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            # non-owner cannot mint
            app.dependency_overrides[get_user_id] = lambda: "rando"
            r = c.post("/api/auth/invite", json={})
            assert r.status_code == 403
            app.dependency_overrides[get_user_id] = lambda: "boss"
            r = c.post("/api/auth/invite", json={})
            assert r.status_code == 200, r.text
            invite = r.json()["invite"]
            # registration without invite is refused
            r = c.post("/api/auth/register", json={
                "email": "sis@example.com", "password": "secret7", "display_name": "Sis"})
            assert r.status_code == 403
            # registration with the invite works and is born locked
            r = c.post("/api/auth/register", json={
                "email": "sis@example.com", "password": "secret7",
                "display_name": "Sis", "invite": invite})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["trading_permission"] == "locked"
            assert r.json()["user"]["role"] == "user"
            # invite consumed
            r = c.post("/api/auth/register", json={
                "email": "other@example.com", "password": "secret7", "invite": invite})
            assert r.status_code == 403
            # duplicate email refused
            r = c.post("/api/auth/invite", json={})
            inv2 = r.json()["invite"]
            r = c.post("/api/auth/register", json={
                "email": "sis@example.com", "password": "secret7", "invite": inv2})
            assert r.status_code == 409
    finally:
        State.store = prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)


def test_agent_status_hides_retired_strategies():
    """/api/agent/status must list live strategies only (2026-10-01: the
    hardcoded zero-lag entry ghosted the Agent screen for a week)."""
    from fastapi.testclient import TestClient
    from app.db.store import LocalStore
    from app.db import store as store_mod
    from app.state import State
    from app.config import settings
    from app.main import app
    from app.api.deps import get_user_id
    import tempfile

    tmp = tempfile.mkdtemp()
    st = LocalStore(path=f"{tmp}/db.json")
    store_mod._store = st
    prev = State.store
    State.store = st
    settings.owner_user_id = "boss"
    app.dependency_overrides[get_user_id] = lambda: "boss"
    try:
        with TestClient(app) as c:
            r = c.get("/api/agent/status")
            assert r.status_code == 200, r.text[:200]
            sv = r.json().get("strategy_versions", {})
            assert "strategy_1_zero_lag" not in sv
            assert "strategy_1_vp_pivots" in sv
    finally:
        State.store = prev
        State.ready = False
        app.dependency_overrides.pop(get_user_id, None)
