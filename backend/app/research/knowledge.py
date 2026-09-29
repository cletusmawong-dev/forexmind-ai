"""KNOWLEDGE GRAPH + STRUCTURED QUERIES (3.0 spec section 44).

A structured relationship layer built FROM REAL DATA (matrix cells + evidence
states): Strategy <-> Instrument <-> Session <-> Regime <-> Outcome. Makes
"How does Strategy 2 perform on EURUSD during London in trending conditions?"
answerable with a database query - no LLM memory required.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def graph(user_id: str) -> dict:
    from ..db.store import get_store
    store = get_store()
    from ..learning.matrix import build_matrix
    from ..evidence import engine
    mx = build_matrix(user_id)
    ev_states = {d["subject_id"]: d["state"] for d in engine.list_evidence(user_id)}

    nodes: Dict[str, dict] = {}
    edges: List[dict] = []

    def node(nid: str, kind: str, label: str, extra: Optional[dict] = None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "kind": kind, "label": label, **(extra or {})}

    for sid, st in (mx.get("strategies") or {}).items():
        node(sid, "strategy", st.get("name") or sid, {"n": st.get("n"),
                                                      "win_rate": st.get("win_rate")})
    for c in mx.get("cells", []):
        mkt, ses, reg = c.get("market"), c.get("session"), c.get("regime")
        if mkt:
            node(f"market:{mkt}", "market", mkt)
        if ses:
            node(f"session:{ses}", "session", ses)
        if reg:
            node(f"regime:{reg}", "regime", reg)
        subject = f"{c['strategy_id']}:{mkt}"
        edges.append({"from": c["strategy_id"], "to": f"market:{mkt}", "kind": "trades",
                      "n": c.get("n"), "win_rate": c.get("win_rate"),
                      "avg_r": c.get("avg_r"), "session": ses, "regime": reg,
                      "evidence_state": ev_states.get(subject)})
        if ses:
            edges.append({"from": f"market:{mkt}", "to": f"session:{ses}",
                          "kind": "active_in", "n": c.get("n")})
        if reg:
            edges.append({"from": f"market:{mkt}", "to": f"regime:{reg}",
                          "kind": "classified_as", "n": c.get("n")})
    return {"nodes": list(nodes.values()), "edges": edges,
            "basis": "learning matrix + evidence states - real completed signals only"}


def query(user_id: str, strategy_id: Optional[str] = None,
          market: Optional[str] = None, session: Optional[str] = None,
          regime: Optional[str] = None) -> dict:
    from ..learning.matrix import build_matrix
    from ..evidence import engine
    mx = build_matrix(user_id)
    cells = [c for c in mx.get("cells", [])
             if (not strategy_id or c["strategy_id"] == strategy_id)
             and (not market or c.get("market") == market)
             and (not session or c.get("session") == session)
             and (not regime or c.get("regime") == regime)]
    ev_states = {d["subject_id"]: d["state"] for d in engine.list_evidence(user_id)}
    for c in cells:
        c["evidence_state"] = ev_states.get(f"{c['strategy_id']}:{c.get('market')}")
    agg_n = sum(c.get("n") or 0 for c in cells)
    wins = sum((c.get("win_rate") or 0) * (c.get("n") or 0) / 100.0 for c in cells)
    return {"filters": {"strategy_id": strategy_id, "market": market,
                        "session": session, "regime": regime},
            "cells": cells, "n_total": agg_n,
            "win_rate_blended": round(100 * wins / agg_n, 1) if agg_n else None,
            "answer_basis": "structured matrix cells - actual completed signals"}
