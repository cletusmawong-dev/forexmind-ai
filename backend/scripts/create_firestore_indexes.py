"""One-shot: create the composite indexes ForexMind's queries need.

Usage (from backend/):
    export FIREBASE_PROJECT_ID=your-project
    export FIREBASE_CREDENTIALS_JSON='{ ...service account json... }'
    python3 scripts/create_firestore_indexes.py

Idempotent — indexes that already exist are skipped (409).
"""
import json
import os
import sys

import requests

# collection -> equality-filter field sets used by app code.
# Every query also orderBy("createdAt" DESC), so each combo needs
# (fields..., createdAt DESC).
SPECS = {
    "agent_goals": [["userId"]],
    "experiments": [["userId"]],
    "hypotheses": [["source_lesson"], ["status"]],
    "lessons": [["dedupe_key"], ["userId"], ["userId", "strategy_id"]],
    "notifications": [["userId"], ["userId", "read"]],
    "settings": [["userId", "kind"]],
    "signals": [["userId"], ["userId", "completed"], ["userId", "day"], ["market", "completed"]],
    "strategies": [["id"]],
    "strategy_versions": [["strategy_id"]],
    "trades": [["userId"]],
    "users": [["email"], ["id"]],
}

SCOPE = "https://www.googleapis.com/auth/datastore"
BASE = "https://firestore.googleapis.com/v1"


def credentials():
    proj = os.getenv("FIREBASE_PROJECT_ID", "")
    raw = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    if not proj or not raw:
        path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if path and os.path.exists(path):
            raw = open(path).read()
        else:
            sys.exit("Set FIREBASE_PROJECT_ID and FIREBASE_CREDENTIALS_JSON (or GOOGLE_APPLICATION_CREDENTIALS).")
    if not proj:
        proj = json.loads(raw).get("project_id", "")
    return proj, json.loads(raw)


def access_token(sa: dict) -> str:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(sa, scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def main():
    proj, sa = credentials()
    tok = access_token(sa)
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    ok = exists = failed = 0
    for coll, field_sets in SPECS.items():
        for fields in field_sets:
            body = {
                "queryScope": "COLLECTION",
                "fields": [{"fieldPath": f, "order": "ASCENDING"} for f in fields]
                + [{"fieldPath": "createdAt", "order": "DESCENDING"}],
            }
            url = f"{BASE}/projects/{proj}/databases/(default)/collectionGroups/{coll}/indexes"
            r = requests.post(url, headers=headers, json=body, timeout=30)
            name = f"{coll}({','.join(fields)})"
            if r.status_code in (200, 201):
                print(f"  created  {name}")
                ok += 1
            elif r.status_code == 409:
                print(f"  exists   {name}")
                exists += 1
            else:
                print(f"  FAILED   {name}: {r.status_code} {r.text[:160]}")
                failed += 1
    print(f"\n{ok} created, {exists} already existed, {failed} failed.")
    if ok:
        print("Indexes take 1-5 minutes to build; queries work once ready.")


if __name__ == "__main__":
    main()
