"""Tests for genlab_core.media.ffmpeg_utils — probe, filter builders, constants.

Strategy: mock subprocess for unit tests. Tests requiring real FFmpeg
are marked with @pytest.mark.integration.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary, RenderSpec


# ── Binary discovery tests ────────────────────────────────────────


class TestGetFfmpegBinary:
    def setup_method(self):
        get_ffmpeg_binary.cache_clear()

    def teardown_method(self):
        get_ffmpeg_binary.cache_clear()

    def test_env_var_override(self):
        with patch.dict(os.environ, {"FFMPEG_BINARY": "/custom/ffmpeg"}):
            assert get_ffmpeg_binary() == "/custom/ffmpeg"

    def test_conda_path(self, tmp_path):
        conda_ffmpeg = tmp_path / "bin" / "ffmpeg"
        conda_ffmpeg.parent.mkdir(parents=True)
        conda_ffmpeg.touch()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FFMPEG_BINARY", None)
            with patch("genlab_core.media.ffmpeg.sys") as mock_sys:
                mock_sys.prefix = str(tmp_path)
                assert get_ffmpeg_binary() == str(conda_ffmpeg)

    def test_system_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FFMPEG_BINARY", None)
            with patch("genlab_core.media.ffmpeg.os.path.isfile", return_value=False):
                with patch("genlab_core.media.ffmpeg.shutil.which", return_value="/usr/local/bin/ffmpeg"):
                    assert get_ffmpeg_binary() == "/usr/local/bin/ffmpeg"

    def test_not_found_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FFMPEG_BINARY", None)
            with patch("genlab_core.media.ffmpeg.os.path.isfile", return_value=False):
                with patch("genlab_core.media.ffmpeg.shutil.which", return_value=None):
                    with pytest.raises(RuntimeError, match="FFmpeg not found"):
                        get_ffmpeg_binary()


class TestGetFfprobeBinary:
    def setup_method(self):
        get_ffprobe_binary.cache_clear()

    def teardown_method(self):
        get_ffprobe_binary.cache_clear()

    def test_env_var_override(self):
        with patch.dict(os.environ, {"FFPROBE_BINARY": "/custom/ffprobe"}):
            assert get_ffprobe_binary() == "/custom/ffprobe"


# ── RenderSpec tests ──────────────────────────────────────────────


class TestRenderSpec:
    def test_default_values(self):
        spec = RenderSpec()
        assert spec.codec == "libx264"
        assert spec.width == 1080
        assert spec.height == 1920
        assert spec.crf == 18

    def test_instagram_dimensions(self):
        from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform
        ig = PLATFORM_SPECS[Platform.INSTAGRAM]
        assert ig.width == 1080
        assert ig.height == 1920
        assert ig.fps == 30

    def test_to_output_args_produces_list(self):
        spec = RenderSpec()
        args = spec.to_output_args()
        assert isinstance(args, list)
        assert "-c:v" in args
        assert "libx264" in args

    def test_rejects_invalid_crf(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RenderSpec(crf=100)

    def test_crf_none_for_lossless(self):
        spec = RenderSpec(codec="ffv1", crf=None, preset=None)
        args = spec.to_output_args()
        assert "-crf" not in args


# ── Constants tests ───────────────────────────────────────────────


class TestConstants:
    def test_final_video_params_has_codec(self):
        from genlab_core.media.ffmpeg_utils import FINAL_VIDEO_PARAMS
        assert "-c:v" in FINAL_VIDEO_PARAMS
        assert "libx264" in FINAL_VIDEO_PARAMS

    def test_final_audio_params_has_codec(self):
        from genlab_core.media.ffmpeg_utils import FINAL_AUDIO_PARAMS
        assert "-c:a" in FINAL_AUDIO_PARAMS
        assert "aac" in FINAL_AUDIO_PARAMS

    def test_reel_dimensions(self):
        from genlab_core.media.ffmpeg_utils import REEL_WIDTH, REEL_HEIGHT, REEL_FPS
        assert REEL_WIDTH == 1080
        assert REEL_HEIGHT == 1920
        assert REEL_FPS == 30

    def test_landscape_dimensions(self):
        from genlab_core.media.ffmpeg_utils import LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT
        assert LANDSCAPE_WIDTH == 1920
        assert LANDSCAPE_HEIGHT == 1080


# ── Probe function tests (mocked) ─────────────────────────────────


class TestProbeVideoMetadata:
    def test_returns_metadata_dict(self):
        from genlab_core.media.ffmpeg_utils import probe_video_metadata

        fake_output = json.dumps({
            "format": {"duration": "10.5", "format_name": "mp4"},
            "streams": [{
                "codec_type": "video",
                "width": 1920, "height": 1080,
                "r_frame_rate": "30/1",
                "codec_name": "h264",
            }],
        })

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", return_value="ffprobe"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output,
                )
                meta = probe_video_metadata("/fake/video.mp4")

        assert meta["duration"] == 10.5
        assert meta["width"] == 1920
        assert meta["height"] == 1080
        assert meta["fps"] == 30.0
        assert meta["codec"] == "h264"

    def test_returns_empty_on_failure(self):
        from genlab_core.media.ffmpeg_utils import probe_video_metadata

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", return_value="ffprobe"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                assert probe_video_metadata("/fake.mp4") == {}


class TestProbeDuration:
    def test_returns_duration(self):
        from genlab_core.media.ffmpeg_utils import probe_video_duration

        fake_output = json.dumps({"format": {"duration": "42.5"}})

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", return_value="ffprobe"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output,
                )
                assert probe_video_duration("/fake.mp4") == 42.5

    def test_returns_zero_on_error(self):
        from genlab_core.media.ffmpeg_utils import probe_video_duration

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", side_effect=RuntimeError):
            assert probe_video_duration("/fake.mp4") == 0.0


class TestProbeMedia:
    def test_returns_structured_dict(self):
        from genlab_core.media.ffmpeg_utils import probe_media

        fake_output = json.dumps({
            "format": {"duration": "15.0", "size": "5000000"},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1080, "height": 1920,
                    "r_frame_rate": "60/1",
                    "codec_name": "h264",
                    "duration": "15.0",
                },
                {
                    "codec_type": "audio",
                    "sample_rate": "48000",
                    "channels": 2,
                    "codec_name": "aac",
                },
            ],
        })

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", return_value="ffprobe"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output,
                )
                info = probe_media("/fake/video.mp4")

        assert info["video"]["width"] == 1080
        assert info["video"]["height"] == 1920
        assert info["video"]["fps"] == 60.0
        assert info["audio"]["sample_rate"] == 48000
        assert info["audio"]["channels"] == 2
        assert info["duration"] == 15.0

    def test_returns_none_on_failure(self):
        from genlab_core.media.ffmpeg_utils import probe_media

        with patch("genlab_core.media.ffmpeg_utils.get_ffprobe_binary", return_value="ffprobe"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr="error")
                assert probe_media("/fake.mp4") is None


# ── Text escaping tests ───────────────────────────────────────────


class TestEscapeDrawtext:
    def test_escapes_colon(self):
        from genlab_core.media.ffmpeg_utils import escape_drawtext
        assert "\\:" in escape_drawtext("hello:world")

    def test_escapes_semicolon(self):
        from genlab_core.media.ffmpeg_utils import escape_drawtext
        assert "\\;" in escape_drawtext("a;b")

    def test_escapes_brackets(self):
        from genlab_core.media.ffmpeg_utils import escape_drawtext
        result = escape_drawtext("[test]")
        assert "\\[" in result
        assert "\\]" in result

    def test_empty_string(self):
        from genlab_core.media.ffmpeg_utils import escape_drawtext
        assert escape_drawtext("") == ""


# ── Filter builder tests ─────────────────────────────────────────


class TestBuildScaleCrop:
    def test_same_aspect_ratio(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop
        result = build_scale_crop(1080, 1920, 1080, 1920)
        assert "scale=1080:1920" in result

    def test_wider_source_crops_width(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop
        result = build_scale_crop(1920, 1080, 1080, 1920)
        assert "crop=" in result

    def test_taller_source_crops_height(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop
        result = build_scale_crop(720, 1280, 1080, 1920)
        assert "scale=1080:1920" in result

    def test_pillarbox_mode(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop
        result = build_scale_crop(1920, 1080, 1080, 1920, mode="pillarbox")
        assert "boxblur" in result
        assert "overlay" in result


class TestPositionToXY:
    def test_center(self):
        from genlab_core.media.ffmpeg_utils import position_to_xy
        x, y = position_to_xy("center")
        assert "text_w" in x
        assert "text_h" in y

    def test_bottom_left(self):
        from genlab_core.media.ffmpeg_utils import position_to_xy
        x, y = position_to_xy("bottom_left", margin_x=40, margin_y=120)
        assert x == "40"
        assert "120" in y

    def test_unknown_position(self):
        from genlab_core.media.ffmpeg_utils import position_to_xy
        assert position_to_xy("invalid") == ("0", "0")


class TestBuildDrawtext:
    def test_basic_drawtext(self):
        from genlab_core.media.ffmpeg_utils import build_drawtext
        result = build_drawtext(
            text="Hello",
            font_file="/fonts/test.ttf",
            size=48,
            color="white",
            x="10", y="20",
            start=0.0, end=5.0,
        )
        assert "drawtext=" in result
        assert "Hello" in result
        assert "fontsize=48" in result

    def test_with_stroke(self):
        from genlab_core.media.ffmpeg_utils import build_drawtext
        result = build_drawtext(
            text="Test",
            font_file="/fonts/test.ttf",
            size=32, color="white",
            x="0", y="0",
            start=0.0, end=3.0,
            stroke_color="black",
            stroke_width=2,
        )
        assert "bordercolor=black" in result
        assert "borderw=2" in result


class TestBuildLoudnormFilter:
    def test_default(self):
        from genlab_core.media.ffmpeg_utils import build_loudnorm_filter
        result = build_loudnorm_filter()
        assert "loudnorm" in result
        assert "I=-14" in result

    def test_custom_target(self):
        from genlab_core.media.ffmpeg_utils import build_loudnorm_filter
        result = build_loudnorm_filter(target_i=-16.0)
        assert "I=-16" in result


# ── Convenience wrapper tests ─────────────────────────────────────


class TestConvenienceWrappers:
    def test_get_duration_delegates(self):
        from genlab_core.media.ffmpeg_utils import get_duration
        with patch("genlab_core.media.ffmpeg_utils.probe_video_duration", return_value=30.0):
            assert get_duration("/fake.mp4") == 30.0

    def test_get_resolution_delegates(self):
        from genlab_core.media.ffmpeg_utils import get_resolution
        fake_info = {
            "video": {"width": 1920, "height": 1080, "fps": 30.0, "codec": "h264", "duration": 10.0},
            "audio": {"sample_rate": 48000, "channels": 2, "codec": "aac"},
            "duration": 10.0,
            "file_size_bytes": 1000,
        }
        with patch("genlab_core.media.ffmpeg_utils.probe_media", return_value=fake_info):
            assert get_resolution("/fake.mp4") == (1920, 1080)

    def test_get_fps_delegates(self):
        from genlab_core.media.ffmpeg_utils import get_fps
        fake_info = {
            "video": {"width": 1920, "height": 1080, "fps": 60.0, "codec": "h264", "duration": 10.0},
            "audio": {"sample_rate": 48000, "channels": 2, "codec": "aac"},
            "duration": 10.0,
            "file_size_bytes": 1000,
        }
        with patch("genlab_core.media.ffmpeg_utils.probe_media", return_value=fake_info):
            assert get_fps("/fake.mp4") == 60.0
