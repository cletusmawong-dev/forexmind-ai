"""RESEARCH SOURCE REGISTRY (owner brief 2026-10-08, section 2-3).

The research agent must NOT freely search the internet. Every source it may
use is registered here with an EVIDENCE TIER:

  Tier 1  STRONG          academic / institutional / peer-reviewed / verified data
  Tier 2  SUPPORTING      established quant & systematic research, documented
                          methodologies (incl. AlphaInsider for strategy
                          discovery - claims are NEVER accepted as proof)
  Tier 3  IDEA DISCOVERY  forums, Reddit, blogs, videos - ideas only, never
                          strong evidence

Core distinction enforced everywhere: SOURCE CLAIM != FOREXMIND VERIFIED
RESULT. A claim from any tier is only a reason to investigate; only this
system's own reconstruction + backtest + shadow evidence can promote it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# curated registry (code defaults; persisted copies live in research_sources)
# ---------------------------------------------------------------------------
REGISTRY: List[Dict[str, Any]] = [
    # ---- Tier 1: strong evidence ------------------------------------------
    {"id": "ssrn", "name": "SSRN", "url": "https://www.ssrn.com",
     "tier": 1, "kind": "academic", "use": "strategy_research",
     "note": "Working papers in finance/economics; peer-review varies - treat as strong with care."},
    {"id": "arxiv", "name": "arXiv q-fin", "url": "https://arxiv.org/list/q-fin",
     "tier": 1, "kind": "academic", "use": "strategy_research",
     "note": "Quantitative finance preprints (q-fin.*)."},
    {"id": "nber", "name": "NBER", "url": "https://www.nber.org",
     "tier": 1, "kind": "institutional", "use": "strategy_research"},
    {"id": "bis", "name": "BIS", "url": "https://www.bis.org",
     "tier": 1, "kind": "institutional", "use": "market_research"},
    {"id": "aqr", "name": "AQR", "url": "https://www.aqr.com/Insights/Research",
     "tier": 1, "kind": "institutional", "use": "strategy_research",
     "note": "Established systematic factor research."},
    {"id": "cme", "name": "CME Group Research", "url": "https://www.cmegroup.com/education.html",
     "tier": 1, "kind": "institutional", "use": "market_research"},
    {"id": "fed", "name": "Federal Reserve Research", "url": "https://www.federalreserve.gov/econres.htm",
     "tier": 1, "kind": "central_bank", "use": "market_research"},
    {"id": "ecb", "name": "ECB Research", "url": "https://www.ecb.europa.eu/pub/research/html/index.en.html",
     "tier": 1, "kind": "central_bank", "use": "market_research"},
    {"id": "boe", "name": "Bank of England Research", "url": "https://www.bankofengland.co.uk/research",
     "tier": 1, "kind": "central_bank", "use": "market_research"},
    {"id": "journals", "name": "Peer-reviewed finance journals (JF, JFE, RFS, JFM)",
     "url": "", "tier": 1, "kind": "academic", "use": "strategy_research"},
    # ---- Tier 2: supporting research ---------------------------------------
    {"id": "alphainsider", "name": "AlphaInsider",
     "url": "https://alphainsider.com", "tier": 2, "kind": "strategy_platform",
     "use": "strategy_discovery",
     "note": "Strategy discovery + methodology reference ONLY. Reported results are "
             "claims, never proof - everything found here is independently "
             "reconstructed and tested before it counts."},
    {"id": "quant_blogs_verified", "name": "Established quant research blogs (documented methodologies)",
     "url": "", "tier": 2, "kind": "quant_research", "use": "strategy_research"},
    # ---- Tier 3: idea discovery only ---------------------------------------
    {"id": "forums", "name": "Trading forums / public strategy discussions",
     "url": "", "tier": 3, "kind": "community", "use": "idea_discovery",
     "note": "Ideas only - never strong evidence by themselves."},
    {"id": "reddit", "name": "Reddit (r/algotrading, r/Daytrading, ...)",
     "url": "https://www.reddit.com", "tier": 3, "kind": "community", "use": "idea_discovery"},
    {"id": "blogs_videos", "name": "Blogs / YouTube / videos",
     "url": "", "tier": 3, "kind": "community", "use": "idea_discovery"},
]

TIER_NAMES = {1: "STRONG", 2: "SUPPORTING", 3: "IDEA_DISCOVERY"}

# domain hints for classifying URLs that are not in the registry verbatim
_DOMAIN_HINTS = [
    (("ssrn.com",), 1), (("arxiv.org",), 1), (("nber.org",), 1), (("bis.org",), 1),
    (("aqr.com",), 1), (("cmegroup.com",), 1), (("federalreserve.gov",), 1),
    (("ecb.europa.eu",), 1), (("bankofengland.co.uk",), 1),
    (("alphainsider.com",), 2),
    (("reddit.com",), 3), (("youtube.com",), 3), (("medium.com",), 3),
    (("forexfactory.com",), 3), (("tradingview.com",), 3),
]

COLLECTION = "research_sources"


def tier_name(tier: int) -> str:
    return TIER_NAMES.get(int(tier), "UNCLASSIFIED")


def classify(url: str, source_id: Optional[str] = None) -> Dict[str, Any]:
    """Classify a source URL/reference into an evidence tier.

    Registered ids win; unknown domains default to Tier 3 (idea discovery) -
    an unclassified source can NEVER be treated as strong evidence.
    """
    if source_id:
        for r in REGISTRY:
            if r["id"] == source_id:
                return {"tier": r["tier"], "tier_name": tier_name(r["tier"]),
                        "source_id": r["id"], "registered": True}
    host = (urlparse(url or "").hostname or "").lower()
    for hints, tier in _DOMAIN_HINTS:
        if any(host == h or host.endswith("." + h) for h in hints):
            sid = next((r["id"] for r in REGISTRY
                        if urlparse(r["url"]).hostname == host), host)
            return {"tier": tier, "tier_name": tier_name(tier),
                    "source_id": sid, "registered": True}
    return {"tier": 3, "tier_name": tier_name(3),
            "source_id": host or "unclassified", "registered": False}


def ensure_registry(store) -> int:
    """Persist any missing registry entries (idempotent, quota-friendly)."""
    n = 0
    for r in REGISTRY:
        if not store.get(COLLECTION, r["id"]):
            store.create(COLLECTION, {**r, "createdAt": datetime.now(
                timezone.utc).isoformat()}, doc_id=r["id"])
            n += 1
    return n


def list_sources(store) -> List[Dict[str, Any]]:
    ensure_registry(store)
    out = store.list(COLLECTION, limit=100)
    out.sort(key=lambda d: (d.get("tier") or 99, str(d.get("name") or "")))
    return out


def usable_for(tier: int, purpose: str) -> bool:
    """Purpose gates: strategy_research needs tier<=2; idea_discovery allows 3."""
    if purpose == "idea_discovery":
        return tier in (1, 2, 3)
    return int(tier) <= 2
