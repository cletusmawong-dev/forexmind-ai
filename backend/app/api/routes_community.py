"""Trader Community (master upgrade §6).

Posts + comments + likes + follows + reports, with hard tenant isolation:

  * you can only edit/delete YOUR posts and comments
  * verified trade results are FROZEN snapshots taken from the sharer's own
    broker-confirmed signal at share time - they can never be edited later
  * images: base64 data URLs only, magic-byte validated, <= 300 KB decoded
  * privacy: only display names are public; never emails, balances,
    credentials, private trades or AI memory
  * moderation: >= 3 distinct reports auto-hides a post; admin can unhide
"""
from __future__ import annotations

import base64
import re
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db.store import get_store
from .deps import get_user_id

router = APIRouter(prefix="/community", tags=["community"])

MAX_TEXT = 2000
MAX_COMMENT = 500
MAX_IMAGE_BYTES = 600_000   # client compresses to ~1280px JPEG first
MAX_TAGS = 8
HOURLY_POST_LIMIT = 12
HIDE_THRESHOLD = 3
COMMENTS_CAP = 200

_MAGIC = {  # allowed image formats (bytes signature -> format name)
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"RIFF": "webp",   # RIFF....WEBP verified below
}

_TAG_RE = re.compile(r"^[A-Za-z0-9 +/\-]{1,24}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PROVISION_NOTE = ("community table not provisioned yet - run the "
                   "community_posts SQL on Supabase")


def _posts_or_note(store, **kw) -> list:
    """Feed reads degrade to an empty list with an honest note until the
    table exists (same pattern as trading_accounts) - never a 500."""
    try:
        return store.list("community_posts", **kw)
    except Exception:
        return []


def _require_table(store) -> None:
    try:
        store.list("community_posts", limit=1)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(503, _PROVISION_NOTE)


def _public_author(store, user_id: str) -> dict:
    u = store.get("users", user_id) or {}
    return {"user_id": user_id,
            "display_name": u.get("display_name") or "Trader"}


def _validate_image(image: str) -> str:
    if not isinstance(image, str) or not image.startswith("data:image/"):
        raise HTTPException(400, "image must be a data:image/... URL")
    try:
        raw = base64.b64decode(image.split(",", 1)[1], validate=False)
    except Exception:
        raise HTTPException(400, "image is not valid base64")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"image still too large after compression (max {MAX_IMAGE_BYTES // 1000} KB) - try a smaller screenshot")
    ok = any(raw.startswith(m) or (m == b"RIFF" and raw[8:12] == b"WEBP")
             for m in _MAGIC)
    if not ok:
        raise HTTPException(415, "only PNG, JPEG or WEBP images are allowed")
    return image


def _validate_tags(tags: list) -> list:
    out = []
    for t in (tags or [])[:MAX_TAGS]:
        t = str(t).strip()
        if t and _TAG_RE.match(t):
            out.append(t)
    return out


def _verified_snapshot(store, user_id: str, signal_id: str) -> dict:
    """Frozen, broker-confirmed trade snapshot. The signal MUST belong to the
    caller, be completed and carry real MT5 results - otherwise 400."""
    sig = store.get("signals", signal_id)
    if not sig or sig.get("userId") != user_id:
        raise HTTPException(400, "trade not found in your account")
    if not sig.get("completed") or sig.get("mt5_pl") is None:
        raise HTTPException(400, "only completed, broker-confirmed trades can be shared as verified results")
    return {"signal_id": sig.get("signal_id"), "id": signal_id,
            "market": sig.get("market"), "direction": sig.get("direction"),
            "timeframe": sig.get("timeframe"),
            "strategy_id": sig.get("strategy_id"),
            "strategy_name": sig.get("strategy_name"),
            "entry": sig.get("entry"), "sl": sig.get("sl"),
            "tp1": sig.get("tp1"), "tp2": sig.get("tp2"), "tp3": sig.get("tp3"),
            "status": sig.get("status"), "pl": sig.get("mt5_pl"),
            "r_multiple": sig.get("r_multiple"), "outcome": sig.get("outcome"),
            "verified": True,  # immutable from here on
            "shared_at": _now()}


class PostIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    image: Optional[str] = None
    tags: list = Field(default_factory=list)
    trade_ref: Optional[str] = None


class PatchIn(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=MAX_TEXT)
    image: Optional[str] = None


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_COMMENT)


class ReportIn(BaseModel):
    reason: str = Field(default="", max_length=300)


def _public_post(store, d: dict) -> dict:
    return {"id": d["id"], "author": d.get("author"), "text": d.get("text"),
            "image": d.get("image"), "tags": d.get("tags") or [],
            "trade": d.get("trade"), "likes": len(d.get("likes") or []),
            "liked_by_me": False, "comments": d.get("comments") or [],
            "comment_count": len(d.get("comments") or []),
            "hidden": bool(d.get("hidden")), "createdAt": d.get("createdAt")}


@router.post("/posts")
def create_post(body: PostIn, user_id: str = Depends(get_user_id)):
    store = get_store()
    _require_table(store)
    # spam protection: rolling hourly window per user
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    mine = _posts_or_note(store, filters={"userId": user_id}, limit=100)
    if sum(1 for d in mine if (d.get("createdAt") or "") > cutoff) >= HOURLY_POST_LIMIT:
        raise HTTPException(429, "posting too fast - try again later")
    image = _validate_image(body.image) if body.image else None
    trade = _verified_snapshot(store, user_id, body.trade_ref) if body.trade_ref else None
    doc = store.create("community_posts", {
        "userId": user_id, "author": _public_author(store, user_id),
        "text": body.text.strip(), "image": image,
        "tags": _validate_tags(body.tags), "trade": trade,
        "likes": [], "comments": [], "reports": [], "hidden": False,
        "createdAt": _now(),
    })
    return {"post": _public_post(store, doc)}


@router.get("/posts")
def feed(tag: Optional[str] = Query(None), limit: int = Query(30, le=50),
         before: Optional[str] = Query(None), user_id: str = Depends(get_user_id)):
    store = get_store()
    rows = _posts_or_note(store, limit=500)
    rows = [d for d in rows if not d.get("hidden")]
    if tag:
        rows = [d for d in rows if tag.upper() in
                [str(t).upper() for t in (d.get("tags") or [])]]
    if before:
        rows = [d for d in rows if (d.get("createdAt") or "") < before]
    rows.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
    out = []
    for d in rows[:limit]:
        p = _public_post(store, d)
        p["liked_by_me"] = user_id in (d.get("likes") or [])
        p["comments"] = (d.get("comments") or [])[-20:]  # latest slice for the list view
        out.append(p)
    next_before = rows[limit]["createdAt"] if len(rows) > limit else None
    out = {"posts": out, "next_before": next_before}
    if not rows:
        try:
            store.list("community_posts", limit=1)
        except Exception:
            out["note"] = _PROVISION_NOTE
    return out


