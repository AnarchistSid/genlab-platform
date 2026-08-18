"""2026-08-12: pins for the standalone content-tone → music-mood
LLM matcher. Standalone module — no consumer wire tonight.

Motivation: trending-audio infrastructure (audio_replacer, music
beds, transformation_orchestrator, music_mood bandit) ships end-to-
end today. Real gap per operator brief: audio isn't "trending" and
mood selection is bandit-only (no content-fit prior). This module
adds the LLM primitive; wire lands in follow-up after operator
validates suggestions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestFlagGating:
    def test_disabled_returns_none(self, monkeypatch):
        """Default OFF. Every path returns None without calling API."""
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.delenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", raising=False)
        result = suggest_mood(
            niche_id="anime",
            hook="test hook",
            title="test title",
            summary="test summary",
            available_moods=["dramatic", "hype", "chill"],
        )
        assert result is None

    def test_empty_moods_returns_none(self, monkeypatch):
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        result = suggest_mood("anime", "h", "t", "s", available_moods=[])
        assert result is None

    def test_no_content_context_returns_none(self, monkeypatch):
        """Cheaper to skip than pay LLM for empty content."""
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        result = suggest_mood(
            "anime", hook="", title="", summary="",
            available_moods=["dramatic"],
        )
        assert result is None


class TestLLMCallAndParse:
    def _mock_llm_response(self, text: str):
        """Build a MagicMock that mirrors anthropic's response shape."""
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_valid_llm_response_returns_suggestion(self, monkeypatch):
        from genlab_core.media.music_mood_llm_fit import (
            MoodSuggestion,
            suggest_mood,
        )

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_llm_response(
            '{"mood": "dramatic", "reasoning": "epic anime fight", "confidence": 0.85}'
        )

        with patch(
            "anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = suggest_mood(
                niche_id="anime",
                hook="Epic fight scene!",
                title="Attack on Titan",
                summary="Levi vs Beast Titan showdown",
                available_moods=["dramatic", "hype", "chill"],
            )

        assert isinstance(result, MoodSuggestion)
        assert result.top_mood == "dramatic"
        assert result.confidence == 0.85
        assert "epic" in result.reasoning.lower()

    def test_llm_picks_mood_not_in_available_returns_none(self, monkeypatch):
        """LLM might hallucinate a mood outside the pool. Reject."""
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_llm_response(
            '{"mood": "operatic_bombastic", "reasoning": "why not", "confidence": 0.7}'
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = suggest_mood(
                "anime", "hook", "title", "summary",
                available_moods=["dramatic", "hype"],
            )

        assert result is None

    def test_unparseable_llm_output_returns_none(self, monkeypatch):
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_llm_response(
            "Sure, I think dramatic would be great!"  # no JSON
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = suggest_mood(
                "anime", "hook", "title", "summary",
                available_moods=["dramatic"],
            )
        assert result is None

    def test_markdown_fenced_json_still_parses(self, monkeypatch):
        """LLM sometimes wraps JSON in ```json fences despite the
        prompt telling it not to. Should still parse."""
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_llm_response(
            '```json\n{"mood": "hype", "reasoning": "fast cuts", "confidence": 0.7}\n```'
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = suggest_mood(
                "sports", "hook", "title", "summary",
                available_moods=["hype", "aggressive"],
            )
        assert result is not None
        assert result.top_mood == "hype"

    def test_confidence_out_of_range_clamps(self, monkeypatch):
        """Defensive: LLM might output confidence>1 or <0. Clamp."""
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_llm_response(
            '{"mood": "dramatic", "reasoning": "x", "confidence": 99}'
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = suggest_mood(
                "anime", "hook", "title", "summary",
                available_moods=["dramatic"],
            )
        assert result is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_api_exception_returns_none_no_crash(self, monkeypatch):
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network")

        with patch("anthropic.Anthropic", return_value=mock_client):
            # Must not raise
            result = suggest_mood(
                "anime", "hook", "title", "summary",
                available_moods=["dramatic"],
            )
        assert result is None

    def test_missing_api_key_returns_none(self, monkeypatch):
        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = suggest_mood(
            "anime", "hook", "title", "summary",
            available_moods=["dramatic"],
        )
        assert result is None

    def test_missing_api_key_logs_warning_not_silent(
        self, monkeypatch, caplog,
    ):
        """2026-08-18 (task #214): the no-key path used to `return None`
        silently. Rule #19: elevate to WARN so operator sees credit-
        exhaustion in journalctl."""
        import logging as _logging

        from genlab_core.media.music_mood_llm_fit import suggest_mood

        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with caplog.at_level(_logging.WARNING):
            suggest_mood(
                "anime", "hook", "title", "summary",
                available_moods=["dramatic"],
            )
        assert any(
            "no ANTHROPIC_API_KEY" in r.message for r in caplog.records
        ), f"expected WARN log; got: {[r.message for r in caplog.records]}"


class TestOpenAIFallbackWire:
    """2026-08-18 (task #214): pin that suggest_mood goes through
    AnthropicLLMClient, so it inherits the 2026-07-21 OpenAI GPT-4o-
    mini fallback. Previously called `anthropic.Anthropic()` directly
    and silent-failed when Anthropic credit was exhausted even when
    OPENAI_API_KEY was configured.
    """

    def test_suggest_mood_uses_anthropic_llm_client(self):
        """Structural pin — code imports AnthropicLLMClient not
        `import anthropic` at module level."""
        import pathlib

        src = (
            pathlib.Path(__file__).parents[2]
            / "src" / "genlab_core" / "media" / "music_mood_llm_fit.py"
        ).read_text()
        # Must call the wrapper (which has OpenAI fallback)
        assert "AnthropicLLMClient" in src, (
            "music_mood_llm_fit must route via AnthropicLLMClient "
            "to inherit OpenAI fallback (class-of-bug: LLM as SPOF "
            "for producer pipeline)"
        )
        assert "client.complete(" in src, (
            "AnthropicLLMClient.complete is the wrapper's entrypoint"
        )
