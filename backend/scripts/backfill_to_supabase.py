"""One-time Firestore -> Supabase backfill (§21.9). Idempotent upserts.

Usage (from backend/, with the NORMAL backend env + SUPABASE_URL +
SUPABASE_SERVICE_KEY set):
    python3 scripts/backfill_to_supabase.py [--collections users,signals]

Reads every doc from Firestore and upserts it into Supabase. Safe to re-run
(it only overwrites rows with fresher Firestore state; it never deletes).
NEVER touches the live backend - the running app keeps using Firestore until
the user flips DATABASE=supabase after the agreed 48h parallel-run.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests


def upsert(base: str, key: str, coll: str, docs: list) -> int:
    url = f"{base.rstrip('/')}/rest/v1/{coll}?on_conflict=id"
    r = requests.post(url, data=json.dumps(
        [{"id": d["id"], "data": {k: v for k, v in d.items() if k != "id"}}
         for d in docs]),
        headers={"Content-Type": "application/json", "apikey": key,
                 "Authorization": f"Bearer {key}",
                 "Prefer": "resolution=merge-duplicates"},
        timeout=60)
    if r.status_code not in (200, 201):
        print(f"  !! {coll}: {r.status_code} {r.text[:200]}")
        return 0
    return len(docs)


def main() -> None:
    from app.config import settings
    from app.db.store import COLLECTIONS, get_store
    only = None
    if "--collections" in sys.argv:
        only = set(sys.argv[sys.argv.index("--collections") + 1].split(","))
    base = settings.supabase_url
    key = settings.supabase_service_key
    if not base or not key:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY missing")
    store = get_store()
    total = 0
    for coll in COLLECTIONS:
        if only and coll not in only:
            continue
        try:
            docs = store.list(coll, limit=0)
        except Exception as exc:
            print(f"  !! {coll}: read failed {type(exc).__name__}: {exc}")
            continue
        n = 0
        for i in range(0, len(docs), 200):
            n += upsert(base, key, coll, docs[i:i + 200])
        total += n
        print(f"  {coll}: {n}/{len(docs)} upserted")
    print(f"DONE - {total} docs")


if __name__ == "__main__":
    main()
