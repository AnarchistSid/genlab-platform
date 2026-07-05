"""Pin the transformation arm registration script (PR 3).

Two angles:
  1. ``enumerate_arms_for_niche()`` produces the correct arms with
     the correct arm_id format for each niche's real visuals.yaml.
  2. Registration flow uses ``save_arm(arm_type='transformation', ...)``
     with all-1.0 alpha/beta and the composite arm_id — verified via
     mock proxy so no DB touch.

Also verifies:
  * Integer arm values (audio_ducking dB, caption_pacing ms) stringify.
  * Values containing ``__`` are rejected (would corrupt arm_id parse).
  * Composite arm_id format matches the ``transform__<dim>__<value>``
    convention Mission Control cards + Strategist expect.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "register_transformation_arms.py"


def _load_script_module():
    """Load the script as a module so we can call its functions.

    Not on the package path — it lives in ``scripts/`` at workspace
    root. Use importlib to load it dynamically."""
    spec = importlib.util.spec_from_file_location(
        "register_transformation_arms", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


class TestArmIdFormat:
    """The ``transform__<dim>__<value>`` composite is the contract
    consumers depend on (Mission Control cards + Strategist + cross-
    niche transfer suffix extractor). Pin the format."""

    def test_composite_format(self, script_module):
        arm_id = script_module._arm_id("music_mood", "cinematic")
        assert arm_id == "transform__music_mood__cinematic"

    def test_composite_uses_double_underscore(self, script_module):
        arm_id = script_module._arm_id("caption_pacing", "750")
        parts = arm_id.split("__")
        assert len(parts) == 3
        assert parts[0] == "transform"
        assert parts[1] == "caption_pacing"
        assert parts[2] == "750"

    def test_value_containing_separator_rejected(self, script_module):
        """A value with ``__`` in it would silently break the composite
        parse. Better to fail loud at registration time than debug
        strange downstream behavior later."""
        with pytest.raises(ValueError, match="contains '__' separator"):
            script_module._arm_id("music_mood", "cinematic__hype")

    def test_negative_int_stays_negative(self, script_module):
        """Audio ducking uses negative dB values. Ensure the sign
        survives arm_id encoding — a value of -12 should produce
        ``transform__audio_ducking__-12``, not ``__12`` or ``_-12``."""
        arm_id = script_module._arm_id("audio_ducking", "-12")
        assert arm_id == "transform__audio_ducking__-12"


class TestEnumerateArms:
    """The enumeration walks every dimension and yields one arm per
    value. Pin the counts against the 5 real visuals.yaml files."""

    _NICHE_YAMLS = {
        "ai_creators": _REPO_ROOT / "BlackboxBrief" / "config" / "visuals.yaml",
        "gaming": _REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "visuals.yaml",
        "sports": _REPO_ROOT / "ClutchWire" / "config" / "visuals.yaml",
        "movies": _REPO_ROOT / "SpliceReel" / "config" / "visuals.yaml",
        "anime": _REPO_ROOT / "FrameDrift" / "config" / "visuals.yaml",
    }

    @pytest.mark.parametrize("niche_id,yaml_path", list(_NICHE_YAMLS.items()))
    def test_enumerate_produces_arms(
        self, script_module, niche_id, yaml_path
    ) -> None:
        import yaml

        parsed = yaml.safe_load(yaml_path.read_text())
        arms = list(script_module.enumerate_arms_for_niche(parsed))
        # At least 50 arms per niche (11 dims × ~5 arms average).
        assert len(arms) >= 50, (
            f"{niche_id}: expected ≥50 arms, got {len(arms)}. Something "
            "likely regressed in visuals.yaml — check the "
            "intelligent_transform block."
        )

    def test_enumerate_returns_correct_tuple_shape(self, script_module) -> None:
        """Return type must be (arm_id, dimension, dimension_value)."""
        block = {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "music_mood": {
                        "enabled": True,
                        "moods": ["cinematic"],
                    },
                },
            }
        }
        arms = list(script_module.enumerate_arms_for_niche(block))
        assert len(arms) == 1
        arm_id, dim, value = arms[0]
        assert arm_id == "transform__music_mood__cinematic"
        assert dim == "music_mood"
        assert value == "cinematic"

    def test_enumerate_stringifies_int_values(self, script_module) -> None:
        """audio_ducking + caption_pacing carry int values in YAML but
        arm_id + dimension_value are strings on the DB side."""
        block = {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "audio_ducking": {
                        "enabled": True,
                        "levels_db": [-12],
                    },
                    "caption_pacing": {
                        "enabled": True,
                        "pacing_ms_options": [750],
                    },
                },
            }
        }
        arms = list(script_module.enumerate_arms_for_niche(block))
        arm_ids = {arm[0] for arm in arms}
        values = {arm[2] for arm in arms}
        assert "transform__audio_ducking__-12" in arm_ids
        assert "transform__caption_pacing__750" in arm_ids
        # dimension_values are strings
        for _, _, val in arms:
            assert isinstance(val, str)

    def test_enumerate_empty_block_yields_nothing(self, script_module) -> None:
        arms = list(script_module.enumerate_arms_for_niche({}))
        assert arms == []


class TestBanditArmsPerNiche:
    """Verify each real niche produces the expected arms across all
    11 dimensions. If a dimension is silently dropped, this catches it."""

    _EXPECTED_DIMENSIONS = {
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
    }

    @pytest.mark.parametrize(
        "niche_id,yaml_path", list(TestEnumerateArms._NICHE_YAMLS.items())
    )
    def test_all_11_dimensions_produce_arms(
        self, script_module, niche_id, yaml_path
    ) -> None:
        import yaml

        parsed = yaml.safe_load(yaml_path.read_text())
        arms = list(script_module.enumerate_arms_for_niche(parsed))
        dimensions_seen = {dim for _, dim, _ in arms}
        assert dimensions_seen == self._EXPECTED_DIMENSIONS, (
            f"{niche_id}: dimensions_seen {dimensions_seen} != "
            f"expected {self._EXPECTED_DIMENSIONS}. "
            f"Missing: {self._EXPECTED_DIMENSIONS - dimensions_seen}. "
            f"Extra: {dimensions_seen - self._EXPECTED_DIMENSIONS}."
        )


class TestSaveArmSignature:
    """save_arm() must accept arm_type, dimension, dimension_value and
    write them to the fields dict. Legacy callers without these kwargs
    must continue to work unchanged."""

    def test_save_arm_accepts_transformation_kwargs(self):
        """The kwarg-only extension must not break legacy calls."""
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = []  # No existing arm — create path.

        save_arm(
            proxy=proxy,
            arm_id="transform__music_mood__cinematic",
            alpha=1.0,
            beta=1.0,
            niche_id="movies",
            arm_type="transformation",
            dimension="music_mood",
            dimension_value="cinematic",
        )

        # create() was called with the transformation kwargs baked into
        # the fields dict.
        proxy.create.assert_called_once()
        (fields,) = proxy.create.call_args[0]
        assert fields["arm_id"] == "transform__music_mood__cinematic"
        assert fields["niche_id"] == "movies"
        assert fields["arm_type"] == "transformation"
        assert fields["dimension"] == "music_mood"
        assert fields["dimension_value"] == "cinematic"
        assert fields["alpha"] == 1.0
        assert fields["beta"] == 1.0

    def test_save_arm_legacy_call_omits_new_fields(self):
        """Legacy callers (no arm_type kwarg) must not have arm_type
        show up in the update payload — otherwise every legacy update
        would overwrite arm_type=NULL on rows that shouldn't be
        touched by this call."""
        from genlab_core.learning.arm_loader import save_arm

        proxy = MagicMock()
        proxy.all.return_value = []  # No existing arm.

        # Legacy call — content_type based arm.
        save_arm(
            proxy=proxy,
            arm_id="gameplay_clip__instagram",
            alpha=1.5,
            beta=1.2,
            content_type="gameplay_clip",
            platform="instagram",
            niche_id="gaming",
        )

        proxy.create.assert_called_once()
        (fields,) = proxy.create.call_args[0]
        # These transformation-only fields must NOT be in the payload
        # when caller didn't pass them.
        assert "arm_type" not in fields
        assert "dimension" not in fields
        assert "dimension_value" not in fields


class TestRegisterArmsForNiche:
    """The high-level registration function orchestrates enumeration
    + save_arm calls. Test the dry-run path (no DB) and the mocked
    live path."""

    def test_dry_run_enumerates_without_writing(
        self, script_module, monkeypatch, tmp_path
    ):
        # Point script at a synthetic visuals.yaml.
        fake_yaml = tmp_path / "visuals.yaml"
        fake_yaml.write_text(
            "intelligent_transform:\n"
            "  enabled: false\n"
            "  dimensions:\n"
            "    music_mood:\n"
            "      enabled: true\n"
            "      moods: [cinematic, hype]\n"
            "    caption_style:\n"
            "      enabled: true\n"
            "      styles: [word_by_word]\n"
        )

        # Point NICHE_VISUALS_YAML at our synthetic file.
        monkeypatch.setitem(
            script_module.NICHE_VISUALS_YAML,
            "gaming",
            str(fake_yaml.relative_to(tmp_path)),
        )
        stats = script_module.register_arms_for_niche(
            "gaming", tmp_path, dry_run=True
        )
        assert stats["dry_run"] is True
        assert stats["total"] == 3  # 2 moods + 1 style
        assert stats["registered"] == 0  # dry run doesn't write
        assert stats["skipped"] == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
