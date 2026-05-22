"""Tests for genlab_core.media.ffmpeg."""

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.ffmpeg import (
    MASTER_SPEC,
    PLATFORM_SPECS,
    HWAccel,
    Platform,
    RenderSpec,
    _apply_hw_accel,
    detect_hw_accel,
    get_ffmpeg_binary,
    get_ffprobe_binary,
    resolve_twitter_spec,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# RenderSpec.to_output_args()
# ---------------------------------------------------------------------------


class TestRenderSpecOutputArgs:
    def test_default_args_include_codec(self):
        spec = RenderSpec()
        args = spec.to_output_args()
        assert args[0:2] == ["-c:v", "libx264"]

    def test_crf_and_preset_included(self):
        spec = RenderSpec(crf=18, preset="slow")
        args = spec.to_output_args()
        assert "-crf" in args
        assert args[args.index("-crf") + 1] == "18"
        assert "-preset" in args
        assert args[args.index("-preset") + 1] == "slow"

    def test_no_crf_for_lossless(self):
        spec = RenderSpec(codec="ffv1", crf=None, preset=None)
        args = spec.to_output_args()
        assert "-crf" not in args
        assert "-preset" not in args

    def test_source_fps_omits_rate_flag(self):
        spec = RenderSpec(fps="source")
        args = spec.to_output_args()
        assert "-r" not in args

    def test_numeric_fps_included(self):
        spec = RenderSpec(fps=30)
        args = spec.to_output_args()
        assert "-r" in args
        assert args[args.index("-r") + 1] == "30"

    def test_maxrate_and_bufsize(self):
        spec = RenderSpec(maxrate="12M", bufsize="24M")
        args = spec.to_output_args()
        assert "-maxrate" in args
        assert args[args.index("-maxrate") + 1] == "12M"
        assert "-bufsize" in args
        assert args[args.index("-bufsize") + 1] == "24M"

    def test_h265_adds_hvc1_tag(self):
        spec = RenderSpec(codec="libx265")
        args = spec.to_output_args()
        assert "-tag:v" in args
        assert args[args.index("-tag:v") + 1] == "hvc1"

    def test_h264_no_hvc1_tag(self):
        spec = RenderSpec(codec="libx264")
        args = spec.to_output_args()
        assert "-tag:v" not in args

    def test_bt709_color_space_always_present(self):
        spec = RenderSpec()
        args = spec.to_output_args()
        assert "-colorspace" in args
        assert args[args.index("-colorspace") + 1] == "bt709"
        assert "-color_primaries" in args
        assert "-color_trc" in args

    def test_audio_args_present(self):
        spec = RenderSpec(audio_codec="aac", audio_bitrate="256k")
        args = spec.to_output_args()
        assert "-c:a" in args
        assert args[args.index("-c:a") + 1] == "aac"
        assert "-b:a" in args
        assert args[args.index("-b:a") + 1] == "256k"

    def test_safe_zone_padding(self):
        spec = RenderSpec(
            height=1920,
            safe_zone_top_pct=0.14,
            safe_zone_bottom_pct=0.35,
        )
        args = spec.to_output_args()
        vf_idx = args.index("-vf")
        vf_val = args[vf_idx + 1]
        assert "pad=" in vf_val

    def test_no_safe_zone_no_pad(self):
        spec = RenderSpec()
        args = spec.to_output_args()
        vf_idx = args.index("-vf")
        vf_val = args[vf_idx + 1]
        assert "pad=" not in vf_val


# ---------------------------------------------------------------------------
# RenderSpec validation
# ---------------------------------------------------------------------------


class TestRenderSpecValidation:
    def test_invalid_crf_too_high(self):
        with pytest.raises(ValidationError):
            RenderSpec(crf=64)

    def test_invalid_crf_negative(self):
        with pytest.raises(ValidationError):
            RenderSpec(crf=-1)

    def test_valid_crf_zero(self):
        spec = RenderSpec(crf=0)
        assert spec.crf == 0

    def test_valid_crf_63(self):
        spec = RenderSpec(crf=63)
        assert spec.crf == 63

    def test_crf_none_is_valid(self):
        spec = RenderSpec(crf=None)
        assert spec.crf is None


# ---------------------------------------------------------------------------
# PLATFORM_SPECS constants
# ---------------------------------------------------------------------------


class TestPlatformSpecs:
    def test_youtube_codec(self):
        # 2026-05-21: deliberately moved YouTube from libx265 → libx264.
        # x265 OOMs on the 4GB Hetzner VPS for 60s reels, dropping into
        # the uncapped-bitrate fallback that platforms silently reject.
        # YouTube re-encodes incoming video anyway, so x264 is safe.
        assert PLATFORM_SPECS[Platform.YOUTUBE].codec == "libx264"

    def test_instagram_crf(self):
        # crf retuned 15 → 22 alongside the preset=fast change for VPS
        # encode-time budget. Instagram re-encodes on ingest.
        assert PLATFORM_SPECS[Platform.INSTAGRAM].crf == 22

    def test_x_std_width_720(self):
        assert PLATFORM_SPECS[Platform.X_STD].width == 720

    def test_x_std_height_1280(self):
        assert PLATFORM_SPECS[Platform.X_STD].height == 1280

    def test_tiktok_has_maxrate(self):
        # maxrate retuned 12M → 6M for the VPS encode budget.
        assert PLATFORM_SPECS[Platform.TIKTOK].maxrate == "6M"

    def test_facebook_has_safe_zones(self):
        fb = PLATFORM_SPECS[Platform.FACEBOOK]
        assert fb.safe_zone_top_pct == 0.14
        assert fb.safe_zone_bottom_pct == 0.35

    def test_threads_matches_instagram_codec(self):
        assert PLATFORM_SPECS[Platform.THREADS].codec == PLATFORM_SPECS[Platform.INSTAGRAM].codec
        assert PLATFORM_SPECS[Platform.THREADS].crf == PLATFORM_SPECS[Platform.INSTAGRAM].crf

    def test_youtube_preserves_source_fps(self):
        assert PLATFORM_SPECS[Platform.YOUTUBE].fps == "source"

    def test_instagram_forces_30fps(self):
        assert PLATFORM_SPECS[Platform.INSTAGRAM].fps == 30

    def test_all_platforms_have_specs(self):
        for platform in Platform:
            assert platform in PLATFORM_SPECS

    def test_master_spec_is_ffv1(self):
        assert MASTER_SPEC.codec == "ffv1"
        assert MASTER_SPEC.crf is None
        assert MASTER_SPEC.fps == "source"
        assert MASTER_SPEC.audio_codec == "pcm_s24le"


# ---------------------------------------------------------------------------
# FFmpeg binary discovery
# ---------------------------------------------------------------------------


class TestBinaryDiscovery:
    def test_ffmpeg_binary_from_env(self):
        get_ffmpeg_binary.cache_clear()
        with patch.dict("os.environ", {"FFMPEG_BINARY": "/custom/ffmpeg"}):
            assert get_ffmpeg_binary() == "/custom/ffmpeg"
        get_ffmpeg_binary.cache_clear()

    def test_ffprobe_binary_from_env(self):
        get_ffprobe_binary.cache_clear()
        with patch.dict("os.environ", {"FFPROBE_BINARY": "/custom/ffprobe"}):
            assert get_ffprobe_binary() == "/custom/ffprobe"
        get_ffprobe_binary.cache_clear()

    def test_ffmpeg_not_found_raises(self):
        get_ffmpeg_binary.cache_clear()
        with patch.dict("os.environ", {}, clear=True):
            with patch("shutil.which", return_value=None):
                with patch("os.path.isfile", return_value=False):
                    with pytest.raises(RuntimeError, match="FFmpeg not found"):
                        get_ffmpeg_binary()
        get_ffmpeg_binary.cache_clear()


# ---------------------------------------------------------------------------
# GPU hardware detection
# ---------------------------------------------------------------------------


class TestHWAccel:
    def test_cpu_fallback_when_no_gpu(self):
        detect_hw_accel.cache_clear()
        mock_result = MagicMock()
        mock_result.stdout = "libx264 libx265"  # no GPU encoders
        with patch("subprocess.run", return_value=mock_result):
            with patch.dict("os.environ", {"FFMPEG_BINARY": "/usr/bin/ffmpeg"}):
                get_ffmpeg_binary.cache_clear()
                hw = detect_hw_accel()
                assert hw.h264_encoder == "libx264"
                assert hw.h265_encoder == "libx265"
                assert hw.speedup_factor == 1.0
        detect_hw_accel.cache_clear()
        get_ffmpeg_binary.cache_clear()

    def test_nvidia_detected(self):
        detect_hw_accel.cache_clear()
        mock_result = MagicMock()
        mock_result.stdout = "h264_nvenc hevc_nvenc libx264"
        with patch("subprocess.run", return_value=mock_result):
            with patch.dict("os.environ", {"FFMPEG_BINARY": "/usr/bin/ffmpeg"}):
                get_ffmpeg_binary.cache_clear()
                hw = detect_hw_accel()
                assert hw.h264_encoder == "h264_nvenc"
                assert hw.h265_encoder == "hevc_nvenc"
                assert hw.speedup_factor == 6.0
        detect_hw_accel.cache_clear()
        get_ffmpeg_binary.cache_clear()

    def test_apple_silicon_detected(self):
        detect_hw_accel.cache_clear()
        mock_result = MagicMock()
        mock_result.stdout = "h264_videotoolbox hevc_videotoolbox libx264"
        with patch("subprocess.run", return_value=mock_result):
            with patch.dict("os.environ", {"FFMPEG_BINARY": "/usr/bin/ffmpeg"}):
                get_ffmpeg_binary.cache_clear()
                hw = detect_hw_accel()
                assert hw.h264_encoder == "h264_videotoolbox"
                assert hw.speedup_factor == 4.0
        detect_hw_accel.cache_clear()
        get_ffmpeg_binary.cache_clear()


# ---------------------------------------------------------------------------
# _apply_hw_accel
# ---------------------------------------------------------------------------


class TestApplyHWAccel:
    def test_cpu_returns_unchanged(self):
        spec = RenderSpec(codec="libx264", crf=15)
        hw = HWAccel()  # CPU fallback
        result = _apply_hw_accel(spec, hw)
        assert result is spec  # identity — no copy

    def test_gpu_adjusts_h264_crf_plus_2(self):
        spec = RenderSpec(codec="libx264", crf=15)
        hw = HWAccel(h264_encoder="h264_nvenc", speedup_factor=6.0)
        result = _apply_hw_accel(spec, hw)
        assert result.codec == "h264_nvenc"
        assert result.crf == 17  # 15 + 2

    def test_gpu_adjusts_h265_crf_plus_2(self):
        spec = RenderSpec(codec="libx265", crf=18)
        hw = HWAccel(h265_encoder="hevc_nvenc", speedup_factor=6.0)
        result = _apply_hw_accel(spec, hw)
        assert result.codec == "hevc_nvenc"
        assert result.crf == 20  # 18 + 2

    def test_crf_clamps_at_max(self):
        spec = RenderSpec(codec="libx264", crf=50)
        hw = HWAccel(h264_encoder="h264_nvenc", speedup_factor=6.0)
        result = _apply_hw_accel(spec, hw)
        assert result.crf == 51  # clamped at 51 for H.264

    def test_none_crf_stays_none(self):
        spec = RenderSpec(codec="libx264", crf=None)
        hw = HWAccel(h264_encoder="h264_nvenc", speedup_factor=6.0)
        result = _apply_hw_accel(spec, hw)
        assert result.crf is None


# ---------------------------------------------------------------------------
# resolve_twitter_spec
# ---------------------------------------------------------------------------


class TestResolveTwitterSpec:
    def test_premium_returns_1080p(self):
        mock_client = MagicMock()
        mock_client.get_platform_account.return_value = {"tier": "premium"}
        result = resolve_twitter_spec("gaming", mock_client)
        assert result.width == 1080

    def test_standard_returns_720p(self):
        mock_client = MagicMock()
        mock_client.get_platform_account.return_value = {"tier": "standard"}
        result = resolve_twitter_spec("gaming", mock_client)
        assert result.width == 720

    def test_error_defaults_to_standard(self):
        mock_client = MagicMock()
        mock_client.get_platform_account.side_effect = Exception("not found")
        result = resolve_twitter_spec("gaming", mock_client)
        assert result.width == 720
