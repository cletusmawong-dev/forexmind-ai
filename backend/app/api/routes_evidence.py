"""Evidence Engine APIs (3.0 spec Stage 1.5).

User-scoped: every route reads/writes ONLY the caller's evidence objects.
Generation is deterministic recomputation - it never mutates strategies,
permissions or execution state.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..api.deps import get_user_id
from ..db.store import get_store
from ..evidence import engine

router = APIRouter(prefix="/evidence", tags=["evidence"])


class GenerateBody(BaseModel):
    strategy_id: Optional[str] = None
    market: Optional[str] = None
    session: Optional[str] = None


@router.get("")
def list_all(subject_type: Optional[str] = Query(None),
             strategy_id: Optional[str] = Query(None),
             user_id: str = Depends(get_user_id)):
    docs = engine.list_evidence(user_id, subject_type=subject_type,
                                strategy_id=strategy_id)
    return {"evidence": docs, "count": len(docs),
            "states": {s: sum(1 for d in docs if d.get("state") == s)
                       for s in engine.EVIDENCE_STATES + ("CONTRADICTED",)}}


@router.get("/{evidence_id}")
def get_one(evidence_id: str, user_id: str = Depends(get_user_id)):
    doc = get_store().get("evidence", evidence_id)
    if not doc or doc.get("userId") != user_id:
        raise HTTPException(404, "evidence object not found")
    return doc


@router.post("/generate")
def generate(body: GenerateBody, user_id: str = Depends(get_user_id)):
    """Recompute evidence deterministically. With no filter, refreshes every
    strategy x market subject the user trades (bounded)."""
    if body.strategy_id and body.market:
        doc = engine.build_evidence(user_id, body.strategy_id, body.market,
                                    session=body.session)
        return {"generated": [doc], "count": 1}
    docs = engine.refresh_all(user_id)
    if body.strategy_id:
        docs = [d for d in docs if d["strategy_id"] == body.strategy_id]
    return {"generated": docs, "count": len(docs)}
