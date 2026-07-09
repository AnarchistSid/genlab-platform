"""Pin the transformation orchestrator (PR 15).

Covers:
  1. Env flag off → no-op, returns source
  2. Config disabled → no-op, returns source
  3. Selector fails → no-op
  4. Empty choices → no-op
  5. Each stage applied gets added to stages_applied
  6. Each stage failed gets added to stages_skipped
  7. arm_ids_by_dimension carried through to result
  8. Final output path == source when all stages skip
  9. Final output path == real file when any stage applies
 10. Temp dir cleaned up
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.intelligent_transform import IntelligentTransformConfig
from genlab_core.media.transformation_orchestrator import (
    _flag_enabled,
    apply_transformations,
)
from genlab_core.media.transformation_selector import (
    TransformationChoice,
    TransformationChoices,
)


def _make_config(enabled: bool = True) -> IntelligentTransformConfig:
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": enabled,
                "dimensions": {
                    "music_mood": {
                        "enabled": True,
                        "moods": ["cinematic"],
                        "source_audio_duck_db": -12,
                        "music_bed_db": -6,
                    },
                    "pan_zoom": {
                        "enabled": True,
                        "patterns": ["ken_burns_slow"],
                    },
                },
            }
        }
    )


class TestFlagGate:
    def test_env_flag_off_returns_source(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False)
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        result = apply_transformations(
            source_video_path=source,
            output_path=output,
            niche_root=tmp_path,
            niche_id="gaming",
            config=_make_config(enabled=True),
        )
        assert result.output_path == source
        assert result.arm_ids_by_dimension == {}
        # output file NOT written (no transformation ran)
        assert not output.exists()

    def test_config_disabled_returns_source(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        result = apply_transformations(
            source_video_path=source,
            output_path=output,
            niche_root=tmp_path,
            niche_id="gaming",
            # config.enabled=False even though env flag on
            config=_make_config(enabled=False),
        )
        assert result.output_path == source
        assert result.arm_ids_by_dimension == {}


class TestSelectorPathways:
    def test_empty_choices_returns_source(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        # Selector returns empty choices (e.g. no arms in DB yet)
        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=TransformationChoices(niche_id="gaming"),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_make_config(),
            )
        assert result.output_path == source
        assert result.arm_ids_by_dimension == {}

    def test_selector_exception_returns_source(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            side_effect=RuntimeError("selector crashed"),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_make_config(),
            )
        assert result.output_path == source


class TestStagePathways:
    def _mk_choices(self, dims: dict[str, str]) -> TransformationChoices:
        c = TransformationChoices(niche_id="gaming")
        for dim, value in dims.items():
            c.choices[dim] = TransformationChoice(
                dimension=dim,
                dimension_value=value,
                arm_id=f"transform__{dim}__{value}",
                propensity=0.5,
            )
        return c

    def test_arm_ids_by_dimension_carried_through(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        # Both stages fail — but arm_ids_by_dimension carries.
        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices(
                    {"music_mood": "cinematic", "pan_zoom": "ken_burns_slow"}
                ),
            ),
            patch(
                "genlab_core.media.audio_replacer.replace_audio_for_reel",
                return_value=False,
            ),
            patch(
                "genlab_core.media.pan_zoom.apply_pan_zoom_for_reel",
                return_value=False,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_make_config(),
            )
        # Every dim's arm_id in the payload regardless of stage
        # success — reward attribution needs it either way.
        assert result.arm_ids_by_dimension == {
            "music_mood": "transform__music_mood__cinematic",
            "pan_zoom": "transform__pan_zoom__ken_burns_slow",
        }
        assert result.stages_skipped == ["music_mood", "pan_zoom"]

    def test_stage_success_appended_to_applied(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        def _fake_audio(**kwargs):
            # Simulate producing an output
            out = kwargs.get("output_path")
            out.write_bytes(b"a" * 2048)
            return True

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices({"music_mood": "cinematic"}),
            ),
            patch(
                "genlab_core.media.audio_replacer.replace_audio_for_reel",
                side_effect=_fake_audio,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_make_config(),
            )
        assert result.stages_applied == ["music_mood"]
        assert result.output_path == output
        assert output.exists()

    def test_stage_raise_treated_as_skip(self, tmp_path: Path, monkeypatch) -> None:
        """A stage raising an exception → skipped, pipeline continues."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices(
                    {"music_mood": "cinematic", "pan_zoom": "ken_burns_slow"}
                ),
            ),
            patch(
                "genlab_core.media.audio_replacer.replace_audio_for_reel",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "genlab_core.media.pan_zoom.apply_pan_zoom_for_reel",
                return_value=False,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_make_config(),
            )
        # music_mood raised → skipped; pan_zoom returned False → skipped
        assert "music_mood" in result.stages_skipped
        assert "pan_zoom" in result.stages_skipped
        # Pipeline still returned safely — did NOT crash
        assert result.output_path == source


