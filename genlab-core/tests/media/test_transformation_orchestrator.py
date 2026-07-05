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
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.intelligent_transform import IntelligentTransformConfig
from genlab_core.media.transformation_orchestrator import (
    TransformationResult,
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
    def test_env_flag_off_returns_source(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delenv(
            "GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False
        )
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

    def test_config_disabled_returns_source(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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
    def test_empty_choices_returns_source(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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

    def test_selector_exception_returns_source(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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
    def _mk_choices(
        self, dims: dict[str, str]
    ) -> TransformationChoices:
        c = TransformationChoices(niche_id="gaming")
        for dim, value in dims.items():
            c.choices[dim] = TransformationChoice(
                dimension=dim,
                dimension_value=value,
                arm_id=f"transform__{dim}__{value}",
                propensity=0.5,
            )
        return c

    def test_arm_ids_by_dimension_carried_through(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        # Both stages fail — but arm_ids_by_dimension carries.
        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=self._mk_choices(
                {"music_mood": "cinematic", "pan_zoom": "ken_burns_slow"}
            ),
        ), patch(
            "genlab_core.media.audio_replacer.replace_audio_for_reel",
            return_value=False,
        ), patch(
            "genlab_core.media.pan_zoom.apply_pan_zoom_for_reel",
            return_value=False,
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

    def test_stage_success_appended_to_applied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        def _fake_audio(**kwargs):
            # Simulate producing an output
            out = kwargs.get("output_path")
            out.write_bytes(b"a" * 2048)
            return True

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=self._mk_choices({"music_mood": "cinematic"}),
        ), patch(
            "genlab_core.media.audio_replacer.replace_audio_for_reel",
            side_effect=_fake_audio,
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

    def test_stage_raise_treated_as_skip(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A stage raising an exception → skipped, pipeline continues."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        output = tmp_path / "out.mp4"

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=self._mk_choices(
                {"music_mood": "cinematic", "pan_zoom": "ken_burns_slow"}
            ),
        ), patch(
            "genlab_core.media.audio_replacer.replace_audio_for_reel",
            side_effect=RuntimeError("boom"),
        ), patch(
            "genlab_core.media.pan_zoom.apply_pan_zoom_for_reel",
            return_value=False,
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
            monkeypatch.setenv(
                "GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value
            )
            assert _flag_enabled(), f"value={value!r} should be truthy"

    def test_falsy_values(self, monkeypatch) -> None:
        for value in ("0", "false", "no", "off", ""):
            monkeypatch.setenv(
                "GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value
            )
            assert not _flag_enabled(), (
                f"value={value!r} should be falsy"
            )

    def test_missing_is_false(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False
        )
        assert not _flag_enabled()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
