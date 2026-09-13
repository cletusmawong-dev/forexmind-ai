"""Auth routes (register / login / me). Firebase Auth adapter drops in later
without changing these shapes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.security import hash_password, issue_token, make_salt, verify_token
from ..db.store import get_store
from ..models.schemas import LoginIn, RegisterIn
from .deps import get_user_id

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "display_name": u.get("display_name", ""),
            "demo": u.get("demo", False)}


@router.post("/register")
def register(body: RegisterIn):
    store = get_store()
    if store.list("users", filters={"email": body.email.lower()}, limit=1):
        raise HTTPException(409, "An account with this email already exists")
    salt = make_salt()
    u = store.create("users", {
        "email": body.email.lower(), "display_name": body.display_name,
        "salt": salt, "password_hash": hash_password(body.password, salt),
        "demo": False,
    })
    return {"token": issue_token(u["id"]), "user": _public(u)}


@router.post("/login")
def login(body: LoginIn):
    store = get_store()
    u = store.list("users", filters={"email": body.email.lower()}, limit=1)
    if not u or hash_password(body.password, u[0]["salt"]) != u[0]["password_hash"]:
        if getattr(store, "quota_mode", False):
            raise HTTPException(503, "Database daily quota reached — service pauses until the daily reset. Data is safe.")
        raise HTTPException(401, "Invalid email or password")
    return {"token": issue_token(u[0]["id"]), "user": _public(u[0])}


@router.get("/me")
def me(user_id: str = Depends(get_user_id)):
    return _public(get_store().get("users", user_id))
