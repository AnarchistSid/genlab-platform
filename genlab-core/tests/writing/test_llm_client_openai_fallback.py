"""Pin tests for the Anthropic → OpenAI writer fallback (2026-07-21).

Fallback triggers on `credit balance is too low` / rate-limit errors,
preserves the `.complete() -> str` return contract, and includes a
circuit breaker that skips Anthropic entirely after 3 consecutive
exhaustion errors for 10 min.

If these pins regress, the 2026-07-18 → 2026-07-21 class-of-bug
(Anthropic exhaustion → writer stops → blueprint creation halts →
downstream cascade) becomes possible again.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import genlab_core.writing.llm_client as llm_client_module
from genlab_core.writing.llm_client import (
    AnthropicLLMClient,
    _is_exhaustion_error,
)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Clear module-level CB state between tests so ordering doesn't
    contaminate assertions."""
    llm_client_module._ANTHROPIC_EXHAUSTION_COUNT = 0
    llm_client_module._ANTHROPIC_CB_OPEN_UNTIL = 0.0
    yield
    llm_client_module._ANTHROPIC_EXHAUSTION_COUNT = 0
    llm_client_module._ANTHROPIC_CB_OPEN_UNTIL = 0.0


class TestExhaustionDetection:
    """The classifier that decides `is this fallback-worthy or auth-blocking`."""

    def test_credit_balance_too_low_triggers_fallback(self):
        exc = Exception("Error code: 400 - credit balance is too low")
        assert _is_exhaustion_error(exc) is True

    def test_insufficient_credits_triggers_fallback(self):
        exc = Exception("insufficient credits remaining")
        assert _is_exhaustion_error(exc) is True

    def test_ratelimit_class_triggers_fallback(self):
        # anthropic SDK raises RateLimitError on 429
        exc = type("RateLimitError", (Exception,), {})("rate limited")
        assert _is_exhaustion_error(exc) is True

    def test_apistatuserror_triggers_fallback(self):
        exc = type("APIStatusError", (Exception,), {})("some 5xx")
        assert _is_exhaustion_error(exc) is True

    def test_auth_error_does_NOT_trigger_fallback(self):
        # 401 unauthorized is an operator issue OpenAI can't help with
        exc = Exception("401 unauthorized: invalid API key")
        assert _is_exhaustion_error(exc) is False

    def test_generic_network_error_does_NOT_trigger_fallback(self):
        exc = ConnectionError("Connection refused")
        assert _is_exhaustion_error(exc) is False


class TestFallbackFiring:
    """End-to-end: when Anthropic raises exhaustion, OpenAI is called."""

    def _make_client(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        return AnthropicLLMClient(api_key="sk-anthropic", model="claude-haiku-4-5-20251001")

    def test_anthropic_success_no_fallback(self, monkeypatch):
        """Happy path — no OpenAI call when Anthropic works."""
        client = self._make_client(monkeypatch)
        client._client = MagicMock()
        client._client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        # Patch record_anthropic_usage since it's called on success
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage"
        ), patch("genlab_core.writing.llm_client._call_openai_fallback") as fb:
            result = client.complete(system="s", user="u")
        assert result == "ok"
        fb.assert_not_called()

    def test_anthropic_exhausted_falls_back_to_openai(self, monkeypatch):
        """Anthropic credit-exhaustion → OpenAI call → return preserved."""
        client = self._make_client(monkeypatch)
        client._client = MagicMock()
        client._client.messages.create.side_effect = Exception(
            "Error code: 400 - {'error': {'type': 'invalid_request_error', "
            "'message': 'Your credit balance is too low'}}"
        )
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback",
            return_value="fallback-content",
        ) as fb:
            result = client.complete(system="sys", user="usr", max_tokens=500)
        assert result == "fallback-content"
        fb.assert_called_once_with("sys", "usr", 500, 0.7, "sk-openai-test")

    def test_auth_error_re_raises_no_fallback(self, monkeypatch):
        """401 must NOT fallback — needs operator attention."""
        client = self._make_client(monkeypatch)
        client._client = MagicMock()
        auth_exc = Exception("401 unauthorized")
        client._client.messages.create.side_effect = auth_exc
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback"
        ) as fb, pytest.raises(Exception, match="401 unauthorized"):
            client.complete(system="s", user="u")
        fb.assert_not_called()

    def test_openai_also_fails_re_raises_original(self, monkeypatch):
        """When both fail, caller sees the ORIGINAL Anthropic error so
        error_classifier + observability behave the same as before."""
        client = self._make_client(monkeypatch)
        client._client = MagicMock()
        anthropic_exc = Exception("credit balance is too low")
        client._client.messages.create.side_effect = anthropic_exc
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback",
            side_effect=Exception("openai timeout"),
        ), pytest.raises(Exception, match="credit balance is too low"):
            client.complete(system="s", user="u")

    def test_no_openai_key_re_raises(self, monkeypatch):
        """No OPENAI_API_KEY → fall through — re-raise so operator sees it."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = AnthropicLLMClient(api_key="sk-a")
        client._client = MagicMock()
        client._client.messages.create.side_effect = Exception(
            "credit balance is too low"
        )
        with pytest.raises(Exception, match="credit balance is too low"):
            client.complete(system="s", user="u")

    def test_flag_disabled_re_raises_no_fallback(self, monkeypatch):
        """Env flag opt-out (`GENLAB_LLM_FALLBACK_ENABLED=0`) preserves
        legacy behavior — no fallback, re-raise. For when OpenAI budget
        is also dry."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("GENLAB_LLM_FALLBACK_ENABLED", "0")
        client = AnthropicLLMClient(api_key="sk-a")
        client._client = MagicMock()
        client._client.messages.create.side_effect = Exception(
            "credit balance is too low"
        )
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback"
        ) as fb, pytest.raises(Exception, match="credit balance is too low"):
            client.complete(system="s", user="u")
        fb.assert_not_called()


