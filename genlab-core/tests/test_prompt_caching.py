"""Pin the U-01 Anthropic prompt-caching contract at the central helper + wired call sites.

The helper ``genlab_core.llm.prompt_cache.with_prompt_cache`` is the
single point where caching is gated (by char-length threshold +
kill-switch env). These pins lock the helper's behaviour AND verify
each wired call site routes its system prompt through the helper.

If a call site stops routing through the helper, the corresponding
``test_*_uses_prompt_cache_helper`` pin fails — surfaces the
regression before the cost dashboard shows the drop in cache reads.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.llm.prompt_cache import (
    DEFAULT_MIN_CACHE_CHARS,
    ENV_DISABLE,
    ENV_MIN_CHARS,
    build_cache_block,
    extract_cache_usage,
    with_prompt_cache,
)

# ─────────────────────────────────────────────────────────────────────
# Section 1 — helper unit tests
# ─────────────────────────────────────────────────────────────────────


class TestHelperBuildsBlock:
    def test_returns_structured_system_block_above_threshold(self):
        """≥4000-char prompt returns list-of-dict with cache_control."""
        long_prompt = "X" * 5000
        result = with_prompt_cache(long_prompt)
        assert isinstance(result, list), "above threshold should return list"
        assert len(result) == 1
        block = result[0]
        assert block["type"] == "text"
        assert block["text"] == long_prompt
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_returns_plain_string_below_threshold(self):
        """<4000-char prompt returns the original string (no list wrapping).

        Caching short prompts is net-negative — cache writes cost 1.25×
        the input rate. The helper must NOT wrap them.
        """
        short_prompt = "You are a writer."
        result = with_prompt_cache(short_prompt)
        assert isinstance(result, str)
        assert result == short_prompt

    def test_threshold_exactly_at_boundary(self):
        """Pin the comparison direction: ≥ threshold caches, < threshold doesn't.

        Boundary test guards against accidental flip to strict inequality.
        """
        # At the threshold: caching enabled
        at_threshold = with_prompt_cache("X" * DEFAULT_MIN_CACHE_CHARS)
        assert isinstance(at_threshold, list)

        # One char below: skipped
        below = with_prompt_cache("X" * (DEFAULT_MIN_CACHE_CHARS - 1))
        assert isinstance(below, str)

    def test_build_cache_block_shape(self):
        block = build_cache_block("some prompt")
        assert block == {
            "type": "text",
            "text": "some prompt",
            "cache_control": {"type": "ephemeral"},
        }

    def test_none_system_returns_empty_string(self):
        """None must NOT raise; treated as empty system prompt."""
        assert with_prompt_cache(None) == ""

    def test_empty_system_returns_empty_string(self):
        result = with_prompt_cache("")
        assert isinstance(result, str)
        assert result == ""

    def test_unicode_chars_count_as_chars_not_bytes(self):
        """Threshold is char-based; an emoji-heavy 4000-char prompt
        hits the cache threshold even though it's many more bytes."""
        unicode_prompt = "🎬" * DEFAULT_MIN_CACHE_CHARS
        result = with_prompt_cache(unicode_prompt)
        assert isinstance(result, list)


class TestHelperKillSwitch:
    def test_disabled_env_falls_back_to_plain_string(self, monkeypatch):
        """GENLAB_PROMPT_CACHE_DISABLED=1 disables caching globally."""
        monkeypatch.setenv(ENV_DISABLE, "1")
        long_prompt = "X" * 5000
        result = with_prompt_cache(long_prompt)
        assert isinstance(result, str), "kill-switch must force plain-string"
        assert result == long_prompt

    def test_disabled_env_only_accepts_exact_one(self, monkeypatch):
        """Matches the genlab_core opt-in pattern: ``"true"`` / ``"yes"``
        are NOT accepted — only the literal ``"1"`` flips the switch.
        Avoids accidental activation from copy-pasted env values."""
        for non_one in ("0", "true", "yes", "TRUE", ""):
            monkeypatch.setenv(ENV_DISABLE, non_one)
            result = with_prompt_cache("X" * 5000)
            assert isinstance(result, list), f"value {non_one!r} should NOT disable caching"

    def test_min_chars_env_override(self, monkeypatch):
        """Operators can lower the threshold via env once Anthropic
        ships shorter-prefix support."""
        monkeypatch.setenv(ENV_MIN_CHARS, "100")
        # 500 chars now caches because override is 100
        result = with_prompt_cache("X" * 500)
        assert isinstance(result, list)
        # 50 chars still doesn't
        result_short = with_prompt_cache("X" * 50)
        assert isinstance(result_short, str)

    def test_min_chars_env_invalid_falls_back_to_default(self, monkeypatch):
        """Bad env values must NEVER accept short prompts as cacheable."""
        for bad in ("not_a_number", "-1", "abc"):
            monkeypatch.setenv(ENV_MIN_CHARS, bad)
            result = with_prompt_cache("X" * 500)
            assert isinstance(result, str), f"bad env {bad!r} should fall back to default"

    def test_min_chars_explicit_kwarg_wins_over_env(self, monkeypatch):
        """Explicit min_chars kwarg overrides env override."""
        monkeypatch.setenv(ENV_MIN_CHARS, "100")
        # Even though env says 100, kwarg says 1000 → 500-char prompt not cached
        result = with_prompt_cache("X" * 500, min_chars=1000)
        assert isinstance(result, str)


