"""Tests for R1: Video Quality Pipeline.

Tests cover:
  1. _get_encode_args() — platform-aware encode arg generation
  2. load_platform_encode_overrides() — YAML config override loading
  3. VideoCompositor integration — platform param flows through render
  4. ValidateVideos VMAF gate — re-encode on VMAF < 85, reject on second failure
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, mock_open, patch

import pytest

from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform, RenderSpec


# ══════════════════════════════════════════════════════════════════════════════
# 1. _get_encode_args() tests
# ══════════════════════════════════════════════════════════════════════════════


class TestGetEncodeArgs:
    """_get_encode_args returns platform-specific FFmpeg output args."""

    def test_instagram_uses_libx264(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("instagram")
        assert "-c:v" in args
        idx = args.index("-c:v")
        assert args[idx + 1] == "libx264"

    def test_youtube_uses_libx265(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("youtube")
        idx = args.index("-c:v")
        assert args[idx + 1] == "libx265"

    def test_tiktok_has_maxrate(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("tiktok")
        assert "-maxrate" in args
        idx = args.index("-maxrate")
        assert args[idx + 1] == "12M"

    def test_default_falls_back_to_instagram(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        default_args = _get_encode_args()
        ig_args = _get_encode_args("instagram")
        assert default_args == ig_args

    def test_unknown_platform_falls_back_to_instagram(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("nonexistent_platform")
        ig_args = _get_encode_args("instagram")
        assert args == ig_args

    def test_always_includes_pix_fmt(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        for platform in ["instagram", "youtube", "tiktok", "facebook", "threads"]:
            args = _get_encode_args(platform)
            assert "-pix_fmt" in args, f"{platform} must include -pix_fmt"
            idx = args.index("-pix_fmt")
            assert args[idx + 1] == "yuv420p"

    def test_always_includes_movflags(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        for platform in ["instagram", "youtube", "tiktok"]:
            args = _get_encode_args(platform)
            assert "-movflags" in args, f"{platform} must include -movflags"
            idx = args.index("-movflags")
            assert args[idx + 1] == "+faststart"

    def test_youtube_includes_hvc1_tag(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("youtube")
        assert "-tag:v" in args
        idx = args.index("-tag:v")
        assert args[idx + 1] == "hvc1"

    def test_instagram_crf_matches_platform_spec(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        args = _get_encode_args("instagram")
        idx = args.index("-crf")
        crf_val = int(args[idx + 1])
        assert crf_val == PLATFORM_SPECS[Platform.INSTAGRAM].crf

    def test_all_known_platforms_produce_valid_args(self) -> None:
        from genlab_core.media.video_compositor import _get_encode_args

        for platform in ["instagram", "youtube", "tiktok", "facebook", "threads"]:
            args = _get_encode_args(platform)
            # Must have at minimum: codec, color space, audio, pix_fmt, movflags
            assert "-c:v" in args
            assert "-colorspace" in args
            assert "-c:a" in args
            assert "-pix_fmt" in args
            assert "-movflags" in args


# ══════════════════════════════════════════════════════════════════════════════
# 2. YAML config override loading tests
# ══════════════════════════════════════════════════════════════════════════════


class TestPlatformEncodeOverrides:
    """load_platform_encode_overrides merges YAML overrides into PLATFORM_SPECS."""

    def test_load_returns_dict_of_render_specs(self, tmp_path: Path) -> None:
        from genlab_core.media.video_compositor import load_platform_encode_overrides

        yaml_content = (
            "instagram:\n"
            "  crf: 12\n"
            "  preset: veryslow\n"
        )
        yaml_file = tmp_path / "platform_encode_specs.yaml"
        yaml_file.write_text(yaml_content)

        result = load_platform_encode_overrides(yaml_file)
        assert "instagram" in result
        assert isinstance(result["instagram"], RenderSpec)
        assert result["instagram"].crf == 12
        assert result["instagram"].preset == "veryslow"

    def test_override_preserves_non_overridden_fields(self, tmp_path: Path) -> None:
        from genlab_core.media.video_compositor import load_platform_encode_overrides

        yaml_content = "instagram:\n  crf: 10\n"
        yaml_file = tmp_path / "platform_encode_specs.yaml"
        yaml_file.write_text(yaml_content)

        result = load_platform_encode_overrides(yaml_file)
        ig_spec = result["instagram"]
        # crf overridden
        assert ig_spec.crf == 10
        # codec should remain from PLATFORM_SPECS default
        assert ig_spec.codec == PLATFORM_SPECS[Platform.INSTAGRAM].codec

    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        from genlab_core.media.video_compositor import load_platform_encode_overrides

        result = load_platform_encode_overrides(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_empty_yaml_returns_empty_dict(self, tmp_path: Path) -> None:
        from genlab_core.media.video_compositor import load_platform_encode_overrides

        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")

        result = load_platform_encode_overrides(yaml_file)
        assert result == {}

    def test_unknown_platform_key_ignored(self, tmp_path: Path) -> None:
        from genlab_core.media.video_compositor import load_platform_encode_overrides

        yaml_content = (
            "instagram:\n  crf: 12\n"
            "nonexistent_platform:\n  crf: 99\n"
        )
        yaml_file = tmp_path / "specs.yaml"
        yaml_file.write_text(yaml_content)

        result = load_platform_encode_overrides(yaml_file)
        assert "instagram" in result
        assert "nonexistent_platform" not in result


# ══════════════════════════════════════════════════════════════════════════════
# 3. VideoCompositor platform parameter integration
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def tmp_assets(tmp_path: Path) -> dict[str, Path]:
    """Create minimal fake assets for compositor tests."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 256)
    output = tmp_path / "output.mp4"
    return {"logo": logo, "clip": clip, "output": output}


