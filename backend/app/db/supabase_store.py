"""Supabase store (Master Upgrade §21.9 - migration PREP, flip is gated).

Same interface as FirestoreStore/LocalStore:
    create(coll, doc, doc_id=None) / get / update / delete /
    list(coll, filters, order_by, desc, limit) / count(coll, filters)

Design:
 - one Postgres table per collection: (id text primary key, data jsonb)
 - PostgREST (REST) only - no extra dependencies beyond requests
 - doc shape identical to Firestore docs: the "id" field lives INSIDE data
 - the DATABASE env selects the backend (firestore | supabase | dual);
   DEFAULT IS FIRESTORE - nothing flips until the user says go after the
   agreed 7 stable days (§21.9)
 - dual mode: writes go to BOTH (shadow errors are logged, never raised),
   reads come from the PRIMARY - the documented 48h parallel-run

Backfill: scripts/backfill_to_supabase.py (one-time, idempotent upserts).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import settings

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class SupabaseStore:
    """PostgREST-backed store with the exact shared-interface semantics."""

    def __init__(self, url: Optional[str] = None, service_key: Optional[str] = None,
                 timeout: int = 10):
        self.base = (url or settings.supabase_url or "").rstrip("/")
        self.key = service_key or settings.supabase_service_key or ""
        self.timeout = timeout
        if not self.base or not self.key:
            raise ValueError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------------
    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = dict(JSON_HEADERS)
        h["apikey"] = self.key
        h["Authorization"] = f"Bearer {self.key}"
        if extra:
            h.update(extra)
        return h

    def _url(self, coll: str, params: Optional[Dict[str, str]] = None) -> str:
        u = f"{self.base}/rest/v1/{coll}"
        if params:
            # manual encoding: PostgREST jsonb operators (data->>field) must
            # keep their '>' characters - urlencode would mangle them
            from urllib.parse import quote
            q = "&".join(
                f"{quote(str(k), safe='->>=(){},. \"')}={quote(str(v), safe='>=().,{}\" ')}"
                for k, v in params.items())
            u += "?" + q
        return u

    @staticmethod
    def _row_to_doc(row: Dict[str, Any]) -> Dict[str, Any]:
        data = row.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        doc = dict(data or {})
        doc["id"] = row.get("id")
        return doc

    # -- interface ---------------------------------------------------------
    def create(self, coll: str, doc: dict, doc_id: Optional[str] = None) -> dict:
        """Narrow-row contract: every doc is stored as {"id", "data": doc} -
        schema-free like Firestore (any new field just works; no migrations)."""
        from .store import new_id
        d = dict(doc)
        d["id"] = doc_id or d.get("id") or new_id()
        if "createdAt" not in d:
            d["createdAt"] = datetime.now(timezone.utc).isoformat()
        row = {"id": d["id"], "data": {k: v for k, v in d.items() if k != "id"}}
        r = requests.post(self._url(coll), data=json.dumps(row),
                          headers=self._headers({"Prefer": "return=representation"}),
                          timeout=self.timeout)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"supabase create {coll}: {r.status_code} {r.text[:200]}")
        rows = r.json() if r.text else []
        return self._row_to_doc(rows[0]) if rows else d

    def get(self, coll: str, doc_id: str) -> Optional[dict]:
        r = requests.get(self._url(coll, {"id": f"eq.{doc_id}"}),
                         headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            return None
        rows = r.json()
        return self._row_to_doc(rows[0]) if rows else None

    def update(self, coll: str, doc_id: str, patch: dict) -> Optional[dict]:
        # jsonb merge is read-modify-write (PATCH alone would replace the row)
        cur = self.get(coll, doc_id)
        if cur is None:
            return None
        merged = {**{k: v for k, v in cur.items() if k != "id"},
                  **{k: v for k, v in patch.items() if k != "id"}}
        # body-id invariant (incident 2026-09-30): every row's jsonb MUST carry
        # its own id - all data->>id filters (engine, version control, status
        # routes) return EMPTY otherwise. update() used to strip it.
        merged["id"] = doc_id
        row = {"data": merged}
        r = requests.patch(self._url(coll, {"id": f"eq.{doc_id}"}),
                           data=json.dumps(row),
                           headers=self._headers({"Prefer": "return=representation"}),
                           timeout=self.timeout)
        if r.status_code not in (200, 204):
            raise RuntimeError(f"supabase update {coll}/{doc_id}: "
                               f"{r.status_code} {r.text[:200]}")
        rows = r.json() if r.text else []
        return self._row_to_doc(rows[0]) if rows else self.get(coll, doc_id)

    def delete(self, coll: str, doc_id: str) -> bool:
        r = requests.delete(self._url(coll, {"id": f"eq.{doc_id}"}),
                            headers=self._headers(), timeout=self.timeout)
        return r.status_code in (200, 204)

    def list(self, coll: str, filters: Optional[Dict[str, Any]] = None,
             order_by: str = "createdAt", desc: bool = True, limit: int = 0) -> List[dict]:
        base_params: Dict[str, str] = {}
        for k, v in (filters or {}).items():
            if isinstance(v, tuple):
                op, val = v
                if op == "in":
                    base_params[f"data->>{k}"] = f"in.({','.join(str(x) for x in val)})"
                elif op in ("gte", "lte"):
                    base_params[f"data->>{k}"] = f"{op}.{val}"
                else:
                    base_params[f"data->>{k}"] = f"eq.{val}"
            else:
                base_params[f"data->>{k}"] = f"eq.{v}"
        # deterministic pagination: createdAt + row id tiebreaker (PostgREST
        # caps a single page at max_rows (default 1000) - incident 2026-09-30:
        # limit=0 silently truncated 12k-row tables to 1000)
        page = 1000
        out: List[dict] = []
        offset = 0
        while True:
            params = dict(base_params)
            params["order"] = (f"data->>{order_by}.{'desc' if desc else 'asc'}.nullslast,"
                               "id.asc")
            params["limit"] = str(min(page, max(1, min(limit - offset, page)))
                                  if limit else page)
            params["offset"] = str(offset)
            r = requests.get(self._url(coll, params), headers=self._headers(),
                             timeout=self.timeout)
            if r.status_code != 200:
                raise RuntimeError(f"supabase list {coll}: {r.status_code} {r.text[:200]}")
            rows = r.json() or []
            out.extend(self._row_to_doc(row) for row in rows)
            offset += len(rows)
            if len(rows) < page or (limit and offset >= limit):
                break
        return out

    def count(self, coll: str, filters: Optional[Dict[str, Any]] = None,
              limit: int = 0, fresh: bool = False) -> int:
        """fresh is accepted for interface parity with the other stores
        (the engine's daily-cap wall passes fresh=True): PostgREST
        count=exact is computed server-side and is ALWAYS live - there is
        no client cache to bypass. Incident 2026-09-30: missing this kwarg
        raised TypeError inside the wall and silently killed ALL signal
        generation for the day."""
        params: Dict[str, str] = {"limit": "1"}
        for k, v in (filters or {}).items():
            key = f"data->>{k}"
            params[key] = f"eq.{v[1]}" if isinstance(v, tuple) else f"eq.{v}"
        r = requests.get(self._url(coll, params),
                         headers=self._headers({"Prefer": "count=exact"}),
                         timeout=self.timeout)
        # PostgREST answers 206 Partial Content when a Content-Range is present
        if r.status_code not in (200, 206):
            raise RuntimeError(f"supabase count {coll}: {r.status_code}")
        rng = r.headers.get("Content-Range", "")
        try:
            return int(rng.split("/")[-1])
        except Exception:
            return len(r.json() or [])


class DualStore:
    """48h parallel-run (§21.9): writes BOTH, reads PRIMARY (firestore).
    Shadow (supabase) failures are logged loudly but never block trading."""

    def __init__(self, primary, shadow):
        self.primary = primary
        self.shadow = shadow

    @staticmethod
    def _jsonify(doc):
        """Firestore docs can carry non-JSON values returned by the primary:
        SERVER_TIMESTAMP arrives as a Sentinel, timestamps as datetimes. The
        JSON-backed shadow needs plain values (incident 2026-09-30: shadow
        create failed on every doc until this sanitizer)."""
        import datetime
        try:
            from google.cloud.firestore_v1 import _helpers as _fh
            sentinels = (_fh.Sentinel,)
        except Exception:
            sentinels = ()

        def cv(v):
            if isinstance(v, datetime.datetime):
                return v.isoformat()
            if sentinels and isinstance(v, sentinels):
                return None
            if isinstance(v, dict):
                return {k: cv(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [cv(x) for x in v]
            return v

        return {k: cv(v) for k, v in dict(doc).items()}

    def _shadow(self, op: str, fn):
        try:
            return fn()
        except Exception as exc:
            print(f"[dual-store] shadow {op} FAILED (primary unaffected): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            return None

    def create(self, coll, doc, doc_id=None):
        out = self.primary.create(coll, doc, doc_id)
        out = out or dict(doc, id=doc_id)
        self._shadow("create", lambda: self.shadow.create(
            coll, self._jsonify(dict(out, id=out["id"])), out["id"]))
        return out

    def get(self, coll, doc_id):
        return self.primary.get(coll, doc_id)

    def update(self, coll, doc_id, patch):
        out = self.primary.update(coll, doc_id, patch)
        if out:
            self._shadow("update", lambda: self.shadow.update(
                coll, doc_id, self._jsonify(out)))
        return out

    def delete(self, coll, doc_id):
        out = self.primary.delete(coll, doc_id)
        self._shadow("delete", lambda: self.shadow.delete(coll, doc_id))
        return out

    def list(self, coll, filters=None, order_by="createdAt", desc=True, limit=0):
        return self.primary.list(coll, filters=filters, order_by=order_by,
                                 desc=desc, limit=limit)

    def count(self, coll, filters=None, limit=0, fresh: bool = False):
        # primary backends have differing count() signatures; forward what
        # they accept (Firestore uses fresh to bypass its read cache)
        try:
            return self.primary.count(coll, filters=filters, fresh=fresh)
        except TypeError:
            return self.primary.count(coll, filters=filters)