class TestExtractCacheUsage:
    def test_extracts_both_counters_when_present(self):
        response = MagicMock()
        response.usage = MagicMock()
        response.usage.cache_creation_input_tokens = 100
        response.usage.cache_read_input_tokens = 200
        creation, read = extract_cache_usage(response)
        assert creation == 100
        assert read == 200

    def test_returns_zeros_when_usage_missing(self):
        """Older SDK responses + test mocks may not have ``usage``."""
        response = MagicMock(spec=[])  # spec=[] → no attributes
        creation, read = extract_cache_usage(response)
        assert (creation, read) == (0, 0)

    def test_returns_zeros_when_cache_fields_absent(self):
        """Pre-caching SDK responses have ``usage`` but no cache fields."""
        response = MagicMock()
        response.usage = MagicMock(spec=["input_tokens", "output_tokens"])
        creation, read = extract_cache_usage(response)
        assert (creation, read) == (0, 0)

    def test_returns_zeros_on_none_response(self):
        """Defensive: must not raise on degenerate inputs."""

        # Pass an object with no usage attribute at all
        class Bare:
            pass

        creation, read = extract_cache_usage(Bare())
        assert (creation, read) == (0, 0)


# ─────────────────────────────────────────────────────────────────────
# Section 2 — wired call sites route their system prompts through the helper
# ─────────────────────────────────────────────────────────────────────