class TestCompositorPlatformParam:
    """VideoCompositor.compose_vertical passes platform to _get_encode_args."""

    @patch("genlab_core.media.video_compositor.probe_video_metadata")
    @patch("genlab_core.media.video_compositor.run_ffmpeg")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_compose_vertical_default_platform_is_instagram(
        self,
        _mock_bin: MagicMock,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_assets: dict[str, Path],
    ) -> None:
        from genlab_core.media.video_compositor import VideoCompositor, VisualConfig

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        mock_probe.return_value = {"width": 120, "height": 60}

        cfg = VisualConfig(
            niche_id="test",
            logo_path=tmp_assets["logo"],
            accent_color="#FF0000",
        )
        comp = VideoCompositor(cfg)
        comp.compose_vertical(
            [tmp_assets["clip"]], "Hook text", tmp_assets["output"],
        )

        # The ffmpeg command should use instagram's codec (libx264)
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "libx264" in cmd_str

    @patch("genlab_core.media.video_compositor.probe_video_metadata")
    @patch("genlab_core.media.video_compositor.run_ffmpeg")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_compose_vertical_youtube_uses_libx265(
        self,
        _mock_bin: MagicMock,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_assets: dict[str, Path],
    ) -> None:
        from genlab_core.media.video_compositor import VideoCompositor, VisualConfig

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        mock_probe.return_value = {"width": 120, "height": 60}

        cfg = VisualConfig(
            niche_id="test",
            logo_path=tmp_assets["logo"],
            accent_color="#FF0000",
        )
        comp = VideoCompositor(cfg)
        comp.compose_vertical(
            [tmp_assets["clip"]], "Hook text", tmp_assets["output"],
            platform="youtube",
        )

        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "libx265" in cmd_str


# ══════════════════════════════════════════════════════════════════════════════
# 4. ValidateVideos VMAF gate tests
# ══════════════════════════════════════════════════════════════════════════════


