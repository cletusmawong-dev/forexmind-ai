"""DataStore abstraction (SPEC §43).

Collections mirror the Firestore structure exactly:

    users, markets, signals, trades, strategies, strategy_versions,
    agent_goals, agent_activity, lessons, hypotheses, experiments,
    notifications, performance, settings, backtests

Two implementations:
  * LocalStore    - JSON-file backed, used in the sandbox (no credentials).
  * FirestoreStore- real Firebase Firestore via firebase-admin. Activates
                    automatically when FIREBASE_PROJECT_ID +
                    (GOOGLE_APPLICATION_CREDENTIALS | FIREBASE_CREDENTIALS_JSON)
                    are set. Requires `pip install firebase-admin`.

Services only ever talk to the DataStore interface, so swapping to live
Firebase requires zero application-code changes.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from ..config import settings

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "db.json")

COLLECTIONS = [
    "users", "markets", "signals", "trades", "strategies", "strategy_versions",
    "agent_goals", "agent_activity", "lessons", "hypotheses", "experiments",
    "notifications", "performance", "settings", "backtests",
]


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


class LocalStore:
    """Firestore-shaped document store persisted to a single JSON file."""

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, dict]] = {c: {} for c in COLLECTIONS}
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    raw = json.load(f)
                for c in COLLECTIONS:
                    self._data[c] = raw.get(c, {})
            except Exception:
                pass

    def _save(self):
        with self._lock:
            if not self._dirty:
                return
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(self._data, f, default=str)
            os.replace(tmp, self.path)
            self._dirty = False

    def flush(self):
        with self._lock:
            self._dirty = True
            self._save()

    # -- CRUD ---------------------------------------------------------------
    def create(self, coll: str, doc: dict, doc_id: Optional[str] = None) -> dict:
        with self._lock:
            _id = doc_id or doc.get("id") or new_id()
            doc = dict(doc)
            doc["id"] = _id
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            doc.setdefault("createdAt", now)
            doc["updatedAt"] = now
            self._data[coll][_id] = doc
            self._dirty = True
            self._save()
            return doc

    def get(self, coll: str, doc_id: str) -> Optional[dict]:
        with self._lock:
            d = self._data.get(coll, {}).get(doc_id)
            return dict(d) if d else None

    def update(self, coll: str, doc_id: str, patch: dict) -> Optional[dict]:
        with self._lock:
            d = self._data.get(coll, {}).get(doc_id)
            if not d:
                return None
            d.update(patch)
            d["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._dirty = True
            self._save()
            return dict(d)

    def delete(self, coll: str, doc_id: str) -> bool:
        with self._lock:
            if doc_id in self._data.get(coll, {}):
                del self._data[coll][doc_id]
                self._dirty = True
                self._save()
                return True
            return False

    def list(self, coll: str, filters: Optional[Dict[str, Any]] = None,
             order_by: str = "createdAt", desc: bool = True, limit: int = 0) -> List[dict]:
        with self._lock:
            docs = [dict(d) for d in self._data.get(coll, {}).values()]
        if filters:
            for k, v in filters.items():
                if isinstance(v, tuple):
                    op, val = v
                    if op == "in":
                        docs = [d for d in docs if d.get(k) in val]
                    elif op == "gte":
                        docs = [d for d in docs if d.get(k, 0) >= val]
                    elif op == "lte":
                        docs = [d for d in docs if d.get(k, 0) <= val]
                else:
                    docs = [d for d in docs if d.get(k) == v]
        docs.sort(key=lambda d: d.get(order_by, ""), reverse=desc)
        return docs[:limit] if limit else docs

    def count(self, coll: str, filters: Optional[Dict[str, Any]] = None) -> int:
        return len(self.list(coll, filters))


class FirestoreStore:
    """Real Firebase Firestore adapter (activated by env credentials).

    Adds a short-TTL read cache (default 5s) with write-through invalidation.
    The UI polls every 4-8s; the cache keeps a single user comfortably inside
    the Firestore free tier without changing any read semantics.
    """

    CACHE_TTL = 5.0  # seconds

    def __init__(self):
        from firebase_admin import firestore, credentials  # lazy import
        import firebase_admin
        if not firebase_admin._apps:
            cred = None
            if settings.google_application_credentials and os.path.exists(settings.google_application_credentials):
                cred = credentials.Certificate(settings.google_application_credentials)
            elif settings.firebase_credentials_json:
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                    f.write(settings.firebase_credentials_json)
                    path = f.name
                cred = credentials.Certificate(path)
            firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id}) if cred else \
                firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
        self.db = firestore.client()
        self._cache: Dict[str, tuple] = {}  # key -> (monotonic_ts, value)
        self._cache_lock = threading.RLock()

    def _cache_get(self, key):
        import time as _t
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and (_t.monotonic() - hit[0]) < self.CACHE_TTL:
                return hit[1]
        return None

    def _cache_set(self, key, value):
        import time as _t
        with self._cache_lock:
            self._cache[key] = (_t.monotonic(), value)

    def _invalidate(self, coll: str, doc_id: Optional[str] = None):
        with self._cache_lock:
            keys = [k for k in self._cache if k.startswith(f"L:{coll}|") or k == f"G:{coll}/{doc_id}"]
            for k in keys:
                self._cache.pop(k, None)

    @staticmethod
    def _normalize(doc):
        """Firestore returns createdAt/updatedAt as timestamp objects; the rest
        of the app (and the local store) speak ISO strings. Convert on read so
        behavior is identical everywhere."""
        import datetime
        def conv(v):
            if isinstance(v, datetime.datetime):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            return v
        return {k: conv(v) for k, v in doc.items()}

    def _now(self):
        from firebase_admin import firestore
        return firestore.SERVER_TIMESTAMP

    def create(self, coll, doc, doc_id=None):
        ref = self.db.collection(coll).document(doc_id or new_id())
        doc = dict(doc); doc["id"] = ref.id
        doc.setdefault("createdAt", self._now()); doc["updatedAt"] = self._now()
        ref.set(doc)
        self._invalidate(coll, ref.id)
        return doc

    def get(self, coll, doc_id):
        key = f"G:{coll}/{doc_id}"
        hit = self._cache_get(key)
        if hit is not None:
            return dict(hit) if hit else None
        snap = self.db.collection(coll).document(doc_id).get()
        val = self._normalize(snap.to_dict()) if snap.exists else None
        self._cache_set(key, val)
        return val

    def update(self, coll, doc_id, patch):
        ref = self.db.collection(coll).document(doc_id)
        if not ref.get().exists:
            return None
        patch = dict(patch); patch["updatedAt"] = self._now()
        ref.update(patch)
        self._invalidate(coll, doc_id)
        return self.get(coll, doc_id)

    def delete(self, coll, doc_id):
        self.db.collection(coll).document(doc_id).delete()
        self._invalidate(coll, doc_id)
        return True

    def list(self, coll, filters=None, order_by="createdAt", desc=True, limit=0):
        fkey = repr(sorted((filters or {}).items(), key=lambda kv: kv[0]))
        key = f"L:{coll}|{fkey}|{order_by}|{desc}|{limit}"
        hit = self._cache_get(key)
        if hit is not None:
            return [dict(d) for d in hit]
        def build_query(with_order: bool):
            qq = self.db.collection(coll)
            if filters:
                for k, v in filters.items():
                    if isinstance(v, tuple):
                        op, val = v
                        fmap = {"in": "in", "gte": ">=", "lte": "<="}
                        qq = qq.where(k, fmap[op], val)
                    else:
                        qq = qq.where(k, "==", v)
            if with_order:
                qq = qq.order_by(order_by, direction="DESCENDING" if desc else "ASCENDING")
            if limit and with_order:
                qq = qq.limit(limit)
            return qq

        try:
            out = [self._normalize(d.to_dict()) | {"id": d.id} for d in build_query(True).stream()]
        except Exception as e:
            # Missing composite index safety net: fetch without ordering and
            # sort in memory — identical semantics, no downtime.
            if "index" not in str(e).lower():
                raise
            out = [self._normalize(d.to_dict()) | {"id": d.id} for d in build_query(False).stream()]
            out.sort(key=lambda d: str(d.get(order_by, "")), reverse=desc)
            if limit:
                out = out[:limit]
        self._cache_set(key, out)
        return out

    def count(self, coll, filters=None):
        """Server-side aggregation count (1 read) instead of N document reads."""
        key = f"C:{coll}|{repr(sorted((filters or {}).items(), key=lambda kv: kv[0]))}"
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        q = self.db.collection(coll)
        if filters:
            for k, v in filters.items():
                if isinstance(v, tuple):
                    op, val = v
                    fmap = {"in": "in", "gte": ">=", "lte": "<="}
                    q = q.where(k, fmap[op], val)
                else:
                    q = q.where(k, "==", v)
        try:
            from google.cloud.firestore_v1.aggregation import AggregationQuery
            agg = AggregationQuery(q)
            val = int(agg.count(alias="n").get()[0][0].value)
        except Exception:
            val = len(self.list(coll, filters, limit=10000))
        self._cache_set(key, val)
        return val

    def flush(self):
        pass


_store: Optional[Any] = None


def get_store() -> Any:
    global _store
    if _store is None:
        if settings.firebase_project_id:
            try:
                _store = FirestoreStore()
            except Exception:
                _store = LocalStore()
        else:
            _store = LocalStore()
    return _store
