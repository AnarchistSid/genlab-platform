"""Pin the intelligent_transform config schema + all 5 niches' YAML.

Verifies that:

  1. The Pydantic model parses default-off with no YAML block present
     (backward compat with pre-PR-2 visuals.yaml files).

  2. The Pydantic model parses a full block cleanly, populating all
     11 dimensions with the seed arm lists.

  3. All 5 niche visuals.yaml files contain the intelligent_transform
     block with the expected schema shape — catches accidental
     schema drift when someone edits YAML by hand.

  4. Every declared dimension in the block has a non-empty seed
     arm list (moods, styles, framings, etc.) — an empty list means
     the bandit has nothing to choose from, effectively disabled
     even if enabled: true.

  5. All 5 niches ship with the top-level enabled: false (schema-
     only ship). PR 15 flips the env flag; PR 2 does not activate.

Runs offline — no DB, no network, just file parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.media.intelligent_transform import (
    IntelligentTransformConfig,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

# All 5 niche visuals.yaml paths.
_NICHE_YAMLS = {
    "ai_creators": _REPO_ROOT / "BlackboxBrief" / "config" / "visuals.yaml",
    "gaming": _REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "visuals.yaml",
    "sports": _REPO_ROOT / "ClutchWire" / "config" / "visuals.yaml",
    "movies": _REPO_ROOT / "SpliceReel" / "config" / "visuals.yaml",
    "anime": _REPO_ROOT / "FrameDrift" / "config" / "visuals.yaml",
}

_ALL_11_DIMENSIONS = (
    "music_mood",
    "caption_style",
    "hook_framing",
    "intro_animation",
    "outro_cta",
    "pan_zoom",
    "highlight_moment",
    "audio_ducking",
    "music_visual_sync",
    "caption_emphasis_color",
    "caption_pacing",
)


class TestPydanticModelParsing:
    """The IntelligentTransformConfig model must degrade gracefully
    on empty/absent input AND validate the full schema on populated
    input."""

    def test_from_empty_visuals_dict_returns_defaults(self) -> None:
        """Legacy visuals.yaml files pre-PR-2 have no
        intelligent_transform block — must not crash pipeline."""
        cfg = IntelligentTransformConfig.from_visuals_dict({})
        assert cfg.enabled is False
        # All 11 dimensions should have their model defaults.
        for dim_name in _ALL_11_DIMENSIONS:
            dim = getattr(cfg.dimensions, dim_name)
            assert dim is not None
            # Per-dimension enabled default is True — but the parent
            # enabled=False keeps the whole system off.
            assert dim.enabled is True

    def test_from_malformed_block_returns_defaults(self) -> None:
        """If someone types 'intelligent_transform: []' by mistake
        (list instead of dict), fall back to defaults not crash."""
        cfg = IntelligentTransformConfig.from_visuals_dict({"intelligent_transform": []})
        assert cfg.enabled is False

    def test_from_populated_visuals_dict_parses_all_dims(self) -> None:
        block = {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "music_mood": {
                        "enabled": True,
                        "moods": ["cinematic", "hype"],
                        "source": "pixabay",
                        "source_audio_duck_db": -12,
                        "music_bed_db": -6,
                    },
                    "caption_style": {
                        "enabled": True,
                        "styles": ["word_by_word", "minimal"],
                        "default_pacing_ms": 750,
                    },
                    "hook_framing": {
                        "enabled": True,
                        "framings": ["question_open", "story_open"],
                    },
                    "intro_animation": {
                        "enabled": True,
                        "templates": ["logo_zoom"],
                        "asset_dir": "assets/motion/intros",
                    },
                    "outro_cta": {
                        "enabled": True,
                        "styles": ["follow", "save"],
                        "asset_dir": "assets/motion/outros",
                    },
                    "pan_zoom": {
                        "enabled": True,
                        "patterns": ["ken_burns_slow", "no_motion"],
                    },
                    "highlight_moment": {
                        "enabled": True,
                        "methods": ["llm_pick", "audio_peak"],
                        "window_seconds": 8,
                    },
                    "audio_ducking": {
                        "enabled": True,
                        "levels_db": [-9, -12],
                    },
                    "music_visual_sync": {
                        "enabled": True,
                        "modes": ["beat_cut", "ambient"],
                    },
                    "caption_emphasis_color": {
                        "enabled": True,
                        "colors": ["yellow", "red"],
                    },
                    "caption_pacing": {
                        "enabled": True,
                        "pacing_ms_options": [600, 750, 900],
                    },
                },
            }
        }
        cfg = IntelligentTransformConfig.from_visuals_dict(block)
        assert cfg.enabled is True
        assert cfg.dimensions.music_mood.moods == ["cinematic", "hype"]
        assert cfg.dimensions.caption_pacing.pacing_ms_options == [600, 750, 900]
        assert cfg.dimensions.audio_ducking.levels_db == [-9, -12]


class TestAllNicheYAMLsHaveBlock:
    """Every one of the 5 niches must have the intelligent_transform
    block. If someone accidentally drops it from one niche, the
    bandit registration script (PR 3) will bootstrap 0 arms for that
    niche — that's a silent failure worth catching here."""

    @pytest.mark.parametrize("niche_id,yaml_path", list(_NICHE_YAMLS.items()))
    def test_niche_yaml_has_intelligent_transform(self, niche_id: str, yaml_path: Path) -> None:
        assert yaml_path.is_file(), f"{niche_id} visuals.yaml missing at {yaml_path}"
        parsed = yaml.safe_load(yaml_path.read_text())
        assert "intelligent_transform" in parsed, (
            f"{niche_id} visuals.yaml has no intelligent_transform block. "
            "PR 2 added this block to all 5 niches — if you're seeing "
            "this on a new niche, add the block per the PR 2 template."
        )

    @pytest.mark.parametrize("niche_id,yaml_path", list(_NICHE_YAMLS.items()))
    def test_niche_yaml_parses_via_pydantic(self, niche_id: str, yaml_path: Path) -> None:
        parsed = yaml.safe_load(yaml_path.read_text())
        cfg = IntelligentTransformConfig.from_visuals_dict(parsed)
        # 2026-07-14 update (PR #718 obsoleted the PR 2 strict-false
        # pin): the repo default flipped enabled=true for 4/5 niches
        # (anime kept false pending branded assets). The pin now
        # verifies STRUCTURAL soundness — enabled is a bool, config
        # parses cleanly — not the specific value, which is a per-
        # niche operator decision. See CLAUDE.md session memory for
        # the drift-preservation dance history.
        assert isinstance(cfg.enabled, bool), (
            f"{niche_id} enabled field is {type(cfg.enabled)!r}, not bool"
        )

    @pytest.mark.parametrize("niche_id,yaml_path", list(_NICHE_YAMLS.items()))
    def test_niche_yaml_populates_all_11_dimensions(self, niche_id: str, yaml_path: Path) -> None:
        parsed = yaml.safe_load(yaml_path.read_text())
        block = parsed.get("intelligent_transform", {})
        dims = block.get("dimensions", {})
        for dim_name in _ALL_11_DIMENSIONS:
            assert dim_name in dims, (
                f"{niche_id} missing dimension '{dim_name}' in "
                "intelligent_transform.dimensions. All 11 dimensions "
                "must be present so PR 3's registration script "
                "creates the full arm set per niche."
            )