def _make_probe_data(
    *,
    codec: str = "h264",
    width: int = 1080,
    height: int = 1920,
    pix_fmt: str = "yuv420p",
    color_space: str = "bt709",
    duration: float = 30.0,
    size: int = 50_000_000,
) -> Dict[str, Any]:
    """Helper to build ffprobe-like output."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": width,
                "height": height,
                "pix_fmt": pix_fmt,
                "color_space": color_space,
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {
            "duration": str(duration),
            "size": str(size),
        },
    }


class TestValidateVideosVMAFGate:
    """ValidateVideos runs VMAF checks by default and re-encodes on failure."""

    def _make_context(self, tmp_path: Path) -> Dict[str, Any]:
        """Build a minimal pipeline context with one renderable story."""
        video = tmp_path / "reel.mp4"
        video.write_bytes(b"\x00" * 256)
        master = tmp_path / "master.mkv"
        master.write_bytes(b"\x00" * 512)

        return {
            "stories": [
                {
                    "story_id": "test-1",
                    "media": {
                        "rendered_path": str(video),
                        "master_path": str(master),
                    },
                }
            ],
            "niche_config": {},
        }

    @patch("genlab_core.pipeline.stages.validate_videos.ValidateVideos._probe")
    def test_vmaf_pass_marks_valid(
        self, mock_probe: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        mock_probe.return_value = _make_probe_data()
        ctx = self._make_context(tmp_path)

        with patch(
            "genlab_core.pipeline.stages.validate_videos.check_vmaf",
            return_value=(True, 92.0),
        ):
            result = ValidateVideos().execute(ctx)

        story = result["stories"][0]
        assert story["media"]["video_validation"]["valid"] is True

    @patch("genlab_core.pipeline.stages.validate_videos.ValidateVideos._probe")
    def test_vmaf_fail_triggers_reencode(
        self, mock_probe: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        mock_probe.return_value = _make_probe_data()
        ctx = self._make_context(tmp_path)

        # First VMAF fails (score 70), re-encode succeeds, second VMAF passes
        vmaf_calls = iter([(False, 70.0), (True, 88.0)])

        with patch(
            "genlab_core.pipeline.stages.validate_videos.check_vmaf",
            side_effect=lambda m, v, p: next(vmaf_calls),
        ), patch(
            "genlab_core.pipeline.stages.validate_videos.ValidateVideos._vmaf_reencode",
            return_value=tmp_path / "reel_vmaf_fixed.mp4",
        ) as mock_reencode:
            result = ValidateVideos().execute(ctx)

        mock_reencode.assert_called_once()
        story = result["stories"][0]
        assert story["media"]["video_validation"]["valid"] is True
        assert story["media"]["video_validation"].get("vmaf_reencoded") is True

    @patch("genlab_core.pipeline.stages.validate_videos.ValidateVideos._probe")
    def test_vmaf_fail_twice_rejects(
        self, mock_probe: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        mock_probe.return_value = _make_probe_data()
        ctx = self._make_context(tmp_path)

        with patch(
            "genlab_core.pipeline.stages.validate_videos.check_vmaf",
            return_value=(False, 70.0),
        ), patch(
            "genlab_core.pipeline.stages.validate_videos.ValidateVideos._vmaf_reencode",
            return_value=tmp_path / "reel_vmaf_fixed.mp4",
        ):
            result = ValidateVideos().execute(ctx)

        story = result["stories"][0]
        assert story["media"]["video_validation"]["valid"] is False
        assert "vmaf_below_threshold" in story["media"]["video_validation"].get("issues", [])

    @patch("genlab_core.pipeline.stages.validate_videos.ValidateVideos._probe")
    def test_vmaf_reencode_failure_rejects(
        self, mock_probe: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        mock_probe.return_value = _make_probe_data()
        ctx = self._make_context(tmp_path)

        with patch(
            "genlab_core.pipeline.stages.validate_videos.check_vmaf",
            return_value=(False, 70.0),
        ), patch(
            "genlab_core.pipeline.stages.validate_videos.ValidateVideos._vmaf_reencode",
            return_value=None,
        ):
            result = ValidateVideos().execute(ctx)

        story = result["stories"][0]
        assert story["media"]["video_validation"]["valid"] is False

    @patch("genlab_core.pipeline.stages.validate_videos.ValidateVideos._probe")
    def test_vmaf_disabled_via_config(
        self, mock_probe: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        mock_probe.return_value = _make_probe_data()
        ctx = self._make_context(tmp_path)
        ctx["niche_config"]["video_validation"] = {"run_vmaf": False}

        # check_vmaf should NOT be called
        with patch(
            "genlab_core.pipeline.stages.validate_videos.check_vmaf",
        ) as mock_vmaf:
            result = ValidateVideos().execute(ctx)

        mock_vmaf.assert_not_called()
        story = result["stories"][0]
        assert story["media"]["video_validation"]["valid"] is True


class TestVMAFReencode:
    """_vmaf_reencode reduces CRF by 3 (minimum 12)."""

    @patch("genlab_core.pipeline.stages.validate_videos.get_ffmpeg_binary", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_reencode_reduces_crf_by_3(
        self, mock_run: MagicMock, _mock_bin: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        video = tmp_path / "reel.mp4"
        video.write_bytes(b"\x00" * 256)
        out = tmp_path / "reel_vmaf_fixed.mp4"

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        # Make the output file exist so the method returns it
        def create_output(*args, **kwargs):
            out.write_bytes(b"\x00" * 256)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = create_output

        result = ValidateVideos._vmaf_reencode(video, current_crf=18)
        assert result is not None

        cmd = mock_run.call_args[0][0]
        # CRF should be 18 - 3 = 15
        crf_idx = cmd.index("-crf")
        assert cmd[crf_idx + 1] == "15"

    @patch("genlab_core.pipeline.stages.validate_videos.get_ffmpeg_binary", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_reencode_minimum_crf_is_12(
        self, mock_run: MagicMock, _mock_bin: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        video = tmp_path / "reel.mp4"
        video.write_bytes(b"\x00" * 256)
        out = tmp_path / "reel_vmaf_fixed.mp4"

        def create_output(*args, **kwargs):
            out.write_bytes(b"\x00" * 256)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = create_output

        result = ValidateVideos._vmaf_reencode(video, current_crf=13)
        assert result is not None

        cmd = mock_run.call_args[0][0]
        crf_idx = cmd.index("-crf")
        # 13 - 3 = 10, but minimum is 12
        assert cmd[crf_idx + 1] == "12"

    @patch("genlab_core.pipeline.stages.validate_videos.get_ffmpeg_binary", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_reencode_returns_none_on_ffmpeg_failure(
        self, mock_run: MagicMock, _mock_bin: MagicMock, tmp_path: Path,
    ) -> None:
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        video = tmp_path / "reel.mp4"
        video.write_bytes(b"\x00" * 256)

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error",
        )

        result = ValidateVideos._vmaf_reencode(video, current_crf=18)
        assert result is None