class TestRouterUsesPromptCache:
    """Pin that genlab_core.llm.router._call_anthropic routes the system
    prompt through with_prompt_cache before passing to messages.create.

    The router uses lazy ``import anthropic`` inside the function, so
    we patch the source module ``anthropic.Anthropic`` directly — per
    CLAUDE.md's lazy-imported-dependency testing convention.
    """

    def _patched_anthropic_module(self):
        """Provide a mock anthropic module whose Anthropic() returns a
        capturing client. Returns (patch_ctx, mock_client)."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_client.messages.create.return_value = mock_response

        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = mock_client
        return patch.dict(
            "sys.modules",
            {"anthropic": fake_anthropic},
        ), mock_client

    def test_long_system_prompt_sent_as_list(self):
        from genlab_core.llm import router

        ctx, mock_client = self._patched_anthropic_module()
        with ctx:
            router._call_anthropic(
                model="claude-haiku-4-5-20251001",
                system="X" * 5000,  # long → should cache
                user="hi",
                max_tokens=10,
                temperature=0.0,
                api_key="dummy",
            )

        call = mock_client.messages.create.call_args
        system_arg = call.kwargs["system"]
        assert isinstance(system_arg, list), (
            "router._call_anthropic must route long system through with_prompt_cache"
        )
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    def test_short_system_prompt_sent_as_string(self):
        """Short system prompt falls back to plain-string (no cache write penalty)."""
        from genlab_core.llm import router

        ctx, mock_client = self._patched_anthropic_module()
        with ctx:
            router._call_anthropic(
                model="claude-haiku-4-5-20251001",
                system="short",  # below threshold
                user="hi",
                max_tokens=10,
                temperature=0.0,
                api_key="dummy",
            )

        call = mock_client.messages.create.call_args
        assert isinstance(call.kwargs["system"], str)

    def test_user_message_is_not_cached(self):
        """Per-call dynamic content stays in messages[], NOT in cached system.

        This is the architectural invariant — the cache key is the
        bytes of the system prompt. If we accidentally moved per-story
        content (the user msg) into the system block, we'd never hit
        the cache.
        """
        from genlab_core.llm import router

        unique_user_content = "Story-specific text that must NOT land in cache."
        ctx, mock_client = self._patched_anthropic_module()
        with ctx:
            router._call_anthropic(
                model="claude-haiku-4-5-20251001",
                system="X" * 5000,
                user=unique_user_content,
                max_tokens=10,
                temperature=0.0,
                api_key="dummy",
            )

        call = mock_client.messages.create.call_args
        # System block must NOT contain the dynamic user text
        system_arg = call.kwargs["system"]
        assert isinstance(system_arg, list)
        assert unique_user_content not in system_arg[0]["text"]
        # User message must contain the dynamic text
        messages = call.kwargs["messages"]
        assert messages == [{"role": "user", "content": unique_user_content}]


class TestPersonaEngineUsesPromptCache:
    """Pin that PersonaEngine routes its system prompt through with_prompt_cache."""

    def _make_engine(self):
        from genlab_core.engagement.persona_engine import PersonaEngine
        from genlab_core.engagement.persona_schema import (
            NichePersona,
            ReplyConstraints,
            VoiceConfig,
        )

        persona = NichePersona(
            name="TestChannel",
            voice=VoiceConfig(
                formality=0.5,
                enthusiasm=0.7,
                emoji_density="medium",
                vocabulary="friendly",
            ),
            # Style examples padded long enough that the built system
            # prompt comfortably exceeds 4000 chars and so caching fires.
            style_examples=["Great point about that play!" * 100] * 3,
            topics_to_avoid=["politics", "religion"],
            reply_constraints=ReplyConstraints(max_length_chars=200),
        )
        return PersonaEngine(persona=persona, toxicity_gate=None)

    def test_generate_reply_routes_system_through_helper(self):
        engine = self._make_engine()
        # Sanity: built system prompt is long enough to trigger caching
        assert len(engine._system_prompt) >= DEFAULT_MIN_CACHE_CHARS, (
            "test fixture system prompt must exceed cache threshold to verify wiring"
        )

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hi back")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_client.messages.create.return_value = mock_response
        engine._client = mock_client

        # Disable circuit-breaker pass-through complications by stubbing
        # ANTHROPIC_CB.call to just invoke the underlying callable.
        from genlab_core.engagement import persona_engine as pe_mod

        with patch.object(pe_mod.ANTHROPIC_CB, "call", side_effect=lambda fn: fn()):
            engine.generate_reply(comment="hello", platform="instagram")

        call = mock_client.messages.create.call_args
        system_arg = call.kwargs["system"]
        assert isinstance(system_arg, list), (
            "PersonaEngine.generate_reply must route system through with_prompt_cache"
        )
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


class TestHookGeneratorUsesPromptCache:
    """Pin that llm_hook_generator.generate_hook routes through helper.

    The 3-candidate loop fires 3 messages.create with the IDENTICAL
    system prompt. Caching means calls 2+3 pay ~10% input cost. We
    verify each call's system kwarg is the cached list shape AND that
    all 3 calls share the same cached block (same cache key).
    """

    def test_all_three_candidate_calls_use_cached_system(self, monkeypatch):
        from genlab_core.writing import llm_hook_generator

        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")

        mock_client = MagicMock()
        # Return distinct hooks per call so the candidate loop doesn't
        # short-circuit on dedup.
        hooks = ["Hook one specific to story", "Hook two specific to story", "Hook three"]
        responses = []
        for h in hooks:
            r = MagicMock()
            r.content = [MagicMock(text=h)]
            r.usage = MagicMock(input_tokens=10, output_tokens=5)
            responses.append(r)
        mock_client.messages.create.side_effect = responses

        # Lazy-import pattern: patch the source module ``anthropic`` via
        # sys.modules so the in-function ``import anthropic`` picks up
        # the mock.
        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            llm_hook_generator.generate_hook(
                story={"title": "Test story", "summary": "summary"},
                niche_id="gaming",
            )

        # 3 candidate calls all with cached system
        assert mock_client.messages.create.call_count >= 1
        for call in mock_client.messages.create.call_args_list:
            system_arg = call.kwargs["system"]
            assert isinstance(system_arg, list), "every candidate call must use cached system shape"
            assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

        # All 3 calls must share the EXACT same system text — that's
        # what makes the cache key match. Any per-call mutation of
        # the system would break caching even though cache_control is
        # set.
        system_texts = [
            call.kwargs["system"][0]["text"] for call in mock_client.messages.create.call_args_list
        ]
        assert len(set(system_texts)) == 1, (
            "all 3 candidates must share identical system text for cache to hit"
        )


# ─────────────────────────────────────────────────────────────────────
# Section 3 — cost observability wiring for cache fields
# ─────────────────────────────────────────────────────────────────────


class TestCostAccumulatorCacheFields:
    def test_record_llm_persists_cache_token_fields(self):
        from genlab_core.intelligence.cost_accumulator import CostAccumulator

        acc = CostAccumulator()
        acc.record_llm(
            model="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=400,
            cache_read_input_tokens=900,
        )
        assert len(acc.entries) == 1
        e = acc.entries[0]
        assert e.cache_creation_input_tokens == 400
        assert e.cache_read_input_tokens == 900

    def test_record_anthropic_usage_extracts_cache_fields(self):
        from genlab_core.intelligence.cost_accumulator import (
            CostAccumulator,
            record_anthropic_usage,
            set_accumulator,
        )

        acc = CostAccumulator()
        token = set_accumulator(acc)
        try:
            response = MagicMock()
            response.usage = MagicMock()
            response.usage.input_tokens = 50
            response.usage.output_tokens = 25
            response.usage.cache_creation_input_tokens = 400
            response.usage.cache_read_input_tokens = 800

            record_anthropic_usage("claude-haiku-4-5-20251001", response)
        finally:
            from genlab_core.intelligence.cost_accumulator import reset_accumulator

            reset_accumulator(token)

        assert len(acc.entries) == 1
        e = acc.entries[0]
        assert e.input_tokens == 50
        assert e.output_tokens == 25
        assert e.cache_creation_input_tokens == 400
        assert e.cache_read_input_tokens == 800

    def test_record_anthropic_usage_safe_without_cache_fields(self):
        """Older SDK responses or test mocks without cache fields must
        still produce a valid entry with zero cache tokens."""
        from genlab_core.intelligence.cost_accumulator import (
            CostAccumulator,
            record_anthropic_usage,
            reset_accumulator,
            set_accumulator,
        )

        acc = CostAccumulator()
        token = set_accumulator(acc)
        try:
            response = MagicMock()
            response.usage = MagicMock(spec=["input_tokens", "output_tokens"])
            response.usage.input_tokens = 50
            response.usage.output_tokens = 25
            record_anthropic_usage("claude-haiku-4-5-20251001", response)
        finally:
            reset_accumulator(token)

        assert len(acc.entries) == 1
        e = acc.entries[0]
        assert e.cache_creation_input_tokens == 0
        assert e.cache_read_input_tokens == 0

    def test_summary_surfaces_cache_token_totals(self):
        from genlab_core.intelligence.cost_accumulator import CostAccumulator

        acc = CostAccumulator()
        acc.record_llm(
            model="claude-haiku-4-5-20251001",
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=400,
            cache_read_input_tokens=900,
        )
        acc.record_llm(
            model="claude-haiku-4-5-20251001",
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=800,
        )
        summary = acc.summary()
        assert summary["cache_creation_tokens"] == 400
        assert summary["cache_read_tokens"] == 1700


# ─────────────────────────────────────────────────────────────────────
# Section 4 — caching is opt-OUT (default on); test the opt-out path
# ─────────────────────────────────────────────────────────────────────


class TestCachingDefaultIsOn:
    """Pin the contract that caching is the DEFAULT, opt-out via env.

    Rationale: U-01 motivation cites the 2026-06-27 credit-exhaustion
    incident — turning caching off by default would leave the cost
    lever idle until an operator explicitly flipped it. We want the
    savings on every cached call by default; the kill-switch is a
    safety valve for unexpected billing or cache-layer misbehaviour.
    """

    def test_no_env_set_means_caching_active(self, monkeypatch):
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        result = with_prompt_cache("X" * 5000)
        assert isinstance(result, list), "caching MUST be the default for long prompts"

    def test_explicit_zero_means_caching_active(self, monkeypatch):
        monkeypatch.setenv(ENV_DISABLE, "0")
        result = with_prompt_cache("X" * 5000)
        assert isinstance(result, list)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
