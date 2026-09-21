"""AI ModelRouter (master prompt SS6-SS8): two-layer AI, modular, replaceable.

    AIManager
    |-- PrimaryAI      qwen (cheap/free)  - normal monitoring
    |-- EscalationAI   GPT-5.6 Sol        - deep analysis on triggers ONLY
    '-- ModelRouter    this module

Routing rules:
  * every analysis goes to the PRIMARY model first;
  * escalation runs only when the caller raises an event flag (`escalate`)
    or the primary is unavailable - never per tick (SS7);
  * escalation is rate-limited by AI_ESCALATION_COOLDOWN_S so a burst of
    events cannot burn the powerful model;
  * failures degrade DOWN, never sideways into invention:
    Sol unavailable -> primary result; primary unavailable -> Sol (when the
    event justifies it); both unavailable -> deterministic LocalAnalyst
    (SS44: AI failure never disables safety - it just stops AI opinions);
  * every result carries honest metadata (which model actually answered,
    what was tried, why fallbacks happened) for the dashboard + audit.

Model IDs come from env (AI_PRIMARY_MODEL / AI_ESCALATION_MODEL) - the
defaults (verified live on Xkiro 2026-09-17) are just defaults.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from ..config import settings
from .ai_provider import AIProvider, AIUnavailable, LocalAnalyst, XKiroProvider, get_ai_provider

_router: Optional["ModelRouter"] = None
_router_lock = threading.Lock()


class ModelRouter:
    """Routes one analysis request through primary -> (escalation) -> local."""

    def __init__(self, primary: Optional[AIProvider] = None,
                 escalation: Optional[AIProvider] = None,
                 local: Optional[AIProvider] = None):
        self._lock = threading.Lock()
        self._primary = primary
        self._escalation = escalation
        self._local = local or LocalAnalyst()
        self._esc_last_ts = 0.0
        # honest counters for /api/agent status (in-memory, resets on deploy)
        self.stats = {"primary_ok": 0, "primary_fail": 0,
                      "escalation_ok": 0, "escalation_fail": 0,
                      "escalation_skipped_cooldown": 0, "local_fallbacks": 0}

    # -- lazy providers (default wiring respects the existing key config) --
    @property
    def primary(self) -> AIProvider:
        if self._primary is None:
            self._primary = get_ai_provider()
        return self._primary

    @property
    def escalation(self) -> Optional[AIProvider]:
        if not settings.ai_escalation_enabled:
            return None
        if self._escalation is None:
            if settings.xiro_api_key:
                self._escalation = XKiroProvider()
            else:
                return None     # no key -> escalation honestly unavailable
        return self._escalation

    # ------------------------------------------------------------------
    def analyze(self, user_prompt: str, context: Dict[str, Any],
                escalate: bool = False,
                user_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns {text, model, layer, escalated, fallback, notes[]}.

        Never raises: an AI outage degrades to the deterministic analyst and
        is reported in the metadata (SS43/SS44)."""
        notes: list = []
        primary_text: Optional[str] = None
        primary_err: Optional[str] = None

        try:
            if isinstance(self.primary, XKiroProvider):
                # the router owns the primary model id (env-overridable);
                # plain XKIRO_MODEL stays the default for legacy call sites
                primary_text = self.primary.complete(
                    user_prompt, context, model=settings.ai_primary_model)
            else:
                primary_text = self.primary.complete(user_prompt, context)
            with self._lock:
                self.stats["primary_ok"] += 1
        except Exception as exc:
            primary_err = str(exc)[:200]
            with self._lock:
                self.stats["primary_fail"] += 1
            notes.append(f"primary unavailable: {primary_err}")

        esc = self.escalation
        esc_text: Optional[str] = None
        if esc is not None and (escalate or primary_err):
            now = time.time()
            with self._lock:
                wait = settings.ai_escalation_cooldown_s - (now - self._esc_last_ts)
            if wait > 0 and primary_text is not None:
                # cooldown only matters when the primary already answered
                with self._lock:
                    self.stats["escalation_skipped_cooldown"] += 1
                notes.append(f"escalation skipped - cooldown {int(wait)}s")
            else:
                try:
                    esc_text = esc.complete(
                        user_prompt, context, model=settings.ai_escalation_model,
                        timeout=settings.ai_escalation_timeout_s,
                        max_tokens=settings.ai_escalation_max_tokens)
                    with self._lock:
                        self.stats["escalation_ok"] += 1
                        self._esc_last_ts = time.time()
                except Exception as exc:
                    with self._lock:
                        self.stats["escalation_fail"] += 1
                    notes.append(f"escalation unavailable: {str(exc)[:200]}")
                    self._notify_escalation_down(user_id, str(exc)[:120])

        if esc_text is not None:
            return {"text": esc_text, "model": settings.ai_escalation_model,
                    "layer": "escalation", "escalated": True, "fallback": False,
                    "notes": notes}
        if primary_text is not None:
            return {"text": primary_text, "model": settings.ai_primary_model,
                    "layer": "primary", "escalated": False, "fallback": False,
                    "notes": notes}
        # both AI layers unavailable -> deterministic grounded analyst (SS44)
        with self._lock:
            self.stats["local_fallbacks"] += 1
        notes.append("both AI layers unavailable - deterministic analyst answered")
        return {"text": self._local.complete(user_prompt, context),
                "model": self._local.name, "layer": "local",
                "escalated": False, "fallback": True, "notes": notes}

    # ------------------------------------------------------------------
    def _notify_escalation_down(self, user_id: Optional[str], err: str) -> None:
        """SS8: notify when escalation is unavailable. Throttled by the same
        cooldown so a dead Sol endpoint cannot spam the user."""
        now = time.time()
        with self._lock:
            if now - self._esc_last_ts < settings.ai_escalation_cooldown_s:
                return
            self._esc_last_ts = now
        try:
            from ..notifications.service import notify
            notify(user_id, "AI_ESCALATION_DOWN",
                   "Escalation AI unavailable",
                   f"GPT-5.6 Sol call failed ({err}). Monitoring continues on "
                   f"{settings.ai_primary_model} + deterministic rules.")
        except Exception:
            pass

    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        esc = self.escalation
        return {
            "primary_model": settings.ai_primary_model,
            "escalation_model": settings.ai_escalation_model if esc else None,
            "escalation_enabled": settings.ai_escalation_enabled,
            "escalation_available": esc is not None,
            "escalation_cooldown_s": settings.ai_escalation_cooldown_s,
            "escalation_ready_in_s": max(
                0, int(settings.ai_escalation_cooldown_s -
                       (time.time() - self._esc_last_ts))),
            "stats": dict(self.stats),
        }


def get_router() -> ModelRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = ModelRouter()
        return _router
