"""Pin persona_writer_hint (Phase 4.D bridge, 2026-08-15):

The Phase 4.D drift detector scores hooks against persona.yaml
using specific knobs. The writer must see those same knobs.

  * is_enabled_for respects rollout env value semantics
  * format_persona_prompt_section renders every voice knob
  * emoji_density line matches the value
  * formality below 0.4 → casual line; >= 0.7 → professional line
  * enthusiasm >= 0.7 → high-passion line
  * topics_to_avoid rendered as forbidden list
  * style_examples rendered as exemplars
  * None / empty persona returns empty string
  * build_hint_for fails to empty on missing persona (fail-open)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.writing.persona_writer_hint import (
    build_hint_for,
    format_persona_prompt_section,
    is_enabled_for,
)


class TestIsEnabledFor:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", val)
        assert is_enabled_for("anime") is False

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_PERSONA_HINT_NICHES", raising=False)
        assert is_enabled_for("anime") is False

    @pytest.mark.parametrize("val", ["all", "*", "ALL"])
    def test_wildcard_enables_all(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", val)
        assert is_enabled_for("anime") is True
        assert is_enabled_for("gaming") is True
        assert is_enabled_for("ai_creators") is True

    def test_single_niche_canary(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime")
        assert is_enabled_for("anime") is True
        assert is_enabled_for("gaming") is False

    def test_comma_list(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime,movies")
        assert is_enabled_for("anime") is True
        assert is_enabled_for("movies") is True
        assert is_enabled_for("gaming") is False


class TestFormat:
    def _anime_persona(self) -> dict:
        return {
            "name": "FrameDrift",
            "voice": {
                "formality": 0.35,
                "enthusiasm": 0.85,
                "emoji_density": "medium",
                "vocabulary": "sakuga",
            },
            "style_examples": [
                "The key animation in this cut is Yutaka Nakamura-level 🔥",
                "Studio MAPPA went all out on this sequence.",
            ],
            "topics_to_engage": ["sakuga_analysis", "animation_craft"],
            "topics_to_avoid": ["shipping_wars", "politics"],
        }

    def test_none_returns_empty(self):
        assert format_persona_prompt_section(None) == ""

    def test_empty_dict_returns_empty(self):
        assert format_persona_prompt_section({}) == ""

    def test_full_persona_renders_every_section(self):
        block = format_persona_prompt_section(self._anime_persona())
        assert "PERSONA VOICE MANDATE" in block
        assert "sakuga" in block  # vocabulary
        assert "MEDIUM" in block  # emoji density uppercased in instruction
        assert "0.35" in block  # formality value
        assert "0.85" in block  # enthusiasm value
        assert "shipping_wars" in block  # avoid
        assert "sakuga_analysis" in block  # engage
        assert "Studio MAPPA" in block  # exemplar

    def test_gaming_high_emoji_density_line(self):
        block = format_persona_prompt_section({
            "voice": {"emoji_density": "high", "vocabulary": "gamer"},
        })
        assert "HIGH" in block
        assert "2-3 relevant emoji" in block

    def test_low_formality_gets_casual_directive(self):
        block = format_persona_prompt_section({
            "voice": {"formality": 0.2, "vocabulary": "casual"},
        })
        assert "casual insider" in block.lower()
        assert "journalese" in block.lower()

    def test_high_formality_gets_professional_directive(self):
        block = format_persona_prompt_section({
            "voice": {"formality": 0.85, "vocabulary": "technical"},
        })
        assert "professional" in block.lower()

    def test_high_enthusiasm_line(self):
        block = format_persona_prompt_section({
            "voice": {"enthusiasm": 0.9, "vocabulary": "sakuga"},
        })
        assert "high passion" in block.lower()

    def test_low_enthusiasm_line(self):
        block = format_persona_prompt_section({
            "voice": {"enthusiasm": 0.15, "vocabulary": "technical"},
        })
        assert "measured tone" in block.lower()

    def test_none_emoji_density_forbids(self):
        block = format_persona_prompt_section({
            "voice": {"emoji_density": "none", "vocabulary": "technical"},
        })
        assert "NONE" in block
        assert "do not add emoji" in block.lower()

    def test_no_style_examples_no_exemplar_section(self):
        block = format_persona_prompt_section({
            "voice": {"emoji_density": "low", "vocabulary": "gamer"},
        })
        assert "Style exemplars" not in block

    def test_persona_with_only_topics_still_renders(self):
        block = format_persona_prompt_section({
            "topics_to_avoid": ["politics"],
        })
        assert "AVOID entirely" in block
        assert "politics" in block


class TestBuildHintFor:
    def test_flag_off_returns_empty(self, monkeypatch):
        monkeypatch.delenv("GENLAB_PERSONA_HINT_NICHES", raising=False)
        assert build_hint_for("anime") == ""

    def test_flag_on_missing_persona_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime")
        with patch(
            "genlab_core.quality.persona_drift.load_persona",
            return_value=None,
        ):
            assert build_hint_for("anime") == ""

    def test_flag_on_load_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime")
        with patch(
            "genlab_core.quality.persona_drift.load_persona",
            side_effect=Exception("boom"),
        ):
            assert build_hint_for("anime") == ""

    def test_flag_on_real_persona_returns_block(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime")
        with patch(
            "genlab_core.quality.persona_drift.load_persona",
            return_value={
                "voice": {"vocabulary": "sakuga", "emoji_density": "medium"},
                "style_examples": ["Studio MAPPA went all out."],
            },
        ):
            hint = build_hint_for("anime")
        assert "sakuga" in hint
        assert "PERSONA VOICE MANDATE" in hint

    def test_non_canary_niche_gets_no_hint(self, monkeypatch):
        """Anime-only canary must not leak into other niches."""
        monkeypatch.setenv("GENLAB_PERSONA_HINT_NICHES", "anime")
        # No mocking needed — even if load_persona would succeed
        # for gaming, the enablement check short-circuits first.
        assert build_hint_for("gaming") == ""