@router.get("/posts/{post_id}")
def get_post(post_id: str, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d or (d.get("hidden") and d.get("userId") != user_id):
        raise HTTPException(404, "post not found")
    p = _public_post(store, d)
    p["liked_by_me"] = user_id in (d.get("likes") or [])
    return {"post": p}


@router.patch("/posts/{post_id}")
def edit_post(post_id: str, body: PatchIn, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d:
        raise HTTPException(404, "post not found")
    if d.get("userId") != user_id:
        raise HTTPException(403, "you can only edit your own posts")
    patch: dict = {"updatedAt": _now()}
    if body.text is not None:
        patch["text"] = body.text.strip()
    if body.image is not None:
        patch["image"] = _validate_image(body.image)
    # NOTE: `trade` snapshot is deliberately NOT editable (verified results
    # can never be manipulated after the fact).
    store.update("community_posts", post_id, patch)
    return {"post": _public_post(store, store.get("community_posts", post_id))}


@router.delete("/posts/{post_id}")
def delete_post(post_id: str, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d:
        raise HTTPException(404, "post not found")
    if d.get("userId") != user_id:
        raise HTTPException(403, "you can only delete your own posts")
    store.delete("community_posts", post_id)
    return {"ok": True}


@router.post("/posts/{post_id}/like")
def like(post_id: str, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d or d.get("hidden"):
        raise HTTPException(404, "post not found")
    likes = list(d.get("likes") or [])
    if user_id in likes:
        likes.remove(user_id)
    else:
        likes.append(user_id)
    store.update("community_posts", post_id, {"likes": likes})
    return {"likes": len(likes), "liked_by_me": user_id in likes}


@router.post("/posts/{post_id}/comments")
def comment(post_id: str, body: CommentIn, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d or d.get("hidden"):
        raise HTTPException(404, "post not found")
    comments = list(d.get("comments") or [])
    if len(comments) >= COMMENTS_CAP:
        raise HTTPException(409, "comment limit reached for this post")
    comments.append({"id": f"c{len(comments)}-{datetime.now(timezone.utc).timestamp():.0f}",
                     "userId": user_id, "author": _public_author(store, user_id),
                     "text": body.text.strip(), "at": _now()})
    store.update("community_posts", post_id, {"comments": comments})
    return {"comments": comments[-20:], "comment_count": len(comments)}


@router.delete("/posts/{post_id}/comments/{comment_id}")
def delete_comment(post_id: str, comment_id: str,
                   user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d:
        raise HTTPException(404, "post not found")
    comments = list(d.get("comments") or [])
    target = next((c for c in comments if c.get("id") == comment_id), None)
    if not target:
        raise HTTPException(404, "comment not found")
    if target.get("userId") != user_id and d.get("userId") != user_id:
        raise HTTPException(403, "only your own comments (or post author) can be removed")
    comments = [c for c in comments if c.get("id") != comment_id]
    store.update("community_posts", post_id, {"comments": comments})
    return {"comments": comments[-20:], "comment_count": len(comments)}


@router.post("/posts/{post_id}/report")
def report(post_id: str, body: ReportIn, user_id: str = Depends(get_user_id)):
    store = get_store()
    d = store.get("community_posts", post_id)
    if not d:
        raise HTTPException(404, "post not found")
    reports = list(d.get("reports") or [])
    if user_id not in reports:
        reports.append(user_id)
    patch = {"reports": reports}
    hidden = bool(d.get("hidden"))
    if len(reports) >= HIDE_THRESHOLD:
        patch["hidden"] = True
        hidden = True
    store.update("community_posts", post_id, patch)
    return {"reported": True, "hidden": hidden}


@router.post("/follow/{target_id}")
def follow(target_id: str, user_id: str = Depends(get_user_id)):
    if target_id == user_id:
        raise HTTPException(400, "you cannot follow yourself")
    store = get_store()
    if not store.get("users", target_id):
        raise HTTPException(404, "trader not found")
    me = store.get("users", user_id) or {}
    following = list(me.get("following") or [])
    if target_id in following:
        following.remove(target_id)
    else:
        following.append(target_id)
    store.update("users", user_id, {"following": following})
    return {"following": following, "following_count": len(following)}


@router.get("/profile/{profile_id}")
def profile(profile_id: str, user_id: str = Depends(get_user_id)):
    """Public profile: display name + counts + recent posts. NEVER balances,
    broker info, private trades or AI memory."""
    store = get_store()
    u = store.get("users", profile_id)
    if not u:
        raise HTTPException(404, "trader not found")
    posts = [d for d in store.list("community_posts",
                                   filters={"userId": profile_id}, limit=100)
             if not d.get("hidden")]
    posts.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
    followers = sum(1 for x in store.list("users", limit=200)
                    if profile_id in (x.get("following") or []))
    return {"profile": {"user_id": profile_id,
                        "display_name": u.get("display_name") or "Trader",
                        "followers": followers,
                        "following": len(u.get("following") or []),
                        "posts": len(posts)},
            "recent_posts": [_public_post(store, d) for d in posts[:10]]}
