"""Tests for genlab_core.video.standards — Track C Steps C2 + C3."""
from genlab_core.media.ffmpeg import PLATFORM_SPECS
from genlab_core.media.ffmpeg import Platform as FFmpegPlatform
from genlab_core.media.video_compositor import VisualConfig
from genlab_core.video.standards import LayoutStandard, Platform, all_standards, get_standard


# ── C2: Core standard assertions ─────────────────────────────────────────────


def test_instagram_standard_uses_h264() -> None:
    std = get_standard(Platform.INSTAGRAM)
    assert std.video.codec == "libx264"


def test_youtube_standard_uses_h265() -> None:
    std = get_standard(Platform.YOUTUBE)
    assert std.video.codec == "libx265"


def test_all_standards_have_bt709_colorspace() -> None:
    for platform, std in all_standards().items():
        assert std.video.color_space == "bt709", f"{platform} missing bt709"


def test_vmaf_floor_is_85_for_all_platforms() -> None:
    for platform, std in all_standards().items():
        assert std.video.vmaf_floor == 85, f"{platform} VMAF floor != 85"


# ── C3: Cross-validation against existing modules ────────────────────────────

# Map between standards.Platform and ffmpeg.Platform enum values
_PLATFORM_MAP: dict[Platform, FFmpegPlatform] = {
    Platform.INSTAGRAM: FFmpegPlatform.INSTAGRAM,
    Platform.YOUTUBE: FFmpegPlatform.YOUTUBE,
    Platform.TIKTOK: FFmpegPlatform.TIKTOK,
    Platform.FACEBOOK: FFmpegPlatform.FACEBOOK,
    Platform.X_STANDARD: FFmpegPlatform.X_STD,
    Platform.X_PREMIUM: FFmpegPlatform.X_PREMIUM,
    Platform.THREADS: FFmpegPlatform.THREADS,
}


def test_standards_codec_matches_platform_specs() -> None:
    """Standards codec must match PLATFORM_SPECS codec for every platform."""
    for std_plat, ffmpeg_plat in _PLATFORM_MAP.items():
        std = get_standard(std_plat)
        spec = PLATFORM_SPECS[ffmpeg_plat]
        assert std.video.codec == spec.codec, (
            f"{std_plat.value}: standards says {std.video.codec}, "
            f"PLATFORM_SPECS says {spec.codec}"
        )


def test_standards_crf_matches_platform_specs() -> None:
    """Standards CRF must match PLATFORM_SPECS CRF for every platform."""
    for std_plat, ffmpeg_plat in _PLATFORM_MAP.items():
        std = get_standard(std_plat)
        spec = PLATFORM_SPECS[ffmpeg_plat]
        assert std.video.crf == spec.crf, (
            f"{std_plat.value}: standards says CRF {std.video.crf}, "
            f"PLATFORM_SPECS says CRF {spec.crf}"
        )


def test_layout_standard_matches_visual_config_defaults() -> None:
    """LayoutStandard defaults must match VisualConfig defaults."""
    layout = LayoutStandard()
    # VisualConfig requires niche_id, logo_path, accent_color — use dummy values
    vc = VisualConfig(niche_id="test", logo_path="/tmp/logo.png", accent_color="#000")
    assert layout.top_bar_pct == vc.top_bar_height_pct
    assert layout.bottom_bar_pct == vc.bottom_bar_height_pct
    assert layout.logo_height == vc.logo_height_px
    assert layout.hook_font_size == vc.hook_font_size
