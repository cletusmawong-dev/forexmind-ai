"""GPT-5.6-Sol escalation fix (2026-09-21).

Production notifications showed EVERY escalation failure was
'read timeout=20': the reasoning model needs far longer than the fast
primary. The router now gives the escalation layer its own timeout and
token budget (env-tunable), leaving the fast primary untouched.
"""
import pytest

from app.agent import ai_provider as AIP
from app.agent.ai_provider import AIProvider, AIUnavailable, LocalAnalyst, XKiroProvider
from app.agent.router import ModelRouter
from app.config import settings


class FakeProvider(AIProvider):
    def __init__(self, name, text="ok", fail=False):
        self.name = name
        self.text = text
        self.fail = fail
        self.calls = []

    def complete(self, user_prompt, context, model=None, **kwargs):
        self.calls.append({"prompt": user_prompt, "model": model, **kwargs})
        if self.fail:
            raise AIUnavailable(f"{self.name} down")
        return self.text


@pytest.fixture(autouse=True)
def esc_env(monkeypatch):
    monkeypatch.setattr(settings, "ai_escalation_enabled", True)
    monkeypatch.setattr(settings, "ai_escalation_cooldown_s", 300)
    monkeypatch.setattr(settings, "ai_escalation_timeout_s", 45)
    monkeypatch.setattr(settings, "ai_escalation_max_tokens", 900)


def test_escalation_gets_long_timeout_and_token_budget():
    """Primary down -> Sol is called with 45s / 900 tokens, not the 20s/400 default."""
    sol = FakeProvider("sol", "sol decision")
    r = ModelRouter(primary=FakeProvider("qwen", fail=True), escalation=sol, local=LocalAnalyst())
    out = r.analyze("manage trade", {"sym": "XAUUSD"})
    assert out["layer"] == "escalation" and out["text"] == "sol decision"
    assert sol.calls[0]["timeout"] == 45
    assert sol.calls[0]["max_tokens"] == 900
    assert sol.calls[0]["model"] == "openai/gpt-5.6-sol"


def test_primary_keeps_fast_defaults():
    """The primary path must NOT inherit the slow escalation settings."""
    qwen = FakeProvider("qwen", "fast")
    sol = FakeProvider("sol")
    r = ModelRouter(primary=qwen, escalation=sol, local=LocalAnalyst())
    out = r.analyze("manage trade", {})
    assert out["layer"] == "primary"
    assert "timeout" not in qwen.calls[0] and "max_tokens" not in qwen.calls[0]
    assert sol.calls == []                       # no escalation on healthy primary


def test_config_defaults_reasonable():
    assert settings.ai_escalation_timeout_s >= 30
    assert settings.ai_escalation_max_tokens >= 400


def test_provider_forwards_timeout_and_max_tokens(monkeypatch):
    """XKiroProvider passes the knobs into requests.post."""
    monkeypatch.setattr(settings, "xiro_api_key", "test-key")
    sent = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": '{"decision": "hold"}'}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update({"url": url, "json": json, "timeout": timeout})
        return FakeResp()

    monkeypatch.setattr(AIP.requests, "post", fake_post)
    out = XKiroProvider().complete("prompt", {}, timeout=45, max_tokens=900)
    assert sent["timeout"] == 45
    assert sent["json"]["max_tokens"] == 900
    assert '"decision"' in out


def test_provider_default_contract_unchanged(monkeypatch):
    """Legacy call sites (no kwargs) keep the old 20s/400 behavior."""
    monkeypatch.setattr(settings, "xiro_api_key", "test-key")
    sent = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(AIP.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        sent.update({"json": json, "timeout": timeout}) or FakeResp())
    XKiroProvider().complete("prompt", {})
    assert sent["timeout"] == 20 and sent["json"]["max_tokens"] == 400