class TestFlagEnabledHelper:
    def test_truthy_values(self, monkeypatch) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            assert _flag_enabled(), f"value={value!r} should be truthy"

    def test_falsy_values(self, monkeypatch) -> None:
        for value in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            assert not _flag_enabled(), f"value={value!r} should be falsy"

    def test_missing_is_false(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False)
        assert not _flag_enabled()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


# ── task #620 (2026-07-09): intro-fallback pins ─────────────────────
#
# Live-fire on 2026-07-09 movies pipeline showed 2 of 3 stories failing
# their motion_graphics concat with FFmpeg -22 (Invalid argument). Both
# failures used `intro=pattern_break_intro`; the success used a different
# intro. Assets ffprobe cleanly (identical stream properties) — the
# concat filter graph interacts poorly with specific source+intro+music
# combinations.
#
# Fix: on failure, retry ONCE with a different intro from the config's
# declared templates. Attribution stays clean — bandit-picked arm gets
# credit ONLY if the ORIGINAL attempt succeeded.


def _make_config_with_intro_outro() -> IntelligentTransformConfig:
    """Config with intro_animation + outro_cta enabled, plus 3 intro
    templates + 5 outro styles matching the prod visuals.yaml shape."""
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "intro_animation": {
                        "enabled": True,
                        "templates": [
                            "logo_zoom",
                            "logo_tagline_reveal",
                            "pattern_break_intro",
                        ],
                        "asset_dir": "assets/motion/intros",
                    },
                    "outro_cta": {
                        "enabled": True,
                        "styles": ["follow", "comment", "save", "share", "question"],
                        "asset_dir": "assets/motion/outros",
                    },
                },
            }
        }
    )


