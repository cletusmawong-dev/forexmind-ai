"""Authentication & security (SPEC §45).

* XKiro API keys, Firebase admin credentials and provider secrets live ONLY in
  server environment variables - never in the mobile/web client.
* Passwords are salted+hashed; sessions use signed HMAC tokens.
* Every data query is scoped by userId: users can only access their own data.

When Firebase credentials are configured, token verification can be delegated
to Firebase Auth; the local issuer below keeps the sandbox fully functional.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from ..config import settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def make_salt() -> str:
    return os.urandom(8).hex()


def issue_token(user_id: str) -> str:
    payload = {"uid": user_id,
               "exp": time.time() + settings.token_ttl_hours * 3600}
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    try:
        body, sig = token.split(".")
        expect = _b64(hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("uid")
    except Exception:
        return None
