"""API dependencies - authentication (SPEC §45: users access only their own data)."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from ..core.security import verify_token
from ..db.store import get_store


def get_user_id(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = verify_token(authorization.removeprefix("Bearer ").strip())
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    store = get_store()
    udoc = store.get("users", uid)
    if not udoc:
        raise HTTPException(status_code=401, detail="Unknown user")
    if udoc.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Account suspended")
    return uid


def optional_user_id(authorization: str = Header(default="")) -> str | None:
    if authorization.startswith("Bearer "):
        uid = verify_token(authorization.removeprefix("Bearer ").strip())
        if uid:
            return uid
    return None
