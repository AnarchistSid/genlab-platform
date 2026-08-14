"""Pin Phase 4.B session 1 aesthetic feature extraction.

Uses synthesized MP4s via ffmpeg lavfi like the sibling quality
test suites. Skips gracefully when ffmpeg missing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from genlab_core.quality.aesthetic_features import (
    AestheticFeatures,
    _GRID,
    _balance_score,
    _brightness_entropy,
    _hsv,
    _luma,
    _pixels,
    extract_aesthetic_features,
)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _make_solid_video(tmp_path: Path, color: str, seconds: float = 2.0) -> Path:
    out = tmp_path / f"solid_{color}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"color=c={color}:s=320x180:r=30:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ],
        capture_output=True, check=True, timeout=15,
    )
    return out


def _make_testsrc(tmp_path: Path, seconds: float = 2.0) -> Path:
    out = tmp_path / "testsrc.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=s=320x180:r=30:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ],
        capture_output=True, check=True, timeout=15,
    )
    return out


ffmpeg_mark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not installed on test host",
)


# ── Pure-python helpers (no ffmpeg needed) ────────────────────────


class TestLumaHelpers:
    def test_luma_pure_white(self):
        # 0.2126*255 + 0.7152*255 + 0.0722*255 = 255
        assert _luma((255, 255, 255)) == pytest.approx(255)

    def test_luma_pure_black(self):
        assert _luma((0, 0, 0)) == 0

    def test_luma_pure_red(self):
        assert _luma((255, 0, 0)) == pytest.approx(0.2126 * 255)

    def test_hsv_pure_red_hue_0(self):
        h, s, v = _hsv((255, 0, 0))
        assert h == 0.0
        assert s == 1.0
        assert v == 1.0

    def test_hsv_gray_sat_zero(self):
        _, s, _ = _hsv((128, 128, 128))
        assert s == 0.0

    def test_pixels_slices_rgb_triples(self):
        rgb = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9])
        assert _pixels(rgb) == [(1, 2, 3), (4, 5, 6), (7, 8, 9)]


class TestBalanceScore:
    def test_equal_halves_score_1(self):
        assert _balance_score(100.0, 100.0) == 1.0

    def test_one_zero_score_0(self):
        assert _balance_score(100.0, 0.0) == 0.0

    def test_zero_zero_returns_0(self):
        assert _balance_score(0.0, 0.0) == 0.0

    def test_partial_imbalance(self):
        # 100 vs 50 → diff=50, sum=150, imbalance=0.333, score=0.667
        assert _balance_score(100.0, 50.0) == pytest.approx(0.666, abs=1e-2)


class TestBrightnessEntropy:
    def test_uniform_bins_max_entropy(self):
        # 16 evenly-spaced values from 0-255 → each bin has exactly 1
        luma = [i * 16 for i in range(16)] * 100
        h = _brightness_entropy(luma)
        assert h == pytest.approx(1.0, abs=1e-2)

    def test_single_value_min_entropy(self):
        # All 128 → all fall in bin 8 → entropy 0
        luma = [128.0] * 1000
        assert _brightness_entropy(luma) == 0.0

    def test_empty_returns_zero(self):
        assert _brightness_entropy([]) == 0.0


# ── End-to-end (needs ffmpeg) ────────────────────────────────────


class TestExtractShape:
    def test_missing_file_ok_false(self, tmp_path):
        result = extract_aesthetic_features(tmp_path / "nope.mp4")
        assert result.ok is False
        assert "file_not_found" in result.reason

    @ffmpeg_mark
    def test_solid_color_full_extraction(self, tmp_path):
        vid = _make_solid_video(tmp_path, "black")
        result = extract_aesthetic_features(vid)
        assert isinstance(result, AestheticFeatures)
        assert result.ok is True
        # All features present + within valid ranges
        assert 0 <= result.rot_horizontal_score <= 1
        assert 0 <= result.horizontal_symmetry <= 1
        assert 0 <= result.edge_density <= 1
        assert 0 <= result.brightness_entropy <= 1

    @ffmpeg_mark
    def test_all_15_features_populated(self, tmp_path):
        """Pin the feature count — a drift here means a downstream
        model would silently get fewer or extra features than
        expected."""
        vid = _make_solid_video(tmp_path, "cyan")
        result = extract_aesthetic_features(vid)
        d = result.to_dict()
        # Drop 'ok' and 'reason' + confirm 15 feature keys
        d.pop("ok", None)
        d.pop("reason", None)
        assert len(d) == 15


class TestSolidVsColorful:
    """Ranking pins — synthesized signals with known aesthetic
    properties should score in the expected direction."""

    @ffmpeg_mark
    def test_solid_black_symmetry_high(self, tmp_path):
        """Solid black is trivially symmetric (any half mirrors any
        other)."""
        vid = _make_solid_video(tmp_path, "black")
        result = extract_aesthetic_features(vid)
        assert result.ok
        # Perfect symmetry — every pixel matches its mirror
        assert result.horizontal_symmetry > 0.95
        assert result.vertical_symmetry > 0.95

    @ffmpeg_mark
    def test_solid_edge_density_zero(self, tmp_path):
        """Solid = no gradients = no edges."""
        vid = _make_solid_video(tmp_path, "black")
        result = extract_aesthetic_features(vid)
        assert result.ok
        assert result.edge_density < 0.05

    @ffmpeg_mark
    def test_testsrc_higher_edge_density(self, tmp_path):
        """testsrc has many geometric shapes → high edge density."""
        solid = _make_solid_video(tmp_path, "gray")
        pattern = _make_testsrc(tmp_path)
        s_result = extract_aesthetic_features(solid)
        p_result = extract_aesthetic_features(pattern)
        assert s_result.ok and p_result.ok
        assert p_result.edge_density > s_result.edge_density

    @ffmpeg_mark
    def test_solid_brightness_variance_low(self, tmp_path):
        """Solid color → uniform brightness → variance near 0."""
        vid = _make_solid_video(tmp_path, "gray")
        result = extract_aesthetic_features(vid)
        assert result.ok
        assert result.brightness_variance < 0.01

    @ffmpeg_mark
    def test_9x16_aspect_recognized(self, tmp_path):
        """Vertical short-form aspect. Solid at 320×180 landscape;
        aspect_ratio should be ~1.78 (>1)."""
        vid = _make_solid_video(tmp_path, "black")
        result = extract_aesthetic_features(vid)
        assert result.ok
        assert result.aspect_ratio > 1.0
