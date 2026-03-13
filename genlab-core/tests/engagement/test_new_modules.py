"""Tests for extracted engagement modules — toxicity_gate, spam_filter, timing, rate_limiter, persona_schema."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestToxicityGateResult:
    """Verify the new check_inbound returns a ToxicityResult with full info."""

    def test_toxic_inbound_returns_result_with_details(self):
        from genlab_core.engagement.toxicity_gate import ToxicityGate

        gate = ToxicityGate()
        mock_model = MagicMock()
        mock_model.predict.return_value = {
            "toxicity": 0.85,
            "insult": 0.6,
            "obscene": 0.1,
        }
        gate._model = mock_model

        result = gate.check_inbound("toxic text")
        assert result.is_toxic is True
        assert result.max_score == 0.85
        assert result.max_dimension == "toxicity"
        assert "insult" in result.all_scores

    def test_clean_inbound_returns_non_toxic_result(self):
        from genlab_core.engagement.toxicity_gate import ToxicityGate

        gate = ToxicityGate()
        mock_model = MagicMock()
        mock_model.predict.return_value = {"toxicity": 0.3, "insult": 0.1}
        gate._model = mock_model

        result = gate.check_inbound("nice comment")
        assert result.is_toxic is False

    def test_inbound_fail_open_on_error(self):
        from genlab_core.engagement.toxicity_gate import ToxicityGate

        gate = ToxicityGate()
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("crash")
        gate._model = mock_model

        result = gate.check_inbound("any text")
        assert result.is_toxic is False  # fail-open

    def test_outbound_fail_closed_on_error(self):
        from genlab_core.engagement.toxicity_gate import ToxicityGate

        gate = ToxicityGate()
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("crash")
        gate._model = mock_model

        assert gate.is_clean_outbound("any reply") is False  # fail-closed


class TestSpamFilter:
    def test_url_is_spam(self):
        from genlab_core.engagement.spam_filter import is_spam

        assert is_spam("Check this out https://scam.com/free") is True

    def test_promo_language_is_spam(self):
        from genlab_core.engagement.spam_filter import is_spam

        assert is_spam("Check my bio for free stuff") is True

    def test_genuine_comment_not_spam(self):
        from genlab_core.engagement.spam_filter import is_spam

        assert is_spam("This clip is absolutely insane, how did you find it?") is False

    def test_empty_is_spam(self):
        from genlab_core.engagement.spam_filter import is_spam

        assert is_spam("") is True
        assert is_spam(" ") is True

    def test_repeated_chars_is_spam(self):
        from genlab_core.engagement.spam_filter import is_spam

        assert is_spam("aaaaaaaaa") is True


class TestTiming:
    def test_human_delay_positive_and_bounded(self):
        from genlab_core.engagement.timing import human_delay

        samples = [human_delay() for _ in range(500)]
        assert all(s > 0 for s in samples)
        assert all(s <= 120.0 for s in samples)


class TestEngagementRateLimiter:
    def test_acquire_succeeds_when_under_limit(self):
        from genlab_core.engagement.rate_limiter import EngagementRateLimiter

        rl = EngagementRateLimiter({"youtube": 100})
        assert rl.acquire("youtube") is True

    def test_unknown_platform_allows_through(self):
        from genlab_core.engagement.rate_limiter import EngagementRateLimiter

        rl = EngagementRateLimiter({"youtube": 10})
        assert rl.acquire("unknown_platform") is True


class TestPersonaSchema:
    def test_valid_persona_yaml(self):
        from genlab_core.engagement.persona_schema import NichePersona

        data = {
            "name": "TestBrand",
            "voice": {"formality": 0.9, "enthusiasm": 0.5},
            "style_examples": ["example one"],
            "topics_to_avoid": ["politics"],
            "reply_constraints": {"max_length_chars": 200},
        }
        persona = NichePersona.model_validate(data)
        assert persona.name == "TestBrand"
        assert persona.voice.formality == 0.9
        assert persona.reply_constraints.max_length_chars == 200

    def test_defaults_applied(self):
        from genlab_core.engagement.persona_schema import NichePersona

        persona = NichePersona.model_validate({})
        assert persona.name == "GenLab"
        assert persona.voice.formality == 0.5
        assert persona.reply_constraints.max_length_chars == 280