class TestCircuitBreaker:
    """After 3 consecutive Anthropic exhaustions, skip Anthropic
    entirely for 10 min. Reset on any success."""

    def _make_exhaust_client(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        c = AnthropicLLMClient(api_key="sk-a")
        c._client = MagicMock()
        c._client.messages.create.side_effect = Exception(
            "credit balance is too low"
        )
        return c

    def test_cb_opens_after_3_exhaustions(self, monkeypatch):
        client = self._make_exhaust_client(monkeypatch)
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback",
            return_value="fb",
        ):
            for _ in range(3):
                client.complete(system="s", user="u")
        assert llm_client_module._ANTHROPIC_EXHAUSTION_COUNT >= 3
        assert llm_client_module._ANTHROPIC_CB_OPEN_UNTIL > time.time()

    def test_cb_open_bypasses_anthropic(self, monkeypatch):
        """With CB open, Anthropic client is never called — straight to OpenAI."""
        client = self._make_exhaust_client(monkeypatch)
        # Manually open the breaker
        llm_client_module._ANTHROPIC_CB_OPEN_UNTIL = time.time() + 300
        with patch(
            "genlab_core.writing.llm_client._call_openai_fallback",
            return_value="fb",
        ) as fb:
            result = client.complete(system="s", user="u")
        assert result == "fb"
        # Anthropic client's messages.create MUST NOT have been called
        client._client.messages.create.assert_not_called()
        fb.assert_called_once()

    def test_cb_resets_on_anthropic_success(self, monkeypatch):
        """Any successful Anthropic call resets the CB fully — allows
        recovery once operator tops up credits."""
        # Start with breaker partly open
        llm_client_module._ANTHROPIC_EXHAUSTION_COUNT = 2
        client = AnthropicLLMClient(api_key="sk-a")
        client._client = MagicMock()
        client._client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="recovered")]
        )
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage"
        ):
            result = client.complete(system="s", user="u")
        assert result == "recovered"
        assert llm_client_module._ANTHROPIC_EXHAUSTION_COUNT == 0
        assert llm_client_module._ANTHROPIC_CB_OPEN_UNTIL == 0.0

    def test_cb_no_openai_key_still_tries_anthropic(self, monkeypatch):
        """If CB is open but OpenAI isn't configured, fall through to
        Anthropic anyway — better to try + fail loudly than silently
        return empty."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        llm_client_module._ANTHROPIC_CB_OPEN_UNTIL = time.time() + 300
        client = AnthropicLLMClient(api_key="sk-a")
        client._client = MagicMock()
        client._client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage"
        ):
            result = client.complete(system="s", user="u")
        assert result == "ok"
        client._client.messages.create.assert_called_once()
