"""Pins the fallback ORDER: belt (inference.sh Haiku) before OpenAI.

Regression guard for 2026-09-17. The belt tier was defined on 2026-09-12 and had
zero consumers -- every call site imported `call_openai_fallback` and went
straight to OpenAI. When OpenAI's balance hit zero the auto-approver raised
RateLimitError on every borderline blueprint, approved nothing for days, so no
blueprint got a `scheduled_for` and all five channels stopped publishing.

These tests fail if anyone re-routes `call_openai_fallback` to OpenAI directly.
"""

import pytest
from genlab_core.llm import fallback as fb


@pytest.fixture
def calls(monkeypatch):
    seen = {"belt": 0, "openai": 0}

    def fake_belt(system, user, max_tokens, temperature, **kw):
        seen["belt"] += 1
        return "belt-said-this"

    def fake_openai(system, user, max_tokens, temperature, api_key, **kw):
        seen["openai"] += 1
        return "openai-said-this"

    monkeypatch.setattr(fb, "call_belt_haiku_fallback", fake_belt)
    monkeypatch.setattr(fb, "_call_openai_direct", fake_openai)
    return seen


def _call(**kw):
    return fb.call_openai_fallback("sys", "usr", 80, 0.0, "sk-test", **kw)


def test_belt_is_tried_first_and_openai_is_not_touched(calls, monkeypatch):
    monkeypatch.delenv("GENLAB_LLM_FALLBACK_BELT", raising=False)
    assert _call() == "belt-said-this"
    assert calls == {"belt": 1, "openai": 0}


def test_openai_is_reached_when_belt_raises(calls, monkeypatch):
    monkeypatch.delenv("GENLAB_LLM_FALLBACK_BELT", raising=False)
    monkeypatch.setattr(
        fb,
        "call_belt_haiku_fallback",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("belt down")),
    )
    assert _call() == "openai-said-this"
    assert calls["openai"] == 1


def test_belt_can_be_disabled_by_env(calls, monkeypatch):
    monkeypatch.setenv("GENLAB_LLM_FALLBACK_BELT", "0")
    assert _call() == "openai-said-this"
    assert calls == {"belt": 0, "openai": 1}


def test_json_mode_is_forwarded_to_belt(monkeypatch):
    got = {}

    def fake_belt(system, user, max_tokens, temperature, **kw):
        got.update(kw)
        return "{}"

    monkeypatch.delenv("GENLAB_LLM_FALLBACK_BELT", raising=False)
    monkeypatch.setattr(fb, "call_belt_haiku_fallback", fake_belt)
    _call(json_mode=True)
    assert got.get("json_mode") is True, "json sites would get unparseable prose"


def test_no_key_and_no_belt_raises_rather_than_returning_empty(monkeypatch):
    """Silent empty strings are how this class of bug hides (rule #19)."""
    monkeypatch.setenv("GENLAB_LLM_FALLBACK_BELT", "0")
    with pytest.raises(RuntimeError, match="exhausted"):
        fb.call_openai_fallback("sys", "usr", 80, 0.0, "")


class TestEmptyUserPrompt:
    """Anthropic requires a message; OpenAI does not.

    hook_classifier.py:187 and :218 put the whole prompt in `system` and pass
    user="". Against belt that produced
    `400 invalid_request_error: messages: at least one message is required`
    on every call, so those two sites silently had no belt tier at all.
    """

    def _payload(self, monkeypatch):
        seen = {}

        class _Proc:
            returncode = 0
            stdout = '{"status_text":"completed","output":{"response":"ok"}}'
            stderr = ""

        def fake_run(cmd, *a, **k):
            seen["cmd"] = cmd
            return _Proc()

        # subprocess is lazily imported inside the function, so it is never an
        # attribute of the fallback module -- patch the SOURCE module (CLAUDE.md
        # "Test patterns for lazy-imported dependencies").
        monkeypatch.setattr("subprocess.run", fake_run)
        return seen

    def _sent(self, seen):
        import json

        return json.loads(seen["cmd"][seen["cmd"].index("--input") + 1])

    def test_empty_user_promotes_system_into_the_message(self, monkeypatch):
        seen = self._payload(monkeypatch)
        fb.call_belt_haiku_fallback("CLASSIFY THIS HOOK", "", 8, 0.0)
        sent = self._sent(seen)
        assert sent["text"] == "CLASSIFY THIS HOOK", "empty message -> Anthropic 400"
        assert not sent.get("system_prompt"), "prompt must not be sent twice"

    def test_normal_split_prompt_is_untouched(self, monkeypatch):
        seen = self._payload(monkeypatch)
        fb.call_belt_haiku_fallback("SYS", "USR", 8, 0.0)
        sent = self._sent(seen)
        assert sent["text"] == "USR" and sent["system_prompt"] == "SYS"

    def test_both_empty_raises_rather_than_calling_belt(self, monkeypatch):
        self._payload(monkeypatch)
        with pytest.raises(ValueError, match="both empty"):
            fb.call_belt_haiku_fallback("", "   ", 8, 0.0)
