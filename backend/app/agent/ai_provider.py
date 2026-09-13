"""AI provider layer (SPEC §2, §13, §37, §57).

AIProvider interface with two implementations:

* XKiroProvider  - activated server-side when XKIRO_API_KEY is configured.
                   The key NEVER reaches the client.
* LocalAnalyst   - deterministic, fully grounded on stored/computed data.
                   Used until XKiro credentials are provided. It NEVER
                   fabricates analysis: if data is missing it says so.

Prompt rules from SPEC §57 are enforced in the system prompt for XKiro and
mirrored by construction in the local analyst.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import requests

from ..config import settings

SYSTEM_PROMPT = """You are ForexMind AI, a trading research and signal agent.
- You do not execute trades.
- You analyze only the strategies provided by the system.
- You do not invent strategies.
- You do not modify live strategy parameters.
- You may propose hypotheses.
- You must change only ONE variable in an experiment.
- You must provide evidence.
- You must distinguish observations from conclusions.
- You must not fabricate data.
- You must not fabricate backtests.
- You must not guarantee profits.
- The user's daily objective is an objective, not a command to trade.
- You must prioritize valid strategy conditions over reaching the objective.
- Every proposed strategy change requires user approval.
- Every approved change creates a new version.
- All previous versions must remain available.
Answer concisely using ONLY the data provided in the context."""


class AIUnavailable(Exception):
    pass


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def complete(self, user_prompt: str, context: Dict[str, Any]) -> str:
        """Returns a concise, grounded analysis string."""


class XKiroProvider(AIProvider):
    name = "xkiro"

    def complete(self, user_prompt: str, context: Dict[str, Any]) -> str:
        key = settings.xiro_api_key
        if not key:
            raise AIUnavailable("XKiro key not configured")
        try:
            resp = requests.post(
                f"{settings.xiro_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": settings.xiro_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",
                         "content": user_prompt +
                                    "\n\nVERIFIED CONTEXT (use only this data):\n" +
                                    _format_context(context)},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise AIUnavailable(str(e)) from e


class LocalAnalyst(AIProvider):
    """Deterministic grounded analyst. Renders ONLY supplied context data."""

    name = "local_analyst"

    def complete(self, user_prompt: str, context: Dict[str, Any]) -> str:
        lines = []
        for k, v in context.items():
            if isinstance(v, dict):
                inner = ", ".join(f"{ik}: {iv}" for ik, iv in list(v.items())[:8])
                lines.append(f"{k}: {inner}")
            elif isinstance(v, list):
                lines.append(f"{k}: " + "; ".join(str(i) for i in v[:8]))
            else:
                lines.append(f"{k}: {v}")
        return (f"[Grounded analysis - no external AI configured]\n"
                + "\n".join(lines))


def _format_context(context: Dict[str, Any]) -> str:
    out = []
    for k, v in context.items():
        out.append(f"{k.upper()}:")
        if isinstance(v, dict):
            for ik, iv in v.items():
                out.append(f"  {ik}: {iv}")
        else:
            out.append(f"  {v}")
    return "\n".join(out)


_provider: Optional[AIProvider] = None


def get_ai_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.xiro_api_key:
            _provider = XKiroProvider()
        else:
            _provider = LocalAnalyst()
    return _provider


def ai_status() -> Dict[str, Any]:
    p = get_ai_provider()
    return {
        "provider": p.name,
        "configured": p.name == "xkiro",
        "note": ("XKiro connected (server-side key)." if p.name == "xkiro" else
                 "Using the built-in grounded analyst. Add XKIRO_API_KEY to the "
                 "backend environment to enable XKiro. Key stays server-side."),
    }
