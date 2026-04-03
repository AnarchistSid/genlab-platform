"""Tests that post-encode video validation is wired into the render pipeline.

Sprint 1 finding: F-18.
Validates that:
  - validate_platform_variant() is called after each encoded output
  - Validation failure raises VideoValidationError
  - genlab-core validator: color space fails closed, VMAF fails open with warning
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Wiring tests: render_gaming_video calls validator
# ---------------------------------------------------------------------------

class TestSingleClipValidation:
    def test_validation_called_after_compositor_render(self):
        """validate_platform_variant must be called for each rendered variant."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        story = {
            "title": "Test",
            "media": {"clip": {"file_path": "/tmp/fake_clip.mp4"}},
            "content": {"hook": "Test hook"},
        }

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc), \
             patch.object(Path, "exists", return_value=True), \
             patch("genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")), \
             patch("niches.gaming.stages.render_gaming_video.validate_platform_variant", return_value=True) as mock_val:

            context = {"stories": [story], "feature_flags": {}, "niche_config": {}}
            result = stage.execute(context)

        mock_val.assert_called()
        assert result["stories"][0]["media"]["rendered_path"] is not None

    def test_validation_failure_raises(self):
        """If validation returns False, VideoValidationError is raised."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo, VideoValidationError

        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        story = {
            "title": "Bad Quality",
            "media": {"clip": {"file_path": "/tmp/fake_clip.mp4"}},
            "content": {"hook": "Test hook"},
        }

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc), \
             patch.object(Path, "exists", return_value=True), \
             patch("genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")), \
             patch("niches.gaming.stages.render_gaming_video.validate_platform_variant", return_value=False):

            context = {"stories": [story], "feature_flags": {}, "niche_config": {}}
            with pytest.raises(VideoValidationError):
                stage.execute(context)

    def test_validation_pass_preserves_rendered_path(self):
        """If validation returns True, rendered_path is preserved."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        story = {
            "title": "Good Quality",
            "media": {"clip": {"file_path": "/tmp/fake_clip.mp4"}},
            "content": {"hook": "Nice hook"},
        }

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc), \
             patch.object(Path, "exists", return_value=True), \
             patch("genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")), \
             patch("niches.gaming.stages.render_gaming_video.validate_platform_variant", return_value=True):

            context = {"stories": [story], "feature_flags": {}, "niche_config": {}}
            result = stage.execute(context)

        assert result["stories"][0]["media"]["rendered_path"] is not None
        assert result["run_stats"]["render"]["rendered"] == 1


class TestCompilationValidation:
    def test_compilation_validation_failure_raises(self):
        """When compilation fails validation, VideoValidationError is raised."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo, VideoValidationError

        stage = RenderGamingVideo()
        stage._compilation_rules = {
            "compilation_types": {
                "short_compilation": {
                    "min_clips": 3, "max_clips": 8,
                    "target_duration_seconds": 45,
                    "max_single_clip_ratio": 0.35,
                    "pacing": "escalating",
                    "min_commentary_lines": 3,
                }
            },
            "transitions": {"hard_cut": 0.6, "zoom_impact": 0.4},
        }
        stage._scoring_config = {}
        stage._overlay_styles = {}

        stories = [
            {
                "title": f"Game {i}",
                "clip_id": f"clip_{i}",
                "media": {"clip": {"file_path": "/tmp/fake.mp4"}},
                "content": {"hook": f"Hook {i}"},
                "final_score": 0.8 - i * 0.1,
                "game_title": f"Game{i}",
            }
            for i in range(4)
        ]

        with patch("niches.gaming.stages.render_gaming_video.normalize_clip", return_value=True), \
             patch("niches.gaming.stages.render_gaming_video.concat_with_transitions", return_value=True), \
             patch("niches.gaming.stages.render_gaming_video.add_text_overlay", return_value=True), \
             patch.object(Path, "exists", return_value=True), \
             patch("niches.gaming.stages.render_gaming_video.get_duration", return_value=10.0), \
             patch("niches.gaming.stages.render_gaming_video.compute_hash", return_value=None), \
             patch("niches.gaming.stages.render_gaming_video.validate_platform_variant", return_value=False):

            with pytest.raises(VideoValidationError):
                stage._render_compilation(stories, "test_run", {"rendered": 0}, {})


# ---------------------------------------------------------------------------
# genlab-core validator: fail-closed / fail-open behavior
# ---------------------------------------------------------------------------

class TestColorSpaceFailClosed:
    def test_returns_false_on_parse_error(self):
        """check_color_space returns False (not True) when ffprobe output is unparseable."""
        from genlab_core.media.video_validator import check_color_space

        mock_result = MagicMock()
        mock_result.stdout = "not json at all"

        with patch("genlab_core.media.ffmpeg.get_ffprobe_binary", return_value="ffprobe"), \
             patch("subprocess.run", return_value=mock_result):
            result = check_color_space(Path("/bad.mp4"), "instagram")

        assert result is False

    def test_returns_true_when_bt709(self):
        """check_color_space returns True when all streams are bt709."""
        from genlab_core.media.video_validator import check_color_space

        probe_output = json.dumps({
            "streams": [{
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
            }]
        })

        mock_result = MagicMock()
        mock_result.stdout = probe_output

        with patch("genlab_core.media.ffmpeg.get_ffprobe_binary", return_value="ffprobe"), \
             patch("subprocess.run", return_value=mock_result):
            result = check_color_space(Path("/good.mp4"), "instagram")

        assert result is True


class TestVmafFailOpen:
    def test_returns_true_on_parse_error_with_warning(self, caplog):
        """When VMAF output is unparseable, returns True (fail-open) with warning."""
        from genlab_core.media.video_validator import check_vmaf

        with patch("genlab_core.media.ffmpeg.get_ffmpeg_binary", return_value="ffmpeg"), \
             patch("subprocess.run"), \
             patch("builtins.open", side_effect=FileNotFoundError("no vmaf log")):
            with caplog.at_level(logging.WARNING):
                passed, score = check_vmaf(
                    Path("/master.mp4"), Path("/variant.mp4"), "instagram"
                )

        assert passed is True
        assert score == 0.0
        assert "VMAF check skipped" in caplog.text


# ---------------------------------------------------------------------------
# VideoValidationError is importable
# ---------------------------------------------------------------------------

class TestVideoValidationError:
    def test_importable(self):
        from niches.gaming.stages.render_gaming_video import VideoValidationError
        assert issubclass(VideoValidationError, RuntimeError)