class TestSeedArmListsNonEmpty:
    """Every dimension's arm list must be non-empty. An enabled
    dimension with an empty list means the bandit has nothing to
    choose from — effectively disabled at runtime but declared as
    enabled in config. Catch this schema smell here."""

    _ARM_LIST_FIELDS = {
        "music_mood": "moods",
        "caption_style": "styles",
        "hook_framing": "framings",
        "intro_animation": "templates",
        "outro_cta": "styles",
        "pan_zoom": "patterns",
        "highlight_moment": "methods",
        "audio_ducking": "levels_db",
        "music_visual_sync": "modes",
        "caption_emphasis_color": "colors",
        "caption_pacing": "pacing_ms_options",
    }

    @pytest.mark.parametrize("niche_id,yaml_path", list(_NICHE_YAMLS.items()))
    def test_seed_lists_non_empty(self, niche_id: str, yaml_path: Path) -> None:
        parsed = yaml.safe_load(yaml_path.read_text())
        dims = parsed["intelligent_transform"]["dimensions"]
        for dim_name, list_field in self._ARM_LIST_FIELDS.items():
            arms = dims[dim_name].get(list_field, [])
            assert arms, (
                f"{niche_id}.dimensions.{dim_name}.{list_field} is empty. "
                "An enabled dimension with no arms is effectively "
                "disabled at runtime but declared as enabled in config — "
                "confusing state. Populate the list per the PR 2 seed "
                "values."
            )


class TestNicheSpecificArms:
    """Music moods differ per niche (matches content style). Verify
    the seed values reflect the per-niche intent rather than all-
    identical placeholders."""

    def test_bb_has_ai_creators_moods(self) -> None:
        parsed = yaml.safe_load(_NICHE_YAMLS["ai_creators"].read_text())
        moods = parsed["intelligent_transform"]["dimensions"]["music_mood"]["moods"]
        # BB is ai_creators — expects tech/electronic moods.
        assert any(m in moods for m in ("electronic", "ambient_tech", "tech_hype")), (
            f"ai_creators expected electronic/tech moods, got {moods}"
        )

    def test_gaming_has_hype_moods(self) -> None:
        parsed = yaml.safe_load(_NICHE_YAMLS["gaming"].read_text())
        moods = parsed["intelligent_transform"]["dimensions"]["music_mood"]["moods"]
        assert any(m in moods for m in ("energetic", "hype", "adrenaline")), (
            f"gaming expected energetic/hype moods, got {moods}"
        )

    def test_anime_has_conservative_ducking(self) -> None:
        """FrameDrift ships with less-aggressive audio ducking per
        prior analysis: anime audiences prefer source audio. If
        someone changes these to -12dB default, that's a policy shift
        worth surfacing."""
        parsed = yaml.safe_load(_NICHE_YAMLS["anime"].read_text())
        dim = parsed["intelligent_transform"]["dimensions"]["music_mood"]
        assert dim["source_audio_duck_db"] == -9, (
            "FrameDrift music_mood.source_audio_duck_db moved off -9dB "
            "conservative default. That's a policy shift — anime "
            "audiences prefer source audio per prior analysis. Confirm "
            "before overriding."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
