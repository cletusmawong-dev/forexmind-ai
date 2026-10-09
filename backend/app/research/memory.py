"""RESEARCH MEMORY (owner brief 2026-10-08, section 12).

The Research Engine must not repeat itself: before running anything it
checks what was already researched (sources, strategies, parameters,
markets, sessions), what failed and WHY. Substantially identical experiments
are skipped with a pointer to the earlier verdict.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

COLLECTION = "research_memory"


PIPELINE_VERSION = "v3-channel-fix"   # bumped when research capability changes


def _fingerprint(source_id: str, name: str, market: str, timeframe: str,
                 entry_sig: str, param_sig: str) -> str:
    """Identical experiment under the SAME pipeline version only.

    When the research capability itself improves (deeper data, new gates),
    earlier verdicts do not block re-research - they stay in memory under
    their own version."""
    import hashlib
    h = hashlib.sha1()
    for part in (PIPELINE_VERSION, source_id.lower(), name.lower(), market.upper(),
                 timeframe.upper(), entry_sig, param_sig):
        h.update((part or "").encode())
    return h.hexdigest()[:16]


def entry_signature(implemented: Dict[str, Any]) -> str:
    """A canonical string of the entry program (order-independent enough)."""
    def _c(c: dict) -> str:
        l = c.get("left") or {}
        r = c.get("right") or {}
        return (f"{l.get('indicator')}:{sorted((l.get('params') or {}).items())}|"
                f"{c.get('op')}|{r.get('indicator')}:{sorted((r.get('params') or {}).items())}"
                f":{r.get('value')}")
    conds = sorted(_c(c) for c in (implemented.get("entry") or {}).get("when", []))
    sl = implemented.get("sl") or {}
    return "|".join(conds) + f"#sl:{sl.get('type')}:{sl.get('pips') or sl.get('mult')}"


def param_signature(implemented: Dict[str, Any]) -> str:
    parts = []
    for c in (implemented.get("entry") or {}).get("when", []):
        for side in ("left", "right"):
            p = (c.get(side) or {}).get("params") or {}
            parts.extend(f"{k}={v}" for k, v in sorted(p.items()))
    return ",".join(sorted(parts))


def remember_candidate(store, candidate: Dict[str, Any], implemented: Dict[str, Any],
                       verdict: str, reason: str, experiment_id: Optional[str] = None,
                       report_id: Optional[str] = None) -> str:
    fp = _fingerprint(str(candidate.get("source_id")),
                      str(candidate.get("name")),
                      str(candidate.get("market")),
                      str(candidate.get("timeframe")),
                      entry_signature(implemented),
                      param_signature(implemented))
    doc = {
        "fingerprint": fp,
        "pipeline_version": PIPELINE_VERSION,
        "candidate_id": candidate.get("id") or candidate.get("candidate_id"),
        "name": candidate.get("name"),
        "source_id": candidate.get("source_id"),
        "market": candidate.get("market"), "timeframe": candidate.get("timeframe"),
        "verdict": verdict,                       # REJECT | SHADOW | READY_FOR_REVIEW | ...
        "reason": reason,
        "experiment_id": experiment_id, "report_id": report_id,
        "params_tested": param_signature(implemented),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    existing = store.list(COLLECTION, filters={"fingerprint": fp}, limit=1)
    if existing:
        store.update(COLLECTION, existing[0]["id"], doc)
        return fp
    store.create(COLLECTION, doc)
    return fp


def seen_before(store, candidate: Dict[str, Any], implemented: Dict[str, Any]
                ) -> Optional[Dict[str, Any]]:
    """The earlier memory row for a substantially identical experiment, if any."""
    fp = _fingerprint(str(candidate.get("source_id")),
                      str(candidate.get("name")),
                      str(candidate.get("market")),
                      str(candidate.get("timeframe")),
                      entry_signature(implemented),
                      param_signature(implemented))
    rows = store.list(COLLECTION, filters={"fingerprint": fp}, limit=1)
    return rows[0] if rows else None    # fp already includes the version


def summary(store, limit: int = 50) -> Dict[str, Any]:
    rows = store.list(COLLECTION, limit=limit)
    rows.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
    rejected = [r for r in rows if r.get("verdict") == "REJECT"]
    return {
        "total": len(rows),
        "rejected": len(rejected),
        "recent": [{"name": r.get("name"), "market": r.get("market"),
                    "verdict": r.get("verdict"), "reason": r.get("reason"),
                    "at": r.get("createdAt")} for r in rows[:10]],
    }
