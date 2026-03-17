"""Tests for persona configuration loading and system prompt building."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.engagement.persona_schema import NichePersona, ReplyConstraints, VoiceConfig


class TestPersonaLoadFromYaml:
    def test_gaming_persona_loads(self):
        """Load the actual gaming persona.yaml and verify key fields."""
        path = Path("/Users/anarchistsid/GenLab/CriticalRush/niches/gaming/config/persona.yaml")
        if not path.exists():
            pytest.skip("Gaming persona.yaml not found")
        with open(path) as f:
            data = yaml.safe_load(f)
        persona = NichePersona(**data)
        assert persona.name == "CriticalRush"
        assert persona.voice.formality < 0.5  # Casual
        assert persona.voice.enthusiasm > 0.8  # High energy
        assert persona.voice.emoji_density == "high"

    def test_ai_creators_persona_loads(self):
        """Load the actual BB persona.yaml and verify key fields."""
        path = Path("/Users/anarchistsid/GenLab/BlackboxBrief/config/persona.yaml")
        if not path.exists():
            pytest.skip("BB persona.yaml not found")
        with open(path) as f:
            data = yaml.safe_load(f)
        persona = NichePersona(**data)
        assert persona.name == "Blackbox Brief"
        assert persona.voice.formality > 0.5  # Professional
        assert persona.voice.emoji_density == "low"

    def test_gaming_has_gamer_vocabulary(self):
        """Gaming persona should use gamer vocabulary."""
        path = Path("/Users/anarchistsid/GenLab/CriticalRush/niches/gaming/config/persona.yaml")
        if not path.exists():
            pytest.skip("Gaming persona.yaml not found")
        with open(path) as f:
            data = yaml.safe_load(f)
        persona = NichePersona(**data)
        assert persona.voice.vocabulary == "gamer"


class TestVoiceConfigValidation:
    def test_voice_config_accepts_valid_values(self):
        """VoiceConfig accepts floats in normal range."""
        vc = VoiceConfig(formality=0.3, enthusiasm=0.9)
        assert vc.formality == 0.3
        assert vc.enthusiasm == 0.9

    def test_voice_config_default_values(self):
        """VoiceConfig defaults are sensible middle-ground."""
        vc = VoiceConfig()
        assert vc.formality == 0.5
        assert vc.enthusiasm == 0.5
        assert vc.emoji_density == "low"
        assert vc.vocabulary == "casual"

    def test_voice_config_emoji_density_string(self):
        """emoji_density accepts arbitrary strings (no enum constraint)."""
        vc = VoiceConfig(emoji_density="medium")
        assert vc.emoji_density == "medium"

    def test_formality_accepts_boundary_values(self):
        """Formality of exactly 0 and 1 should work."""
        vc0 = VoiceConfig(formality=0.0)
        vc1 = VoiceConfig(formality=1.0)
        assert vc0.formality == 0.0
        assert vc1.formality == 1.0

    def test_formality_rejects_non_numeric(self):
        """Formality must be numeric — string raises validation error."""
        with pytest.raises(Exception):
            VoiceConfig(formality="high")


class TestReplyConstraints:
    def test_defaults(self):
        """ReplyConstraints has sensible defaults."""
        rc = ReplyConstraints()
        assert rc.max_length_chars == 280
        assert rc.language == "en"
        assert rc.always_include_cta is False

    def test_custom_max_length(self):
        rc = ReplyConstraints(max_length_chars=200)
        assert rc.max_length_chars == 200

    def test_language_override(self):
        rc = ReplyConstraints(language="ja")
        assert rc.language == "ja"

    def test_cta_override(self):
        rc = ReplyConstraints(always_include_cta=True)
        assert rc.always_include_cta is True


class TestNichePersonaDefaults:
    def test_empty_model_has_defaults(self):
        """NichePersona with no fields should use all defaults."""
        persona = NichePersona.model_validate({})
        assert persona.name == "GenLab"
        assert persona.voice.formality == 0.5
        assert persona.style_examples == []
        assert persona.topics_to_engage == []
        assert persona.topics_to_avoid == []
        assert persona.reply_constraints.max_length_chars == 280

    def test_partial_voice_override(self):
        """Overriding one voice field keeps others at default."""
        persona = NichePersona.model_validate({"voice": {"enthusiasm": 0.9}})
        assert persona.voice.enthusiasm == 0.9
        assert persona.voice.formality == 0.5  # default preserved
        assert persona.voice.emoji_density == "low"

    def test_style_examples_are_preserved(self):
        """style_examples round-trip through the model."""
        examples = ["yo that's wild", "no way 🔥"]
        persona = NichePersona(style_examples=examples)
        assert persona.style_examples == examples

    def test_topics_to_avoid_round_trip(self):
        avoid = ["politics", "religion"]
        persona = NichePersona(topics_to_avoid=avoid)
        assert persona.topics_to_avoid == avoid


class TestPersonaEngineSystemPrompt:
    """Test that PersonaEngine._build_system_prompt produces expected content."""

    def test_system_prompt_contains_brand_name(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(name="CriticalRush")
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "CriticalRush" in prompt

    def test_casual_persona_gets_casual_description(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(voice=VoiceConfig(formality=0.1))
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "very casual" in prompt.lower()

    def test_formal_persona_gets_professional_description(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(voice=VoiceConfig(formality=0.8))
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "professional" in prompt.lower()

    def test_high_enthusiasm_gets_energetic_description(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(voice=VoiceConfig(enthusiasm=0.9))
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "enthusiastic" in prompt.lower() or "energetic" in prompt.lower()

    def test_low_enthusiasm_gets_calm_description(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(voice=VoiceConfig(enthusiasm=0.3))
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "calm" in prompt.lower()

    def test_max_chars_included_in_prompt(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(reply_constraints=ReplyConstraints(max_length_chars=200))
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "200" in prompt

    def test_topics_to_avoid_included(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(topics_to_avoid=["politics", "gambling"])
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "politics" in prompt
        assert "gambling" in prompt

    def test_style_examples_included(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona(style_examples=["bro this is insane"])
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "bro this is insane" in prompt

    def test_prompt_never_mentions_ai(self):
        """System prompt instructs the model to never mention it is AI."""
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = NichePersona()
        engine = PersonaEngine(persona=persona)
        prompt = engine._build_system_prompt()
        assert "never mention you are ai" in prompt.lower()
