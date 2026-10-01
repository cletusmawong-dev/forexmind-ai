"""Auth routes (register / login / me). Firebase Auth adapter drops in later
without changing these shapes."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..core.permissions import audit
from ..core.security import hash_password, issue_token, make_salt, verify_token
from ..db.store import get_store
from ..models.schemas import LoginIn, RegisterIn
from .deps import get_user_id

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "display_name": u.get("display_name", ""),
            "demo": u.get("demo", False), "telegram_linked": bool(u.get("telegram_chat_id")),
            "role": u.get("role", "user"), "status": u.get("status", "active"),
            "trading_permission": u.get("trading_permission", "locked")}


@router.post("/register")
def register(body: dict):
    store = get_store()
    invite = store.get("invites", str(body.get("invite") or ""))
    if not invite or invite.get("status") != "unused":
        raise HTTPException(403, "Registration is by invite only - ask the owner for a link")
    email = str(body.get("email") or "").lower().strip()
    password = str(body.get("password") or "")
    display_name = str(body.get("display_name") or email.split("@")[0])
    if not email or "@" not in email or len(password) < 6:
        raise HTTPException(422, "Valid email and a password of at least 6 characters are required")
    if store.list("users", filters={"email": email}, limit=1):
        raise HTTPException(409, "An account with this email already exists")
    salt = make_salt()
    u = store.create("users", {
        "email": email, "display_name": display_name,
        "salt": salt, "password_hash": hash_password(password, salt),
        "demo": False,
        "role": "user",                    # Phase 12: admin is granted, never self-claimed
        "status": "active",
        "trading_permission": "locked",    # Phase 11: new users are SIGNAL ONLY
    })
    store.update("invites", invite["id"], {
        "status": "used", "used_by": u["id"],
        "used_at": datetime.now(timezone.utc).isoformat()})
    audit(u["id"], "user.register", u["id"], None,
          {"email": email}, f"invite {invite['id']}")
    return {"token": issue_token(u["id"]), "user": _public(u)}


@router.post("/invite")
def create_invite(user_id: str = Depends(get_user_id)):
    """Owner-only: mint one signup invite link (multi-user onboarding)."""
    if user_id != settings.owner_user_id:
        raise HTTPException(403, "Only the owner can mint invites")
    store = get_store()
    token = secrets.token_urlsafe(16)
    doc = store.create("invites", {
        "status": "unused", "created_by": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, doc_id=token)
    base = settings.public_app_url or "https://forexmind-ai-v3.netlify.app"
    return {"invite": doc["id"],
            "url": f"{base}/?invite={doc['id']}"}


@router.get("/invites")
def list_invites(user_id: str = Depends(get_user_id)):
    if user_id != settings.owner_user_id:
        raise HTTPException(403, "Only the owner")
    return {"invites": get_store().list("invites", limit=50)}


@router.post("/login")
def login(body: LoginIn):
    store = get_store()
    u = store.list("users", filters={"email": body.email.lower()}, limit=1)
    if not u or hash_password(body.password, u[0]["salt"]) != u[0]["password_hash"]:
        if getattr(store, "quota_mode", False):
            raise HTTPException(503, "Database daily quota reached - service pauses until the daily reset. Data is safe.")
        raise HTTPException(401, "Invalid email or password")
    return {"token": issue_token(u[0]["id"]), "user": _public(u[0])}


@router.get("/me")
def me(user_id: str = Depends(get_user_id)):
    return _public(get_store().get("users", user_id))