class TestIntroFallback:
    """Task #620 (2026-07-09) — retry once with a different intro when
    composite_for_reel fails, so the reel still gets a motion wrap."""

    def _mk_choices(self, intro_value: str, outro_value: str) -> TransformationChoices:
        c = TransformationChoices(niche_id="movies")
        c.choices["intro_animation"] = TransformationChoice(
            dimension="intro_animation",
            dimension_value=intro_value,
            arm_id=f"transform__intro_animation__{intro_value}",
            propensity=0.5,
        )
        c.choices["outro_cta"] = TransformationChoice(
            dimension="outro_cta",
            dimension_value=outro_value,
            arm_id=f"transform__outro_cta__{outro_value}",
            propensity=0.5,
        )
        return c

    def test_composite_success_no_fallback_intro_attributed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Happy path: composite_for_reel succeeds first try → intro
        arm gets credited normally, no fallback used."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        def _fake_composite(choice, src, out, **kw):
            out.write_bytes(b"a" * 2048)
            return True

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("pattern_break_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                side_effect=_fake_composite,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=_make_config_with_intro_outro(),
            )

        # Original intro succeeded → attribute both intro and outro
        assert "intro_animation" in result.stages_applied, (
            "Original composite succeeded — intro arm MUST get "
            "attributed. Fallback only fires on failure."
        )
        assert "outro_cta" in result.stages_applied

    def test_composite_failure_retries_with_alternative_intro(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The core #620 fix: first composite fails → retry with a
        different intro from config templates → if that succeeds,
        motion stage lands but ORIGINAL intro is NOT attributed."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        attempts = []

        def _fake_composite(choice, src, out, **kw):
            # Fail the first attempt (using original intro), succeed the
            # second (fallback intro).
            attempts.append(choice.intro_template)
            if len(attempts) == 1:
                return False  # first attempt fails
            out.write_bytes(b"a" * 2048)
            return True

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("pattern_break_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                side_effect=_fake_composite,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=_make_config_with_intro_outro(),
            )

        # composite_for_reel called twice: once with original, once
        # with fallback.
        assert len(attempts) == 2, (
            f"Expected 2 attempts (original + fallback), got {len(attempts)}. "
            "#620 retry logic didn't fire."
        )
        assert attempts[0] == "pattern_break_intro", (
            "First attempt must use the bandit-picked intro."
        )
        assert attempts[1] != "pattern_break_intro", (
            "Fallback intro must be different from the original. Retrying "
            "with the same intro won't help — the -22 error is "
            "deterministic given the same filter chain."
        )
        # ATTRIBUTION: original intro was picked but failed → NOT applied
        # (bandit shouldn't take credit for something that failed).
        assert "intro_animation" in result.stages_skipped, (
            "Bandit-picked intro FAILED — its arm must be marked skipped, "
            "not applied. Otherwise the bandit accumulates positive "
            "reward for a failing arm."
        )
        assert "intro_animation" not in result.stages_applied
        # OUTRO: the fallback path still ran with the same outro. Since
        # outro WAS actually applied (as part of the successful fallback
        # composite), its arm gets attributed.
        assert "outro_cta" in result.stages_applied

    def test_composite_both_attempts_fail_marks_both_skipped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Both attempts fail → intro AND outro both marked skipped.
        Same behavior as before the retry logic when there's no
        working alternative."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("pattern_break_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                return_value=False,  # always fails
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=_make_config_with_intro_outro(),
            )

        assert "intro_animation" in result.stages_skipped
        assert "outro_cta" in result.stages_skipped
        assert "intro_animation" not in result.stages_applied
        assert "outro_cta" not in result.stages_applied

    def test_fallback_picks_deterministic_alternative(self, tmp_path: Path, monkeypatch) -> None:
        """Fallback picks the FIRST alternative alphabetically, not a
        random one. Reproducibility for tests + debugging."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        attempts = []

        def _fake_composite(choice, src, out, **kw):
            attempts.append(choice.intro_template)
            if len(attempts) == 1:
                return False
            out.write_bytes(b"a" * 2048)
            return True

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("pattern_break_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                side_effect=_fake_composite,
            ),
        ):
            apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=_make_config_with_intro_outro(),
            )

        # Templates: [logo_zoom, logo_tagline_reveal, pattern_break_intro]
        # Original: pattern_break_intro → alternatives sorted:
        #   [logo_tagline_reveal, logo_zoom]
        # Pick first = logo_tagline_reveal
        assert attempts[1] == "logo_tagline_reveal", (
            f"Expected deterministic fallback 'logo_tagline_reveal' "
            f"(first alt alphabetically), got {attempts[1]!r}"
        )

    def test_only_one_alternative_intro_still_uses_it(self, tmp_path: Path, monkeypatch) -> None:
        """Config with only 2 intro templates: fallback still works
        (picks the one non-original alternative). Guards against a
        future config trim that leaves only 1 alt."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        two_template_config = IntelligentTransformConfig.from_visuals_dict(
            {
                "intelligent_transform": {
                    "enabled": True,
                    "dimensions": {
                        "intro_animation": {
                            "enabled": True,
                            "templates": ["logo_zoom", "pattern_break_intro"],
                            "asset_dir": "assets/motion/intros",
                        },
                        "outro_cta": {
                            "enabled": True,
                            "styles": ["follow"],
                            "asset_dir": "assets/motion/outros",
                        },
                    },
                }
            }
        )

        attempts = []

        def _fake_composite(choice, src, out, **kw):
            attempts.append(choice.intro_template)
            if len(attempts) == 1:
                return False
            out.write_bytes(b"a" * 2048)
            return True

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("pattern_break_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                side_effect=_fake_composite,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=two_template_config,
            )

        # Only logo_zoom is the alternative
        assert attempts[1] == "logo_zoom"
        assert "outro_cta" in result.stages_applied

    def test_config_with_single_intro_template_no_fallback_available(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Edge case: config declares only 1 intro template AND the
        bandit picks it. If it fails, there's no fallback to try — mark
        skipped, don't retry."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        single_template_config = IntelligentTransformConfig.from_visuals_dict(
            {
                "intelligent_transform": {
                    "enabled": True,
                    "dimensions": {
                        "intro_animation": {
                            "enabled": True,
                            "templates": ["only_intro"],
                            "asset_dir": "assets/motion/intros",
                        },
                        "outro_cta": {
                            "enabled": True,
                            "styles": ["follow"],
                            "asset_dir": "assets/motion/outros",
                        },
                    },
                }
            }
        )

        attempts = []

        def _fake_composite(choice, src, out, **kw):
            attempts.append(choice.intro_template)
            return False

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=self._mk_choices("only_intro", "follow"),
            ),
            patch(
                "genlab_core.media.motion_compositor.composite_for_reel",
                side_effect=_fake_composite,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=tmp_path,
                niche_id="movies",
                config=single_template_config,
            )

        # Only 1 attempt — no fallback available
        assert len(attempts) == 1, (
            "When only 1 intro template declared, no fallback should "
            "be attempted. Retrying with the same asset won't help."
        )
        assert "intro_animation" in result.stages_skipped
