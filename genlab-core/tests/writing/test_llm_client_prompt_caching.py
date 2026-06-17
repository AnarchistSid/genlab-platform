"""Pin the U-01 Anthropic prompt-caching behaviour in AnthropicLLMClient.

Why
---
SYSTEM-RESEARCH § 8 U-01: hot LLM call sites have static system prompts
that Anthropic prompt caching can serve at ~10% of input cost. The 5-
niche writing pipeline runs ~30 candidates per niche with identical
per-niche system prompts — calls 2-N within a niche become cache
reads.

The implementation gates on a CHAR-LENGTH threshold (≥4000 chars =
~1000 tokens with 4 chars/token English heuristic) because caching
below ~1024 tokens is net-negative: cache writes cost 1.25× the input
rate, so a never-reused 500-token system prompt would actually pay
MORE with caching enabled.

These pins:
- Above threshold: system passed as list-of-dict with cache_control
- Below threshold: system passed as plain string (no caching)
- ``cache_control`` shape matches Anthropic's API spec
- record_anthropic_usage still called regardless of cache mode
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.writing.llm_client import AnthropicLLMClient


def _make_client_with_mock_response():
    client = AnthropicLLMClient(api_key="dummy")
    # Bypass _ensure_client by injecting the mock directly
    mock_sdk = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="generated text")]
    mock_response.usage = MagicMock()
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 50
    mock_sdk.messages.create.return_value = mock_response
    client._client = mock_sdk
    return client, mock_sdk


class TestPromptCacheGating:
    def test_long_system_prompt_enables_caching(self):
        """≥4000 chars → system sent as list-of-dict with cache_control."""
        client, sdk = _make_client_with_mock_response()
        long_system = "X" * 5000  # well above threshold
        client.complete(system=long_system, user="u", max_tokens=10, temperature=0.5)

        call = sdk.messages.create.call_args
        assert isinstance(call.kwargs["system"], list), (
            "long system prompt should be sent as list to enable caching"
        )
        assert len(call.kwargs["system"]) == 1
        block = call.kwargs["system"][0]
        assert block["type"] == "text"
        assert block["text"] == long_system
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_short_system_prompt_no_caching(self):
        """<4000 chars → system sent as plain string (caching costs more
        than it saves below ~1024 tokens)."""
        client, sdk = _make_client_with_mock_response()
        short_system = "You are a writer."  # ~17 chars
        client.complete(system=short_system, user="u", max_tokens=10, temperature=0.5)

        call = sdk.messages.create.call_args
        assert isinstance(call.kwargs["system"], str), (
            f"short system prompt should be a plain string, got {type(call.kwargs['system'])}"
        )
        assert call.kwargs["system"] == short_system

    def test_threshold_exactly_at_boundary(self):
        """Pin the comparison direction: ≥ threshold uses caching;
        < threshold doesn't. Boundary value test guards against
        accidental flip to strict inequality."""
        client, sdk = _make_client_with_mock_response()
        threshold = AnthropicLLMClient._CACHE_THRESHOLD_CHARS

        # At exactly threshold: enables caching
        client.complete(system="X" * threshold, user="u", max_tokens=10, temperature=0.5)
        assert isinstance(sdk.messages.create.call_args.kwargs["system"], list)

        sdk.reset_mock()
        # One char below: skips caching
        client.complete(system="X" * (threshold - 1), user="u", max_tokens=10, temperature=0.5)
        assert isinstance(sdk.messages.create.call_args.kwargs["system"], str)

    def test_other_args_unchanged_by_cache_gate(self):
        """The caching gate only mutates ``system``; model, max_tokens,
        temperature, messages must pass through unchanged."""
        client, sdk = _make_client_with_mock_response()
        client.complete(system="X" * 5000, user="my user", max_tokens=512, temperature=0.9)

        call = sdk.messages.create.call_args
        assert call.kwargs["max_tokens"] == 512
        assert call.kwargs["temperature"] == 0.9
        assert call.kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_cost_recording_works_in_both_modes(self, monkeypatch):
        """U-03 contract: record_anthropic_usage is called whether or
        not caching is enabled. Cache-hit input_tokens are excluded by
        the API itself (the response.usage.input_tokens count already
        omits cached tokens), so the existing record call captures the
        cost-after-caching automatically."""
        recorded = []

        def _fake_record(model, response):
            recorded.append((model, response.usage.input_tokens))

        monkeypatch.setattr(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage",
            _fake_record,
        )

        client, _sdk = _make_client_with_mock_response()
        # Short prompt (no caching)
        client.complete(system="short", user="u")
        # Long prompt (caching enabled)
        client.complete(system="X" * 5000, user="u")

        # Both calls should have recorded
        assert len(recorded) == 2


class TestNoRegressionOnEmptyOrNoneInputs:
    def test_empty_system_falls_through_no_crash(self):
        client, sdk = _make_client_with_mock_response()
        client.complete(system="", user="u")
        # Empty system → 0 chars < 4000 → plain string path
        assert sdk.messages.create.call_args.kwargs["system"] == ""

    def test_unicode_chars_count_as_chars(self):
        """The threshold is char-based, not byte-based — a Unicode-heavy
        system prompt at 4000 chars still hits the threshold even though
        it's more bytes."""
        client, sdk = _make_client_with_mock_response()
        unicode_system = "🎬" * 4000  # 4000 chars, ~16000 bytes
        client.complete(system=unicode_system, user="u")
        assert isinstance(sdk.messages.create.call_args.kwargs["system"], list)
