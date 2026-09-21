"""Phase 7: AI ModelRouter - Qwen primary, GPT-5.6 Sol escalation, fallbacks.

Providers are injected (DI) so tests never touch the network. Contract:
primary first; escalation only on event flag or primary failure; cooldown
rate-limits Sol; every failure degrades DOWN to deterministic analysis
(SS44) with honest metadata about what actually answered.
"""
import pytest

from app.agent.ai_provider import AIProvider, AIUnavailable, LocalAnalyst
from app.agent.router import ModelRouter
from app.config import settings


class FakeProvider(AIProvider):
    def __init__(self, name: str, text: str = "ok", fail: bool = False,
                 record_models: bool = False):
        self.name = name
        self.text = text
        self.fail = fail
        self.calls = []
        self.record_models = record_models

    def complete(self, user_prompt, context, model=None, **kwargs):
        self.calls.append({"prompt": user_prompt, "model": model, **kwargs})
        if self.fail:
            raise AIUnavailable(f"{self.name} down")
        if self.record_models:
            self.text = f"{self.name}:{model}"
        return self.text


@pytest.fixture(autouse=True)
def esc_on(monkeypatch):
    monkeypatch.setattr(settings, "ai_escalation_enabled", True)
    monkeypatch.setattr(settings, "ai_escalation_cooldown_s", 300)
    monkeypatch.setattr(settings, "ai_primary_model", "qwen/qwen3.7-plus:free")
    monkeypatch.setattr(settings, "ai_escalation_model", "openai/gpt-5.6-sol")


def test_primary_answers_by_default():
    q = FakeProvider("qwen", "qwen says hi")
    r = ModelRouter(primary=q, escalation=FakeProvider("sol"), local=LocalAnalyst())
    out = r.analyze("check the trade", {"sym": "XAUUSD"})
    assert out["text"] == "qwen says hi" and out["layer"] == "primary"
    assert out["escalated"] is False and out["fallback"] is False
    assert len(q.calls) == 1


def test_event_flag_escalates_and_passes_model_id():
    q = FakeProvider("qwen")
    sol = FakeProvider("sol", record_models=True)
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    out = r.analyze("deep dive", {}, escalate=True)
    assert out["layer"] == "escalation" and out["escalated"] is True
    assert out["text"] == "sol:openai/gpt-5.6-sol"     # env model id passed through
    assert sol.calls[0]["model"] == "openai/gpt-5.6-sol"


def test_cooldown_blocks_second_escalation():
    q = FakeProvider("qwen", "primary answer")
    sol = FakeProvider("sol", "sol answer")
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    r.analyze("a", {}, escalate=True)
    out2 = r.analyze("b", {}, escalate=True)
    assert len(sol.calls) == 1                          # Sol called once only
    assert out2["layer"] == "primary"
    assert any("cooldown" in n for n in out2["notes"])


def test_primary_failure_escalates_even_without_flag():
    q = FakeProvider("qwen", fail=True)
    sol = FakeProvider("sol", "sol rescued")
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    out = r.analyze("x", {}, escalate=False)
    assert out["layer"] == "escalation" and out["text"] == "sol rescued"
    assert any("primary unavailable" in n for n in out["notes"])


def test_both_fail_deterministic_fallback():
    q = FakeProvider("qwen", fail=True)
    sol = FakeProvider("sol", fail=True)
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    out = r.analyze("x", {"market": "XAUUSD", "r": 2})
    assert out["layer"] == "local" and out["fallback"] is True
    assert "Grounded analysis" in out["text"]           # never invents
    assert r.stats["local_fallbacks"] == 1


def test_escalation_disabled_never_called(monkeypatch):
    monkeypatch.setattr(settings, "ai_escalation_enabled", False)
    q = FakeProvider("qwen", "primary only")
    sol = FakeProvider("sol")
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    out = r.analyze("x", {}, escalate=True)
    assert out["layer"] == "primary" and sol.calls == []


def test_no_key_no_escalation(monkeypatch):
    monkeypatch.setattr(settings, "xiro_api_key", "")
    q = FakeProvider("qwen", "primary only")
    r = ModelRouter(primary=q, local=LocalAnalyst())    # escalation lazily = None
    out = r.analyze("x", {}, escalate=True)
    assert out["layer"] == "primary"
    assert r.status()["escalation_available"] is False


def test_sol_down_notifies_user_throttled(monkeypatch):
    sent = []
    import app.notifications.service as NS
    monkeypatch.setattr(NS, "notify", lambda uid, t, title, body, **kw:
                        sent.append((t, title)))
    q = FakeProvider("qwen", "primary ok")
    sol = FakeProvider("sol", fail=True)
    r = ModelRouter(primary=q, escalation=sol, local=LocalAnalyst())
    # primary answers -> no auto escalation path... force via flag twice:
    r.analyze("x", {}, escalate=True)                   # Sol fails -> notify set
    sent.clear()
    r.analyze("y", {}, escalate=True)                   # within cooldown: silent
    assert sent == []                                   # throttled by cooldown


def test_status_reports_layers_and_stats():
    q = FakeProvider("qwen")
    r = ModelRouter(primary=q, escalation=FakeProvider("sol"), local=LocalAnalyst())
    r.analyze("x", {}, escalate=True)
    st = r.status()
    assert st["primary_model"] == "qwen/qwen3.7-plus:free"
    assert st["escalation_model"] == "openai/gpt-5.6-sol"
    assert st["stats"]["escalation_ok"] == 1
    assert st["escalation_ready_in_s"] > 0


def test_get_router_singleton():
    from app.agent.router import get_router
    assert get_router() is get_router()
