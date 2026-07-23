"""Tests for genlab_core.engagement.persona_engine."""

import unittest
from unittest.mock import MagicMock, patch

from genlab_core.engagement.persona_schema import NichePersona, ReplyConstraints, VoiceConfig


def _make_persona() -> NichePersona:
    return NichePersona(
        name="TestChannel",
        niche_id="gaming",
        voice=VoiceConfig(
            formality=0.2,
            enthusiasm=0.8,
            emoji_density="moderate",
            vocabulary="casual gamer slang",
        ),
        style_examples=["fire take!", "nah that's cap"],
        topics_to_avoid=["politics", "religion"],
        reply_constraints=ReplyConstraints(max_length_chars=150),
    )


class TestPersonaEngine(unittest.TestCase):
    def test_build_system_prompt(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        engine = PersonaEngine(persona=_make_persona())
        prompt = engine._build_system_prompt()
        assert "TestChannel" in prompt
        assert "casual" in prompt.lower()
        assert "150" in prompt
        assert "politics" in prompt

    @patch("genlab_core.engagement.persona_engine.ANTHROPIC_CB")
    def test_generate_reply_circuit_open(self, mock_cb):
        from genlab_core.engagement.persona_engine import PersonaEngine
        from genlab_core.http.circuit_breaker import CircuitOpenError

        mock_cb.call.side_effect = CircuitOpenError("anthropic")
        engine = PersonaEngine(persona=_make_persona())

        result = engine.generate_reply("great clip!", "youtube")
        assert result is None

    def test_toxicity_gate_blocks(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        mock_gate = MagicMock()
        mock_gate.is_clean_outbound.return_value = False

        engine = PersonaEngine(persona=_make_persona(), toxicity_gate=mock_gate)
        # With toxicity gate blocking all replies, should return None after retries
        with patch.object(engine, "_client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text="Thanks!")]
            mock_client.messages.create.return_value = mock_resp
            engine._client = mock_client

            result = engine.generate_reply("hello", "instagram", max_retries=1)
            assert result is None


class TestLastErrorReasonAttribution(unittest.TestCase):
    """2026-07-23: PersonaEngine._last_error_reason must capture WHY
    generate_reply returned None. Prod observed: 3 days of Threads
    engagement failures all writing the same "Reply generation failed"
    error_message — root cause opaque because different failure modes
    look identical downstream."""

    def test_init_last_error_reason_empty(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        engine = PersonaEngine(persona=_make_persona())
        assert engine._last_error_reason == ""

    @patch("genlab_core.engagement.persona_engine.ANTHROPIC_CB")
    def test_circuit_open_sets_last_error_reason(self, mock_cb):
        from genlab_core.engagement.persona_engine import PersonaEngine
        from genlab_core.http.circuit_breaker import CircuitOpenError

        mock_cb.call.side_effect = CircuitOpenError("anthropic")
        engine = PersonaEngine(persona=_make_persona())

        result = engine.generate_reply("great clip!", "youtube")
        assert result is None
        assert engine._last_error_reason == "circuit_open", (
            "circuit-open failure must set _last_error_reason so "
            "downstream can distinguish it from retries_exhausted / "
            "toxicity_gate"
        )

    def test_toxicity_gate_sets_last_error_reason(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        mock_gate = MagicMock()
        mock_gate.is_clean_outbound.return_value = False

        engine = PersonaEngine(persona=_make_persona(), toxicity_gate=mock_gate)
        with patch.object(engine, "_client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text="Thanks!")]
            mock_client.messages.create.return_value = mock_resp
            engine._client = mock_client

            result = engine.generate_reply("hello", "instagram", max_retries=1)
        assert result is None
        assert engine._last_error_reason == "toxicity_gate", (
            "toxicity-gate failure must set _last_error_reason so "
            "operators know the fix is tuning the gate, not topping "
            "up Anthropic credits"
        )

    def test_success_clears_last_error_reason(self):
        """Success on any attempt clears the field so a subsequent None
        return isn't misattributed to a previous failure."""
        from genlab_core.engagement.persona_engine import PersonaEngine

        # First: set a poisoned last_error_reason.
        engine = PersonaEngine(persona=_make_persona())
        engine._last_error_reason = "circuit_open"  # stale

        # Now succeed on a fresh call.
        mock_gate = MagicMock()
        mock_gate.is_clean_outbound.return_value = True
        engine._toxicity_gate = mock_gate

        with patch.object(engine, "_client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text="Thanks!")]
            mock_client.messages.create.return_value = mock_resp
            engine._client = mock_client

            result = engine.generate_reply("hello", "instagram", max_retries=1)

        assert result == "Thanks!"
        assert engine._last_error_reason == "", (
            "successful reply must clear _last_error_reason so a later "
            "None return doesn't get misattributed"
        )


if __name__ == "__main__":
    unittest.main()
