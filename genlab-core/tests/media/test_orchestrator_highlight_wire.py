"""Pin the highlight_moment wire — orchestrator Stage 0 trim
(Task #522 follow-up).

Verifies:
  1. Orchestrator calls detect_highlight when highlight_moment arm
     was chosen.
  2. When the arm picks 'first_frame' with window >= duration → skip
     the ffmpeg trim pass (no-op), but stages_applied still includes
     'highlight_moment' so arm attribution works.
  3. When the arm picks 'audio_peak' or 'motion_peak' and detector
     returns a non-baseline window, orchestrator runs the trim +
     updates video_duration_s for downstream stages.
  4. Trim failure → skipped, pipeline continues on untrimmed source.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.highlight_detector import HighlightWindow
from genlab_core.media.intelligent_transform import IntelligentTransformConfig
from genlab_core.media.transformation_orchestrator import apply_transformations
from genlab_core.media.transformation_selector import (
    TransformationChoice,
    TransformationChoices,
)


def _choices(method: str) -> TransformationChoices:
    c = TransformationChoices(niche_id="gaming")
    c.choices["highlight_moment"] = TransformationChoice(
        dimension="highlight_moment",
        dimension_value=method,
        arm_id=f"transform__highlight_moment__{method}",
        propensity=0.5,
    )
    return c


def _cfg() -> IntelligentTransformConfig:
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "highlight_moment": {
                        "enabled": True,
                        "methods": ["first_frame", "audio_peak"],
                        "window_seconds": 8,
                    },
                },
            }
        }
    )


class TestFirstFrameSkipsTrim:
    def test_first_frame_full_duration_no_trim(self, monkeypatch, tmp_path: Path) -> None:
        """When window covers full source (first_frame baseline),
        no ffmpeg trim runs — saves render time."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        # detect_highlight returns (0, 20) — full source
        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=_choices("first_frame"),
            ),
            patch(
                "genlab_core.media.highlight_detector.detect_highlight",
                return_value=HighlightWindow(0.0, 20.0),
            ),
            patch("subprocess.run") as mock_run,
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_cfg(),
                video_duration_s=20.0,
            )

        # Stage attributed (arm gets reward attribution)
        assert "highlight_moment" in result.stages_applied
        # But no ffmpeg trim was invoked
        mock_run.assert_not_called()


class TestAudioPeakRunsTrim:
    def test_audio_peak_window_triggers_trim(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        def _fake_ffmpeg(*args, **kwargs):
            # Simulate ffmpeg writing the trimmed output
            for arg in args[0]:
                if arg.endswith(".mp4") and not arg.endswith("source.mp4"):
                    Path(arg).write_bytes(b"trimmed" * 400)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=_choices("audio_peak"),
            ),
            patch(
                "genlab_core.media.highlight_detector.detect_highlight",
                return_value=HighlightWindow(5.0, 13.0),  # 8s window in middle
            ),
            patch(
                "genlab_core.media.ffmpeg.get_ffmpeg_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("subprocess.run", side_effect=_fake_ffmpeg) as mock_run,
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_cfg(),
                video_duration_s=20.0,
            )

        assert "highlight_moment" in result.stages_applied
        # ffmpeg trim was invoked
        assert mock_run.called
        # And the -ss / -t flags carry the window bounds
        call_argv = mock_run.call_args_list[0][0][0]
        ss_idx = call_argv.index("-ss")
        assert float(call_argv[ss_idx + 1]) == pytest.approx(5.0)
        t_idx = call_argv.index("-t")
        # Duration = end - start = 13 - 5 = 8
        assert float(call_argv[t_idx + 1]) == pytest.approx(8.0)


class TestTrimFailureFallsBackToSource:
    def test_trim_ffmpeg_error_marks_skipped(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=_choices("audio_peak"),
            ),
            patch(
                "genlab_core.media.highlight_detector.detect_highlight",
                return_value=HighlightWindow(5.0, 13.0),
            ),
            patch(
                "genlab_core.media.ffmpeg.get_ffmpeg_binary",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="fake error"
                ),
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_cfg(),
                video_duration_s=20.0,
            )

        assert "highlight_moment" in result.stages_skipped
        assert "highlight_moment" not in result.stages_applied

    def test_detector_returns_none_falls_back(self, monkeypatch, tmp_path: Path) -> None:
        """detect_highlight with fallback_to_first_frame=True should
        rarely return None, but if it does, orchestrator handles it."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        with (
            patch(
                "genlab_core.media.transformation_selector.select_transformation_dimensions",
                return_value=_choices("audio_peak"),
            ),
            patch(
                "genlab_core.media.highlight_detector.detect_highlight",
                return_value=None,
            ),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_cfg(),
                video_duration_s=20.0,
            )

        # None window → no trim needed (skip pass), arm still attributes
        assert "highlight_moment" in result.stages_applied
        assert "highlight_moment" not in result.stages_skipped


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
