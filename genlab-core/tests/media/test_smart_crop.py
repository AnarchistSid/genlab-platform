"""Tests for genlab_core.media.smart_crop.

Unit tests run without OpenCV or FFmpeg (mock-based).
Integration tests require both and are marked with pytest.mark.integration.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from genlab_core.media.smart_crop import (
    CropRegion,
    CropStrategy,
    SmartCropper,
    smart_crop_available,
    _clamp_offset,
    _no_crop,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_meta(w: int, h: int):
    return {"width": w, "height": h, "duration": 10.0}


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestCropRegion:
    def test_none_strategy(self):
        r = _no_crop(1920, 1080)
        assert r.strategy == CropStrategy.NONE
        assert r.crop_width == 1920
        assert r.crop_height == 1080

    def test_center_strategy_fields(self):
        r = CropRegion(
            strategy=CropStrategy.CENTER,
            x_offset=420, crop_width=608, crop_height=1080,
            source_width=1920, source_height=1080,
            confidence=1.0,
        )
        assert r.x_offset == 420
        assert r.strategy == CropStrategy.CENTER


class TestClampOffset:
    def test_clamps_negative(self):
        assert _clamp_offset(-10, 1920, 608) == 0

    def test_clamps_overflow(self):
        assert _clamp_offset(1500, 1920, 608) == 1920 - 608

    def test_passes_valid(self):
        assert _clamp_offset(500, 1920, 608) == 500


class TestDetectSkipsPortrait:
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_portrait_returns_none_strategy(self, mock_probe):
        mock_probe.return_value = _fake_meta(1080, 1920)
        cropper = SmartCropper()
        region = cropper.detect(Path("fake.mp4"))
        assert region.strategy == CropStrategy.NONE

    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_square_returns_none_strategy(self, mock_probe):
        mock_probe.return_value = _fake_meta(1080, 1080)
        cropper = SmartCropper()
        region = cropper.detect(Path("fake.mp4"))
        assert region.strategy == CropStrategy.NONE


class TestDetectLandscape:
    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_landscape_without_cv2_uses_center(self, mock_probe):
        mock_probe.return_value = _fake_meta(1920, 1080)
        cropper = SmartCropper()
        region = cropper.detect(Path("fake.mp4"))
        assert region.strategy == CropStrategy.CENTER
        # Crop width should be height * 9/16, even
        expected_w = int(1080 * 9 / 16) & ~1
        assert region.crop_width == expected_w
        # X offset should centre the crop
        assert region.x_offset == (1920 - expected_w) // 2

    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_probe_failure_returns_none_strategy(self, mock_probe):
        mock_probe.return_value = {}
        cropper = SmartCropper()
        region = cropper.detect(Path("fake.mp4"))
        assert region.strategy == CropStrategy.NONE

    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_even_dimensions(self, mock_probe):
        # Odd source height should still produce even crop dims
        mock_probe.return_value = _fake_meta(1921, 1081)
        cropper = SmartCropper()
        region = cropper.detect(Path("fake.mp4"))
        assert region.crop_width % 2 == 0
        assert region.crop_height % 2 == 0

    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_custom_min_aspect(self, mock_probe):
        # 1280x1080 = aspect 1.185 — below default 1.2 but above 1.1
        mock_probe.return_value = _fake_meta(1280, 1080)
        cropper_strict = SmartCropper(min_landscape_aspect=1.2)
        region_strict = cropper_strict.detect(Path("fake.mp4"))
        assert region_strict.strategy == CropStrategy.NONE

        cropper_relaxed = SmartCropper(min_landscape_aspect=1.1)
        region_relaxed = cropper_relaxed.detect(Path("fake.mp4"))
        assert region_relaxed.strategy == CropStrategy.CENTER


class TestDetectFaceCenter:
    """Test face detection with mocked OpenCV."""

    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_face_detected_uses_face_strategy(self, mock_probe):
        mock_probe.return_value = _fake_meta(1920, 1080)

        cropper = SmartCropper(face_probe_frames=5, min_face_detections=2)

        # Mock the OpenCV calls inside _detect_face_center
        import numpy as np

        fake_faces = np.array([[800, 200, 150, 150]])  # x, y, w, h → centre = 875

        with (
            patch("genlab_core.media.smart_crop._CV2_AVAILABLE", True),
            patch("genlab_core.media.smart_crop.cv2") as mock_cv2,
        ):
            mock_cap = MagicMock()
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cap.isOpened.return_value = True
            mock_cap.get.return_value = 100  # total frames
            # Return 5 frames, then stop
            mock_cap.read.side_effect = [
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
            ]
            mock_cv2.cvtColor.return_value = MagicMock()

            cascade = MagicMock()
            mock_cv2.CascadeClassifier.return_value = cascade
            mock_cv2.data.haarcascades = "/fake/"
            cascade.detectMultiScale.return_value = fake_faces

            region = cropper.detect(Path("fake.mp4"))

        assert region.strategy == CropStrategy.FACE_CENTER
        assert region.confidence == 0.8
        # Face centre at 875, crop_w = 1080*9/16 = 607 → x_offset ≈ 875-303 = 572
        expected_crop_w = int(1080 * 9 / 16) & ~1
        assert region.crop_width == expected_crop_w

    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_no_faces_falls_through(self, mock_probe):
        mock_probe.return_value = _fake_meta(1920, 1080)

        cropper = SmartCropper(face_probe_frames=5, min_face_detections=3)

        import numpy as np

        with (
            patch("genlab_core.media.smart_crop._CV2_AVAILABLE", True),
            patch("genlab_core.media.smart_crop.cv2") as mock_cv2,
        ):
            mock_cap = MagicMock()
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cap.isOpened.return_value = True
            mock_cap.get.return_value = 100
            mock_cap.read.side_effect = [
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
                (True, MagicMock()),
            ]
            mock_cv2.cvtColor.return_value = MagicMock()
            cascade = MagicMock()
            mock_cv2.CascadeClassifier.return_value = cascade
            mock_cv2.data.haarcascades = "/fake/"
            # No faces detected
            cascade.detectMultiScale.return_value = np.array([])

            # Also mock _detect_motion_center to return None
            with patch.object(cropper, "_detect_motion_center", return_value=None):
                region = cropper.detect(Path("fake.mp4"))

        # Falls through to centre crop
        assert region.strategy == CropStrategy.CENTER


class TestApply:
    @patch("genlab_core.media.smart_crop.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.smart_crop.subprocess.run")
    def test_apply_calls_ffmpeg_crop(self, mock_run, mock_ffmpeg, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)

        region = CropRegion(
            strategy=CropStrategy.CENTER,
            x_offset=420, crop_width=608, crop_height=1080,
            source_width=1920, source_height=1080,
        )
        clip = tmp_path / "input.mp4"
        clip.touch()
        out = tmp_path / "output.mp4"

        cropper = SmartCropper()
        result = cropper.apply(clip, region, out)

        assert result == out
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-vf" in cmd
        crop_idx = cmd.index("-vf") + 1
        assert "crop=608:1080:420:0" in cmd[crop_idx]

    def test_apply_none_strategy_returns_input(self, tmp_path):
        region = _no_crop(1920, 1080)
        clip = tmp_path / "input.mp4"
        clip.touch()
        out = tmp_path / "output.mp4"

        cropper = SmartCropper()
        result = cropper.apply(clip, region, out)
        assert result == clip  # unchanged
        assert not out.exists()

    @patch("genlab_core.media.smart_crop.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.smart_crop.subprocess.run")
    def test_apply_raises_on_ffmpeg_failure(self, mock_run, mock_ffmpeg, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Error: crop dimensions invalid",
        )
        region = CropRegion(
            strategy=CropStrategy.CENTER,
            x_offset=0, crop_width=608, crop_height=1080,
            source_width=1920, source_height=1080,
        )
        clip = tmp_path / "input.mp4"
        clip.touch()
        out = tmp_path / "output.mp4"

        cropper = SmartCropper()
        with pytest.raises(RuntimeError, match="Smart crop failed"):
            cropper.apply(clip, region, out)


class TestAutoCrop:
    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_auto_crop_portrait_returns_input(self, mock_probe, tmp_path):
        mock_probe.return_value = _fake_meta(1080, 1920)
        clip = tmp_path / "portrait.mp4"
        clip.touch()
        out = tmp_path / "cropped.mp4"

        cropper = SmartCropper()
        result = cropper.auto_crop(clip, out)
        assert result == clip

    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.smart_crop.subprocess.run")
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_auto_crop_landscape_crops(self, mock_probe, mock_run, mock_ffmpeg, tmp_path):
        mock_probe.return_value = _fake_meta(1920, 1080)
        mock_run.return_value = MagicMock(returncode=0)

        clip = tmp_path / "landscape.mp4"
        clip.touch()
        out = tmp_path / "cropped.mp4"

        cropper = SmartCropper()
        result = cropper.auto_crop(clip, out)
        assert result == out
        mock_run.assert_called_once()


class TestPreCropClips:
    @patch("genlab_core.media.smart_crop._CV2_AVAILABLE", False)
    @patch("genlab_core.media.smart_crop.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.smart_crop.subprocess.run")
    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_mixed_clips(self, mock_probe, mock_run, mock_ffmpeg, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)

        portrait = tmp_path / "portrait.mp4"
        portrait.touch()
        landscape = tmp_path / "landscape.mp4"
        landscape.touch()

        def fake_probe(path):
            if "portrait" in str(path):
                return _fake_meta(1080, 1920)
            return _fake_meta(1920, 1080)

        mock_probe.side_effect = fake_probe

        cropper = SmartCropper()
        result_clips, temp_dir = cropper.pre_crop_clips([portrait, landscape])

        assert len(result_clips) == 2
        assert result_clips[0] == portrait  # portrait unchanged
        assert result_clips[1] != landscape  # landscape was cropped
        assert temp_dir is not None

    @patch("genlab_core.media.smart_crop.probe_video_metadata")
    def test_all_portrait_no_temp_dir(self, mock_probe, tmp_path):
        mock_probe.return_value = _fake_meta(1080, 1920)
        clip = tmp_path / "portrait.mp4"
        clip.touch()

        cropper = SmartCropper()
        result_clips, temp_dir = cropper.pre_crop_clips([clip])

        assert result_clips == [clip]
        assert temp_dir is None


class TestSmartCropAvailable:
    def test_returns_bool(self):
        result = smart_crop_available()
        assert isinstance(result, bool)


# ── Integration tests (real FFmpeg + OpenCV) ──────────────────────────────────

FIXTURES = Path(__file__).parent / "_fixtures"

_has_fixtures = FIXTURES.exists() and (FIXTURES / "landscape_motion.mp4").exists()
_skip_integration = pytest.mark.skipif(
    not _has_fixtures, reason="test fixtures not generated",
)


@_skip_integration
class TestIntegrationDetect:
    """Run real OpenCV detection against synthetic test videos."""

    def test_portrait_skipped(self):
        cropper = SmartCropper()
        region = cropper.detect(FIXTURES / "portrait.mp4")
        assert region.strategy == CropStrategy.NONE

    def test_landscape_static_uses_center(self):
        cropper = SmartCropper()
        region = cropper.detect(FIXTURES / "landscape_static.mp4")
        # Static solid colour → no faces, no motion → centre crop
        assert region.strategy == CropStrategy.CENTER
        assert region.crop_width > 0
        assert region.crop_width < region.source_width
        assert region.crop_width % 2 == 0

    def test_landscape_motion_detects_motion(self):
        cropper = SmartCropper()
        region = cropper.detect(FIXTURES / "landscape_motion.mp4")
        # Moving white box should trigger motion detection
        assert region.strategy in (CropStrategy.MOTION_CENTER, CropStrategy.CENTER)
        assert region.crop_width > 0


@_skip_integration
class TestIntegrationAutoCrop:
    """Full auto_crop pipeline: detect + FFmpeg crop."""

    def test_landscape_static_produces_output(self, tmp_path):
        cropper = SmartCropper()
        out = tmp_path / "cropped.mp4"
        result = cropper.auto_crop(FIXTURES / "landscape_static.mp4", out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

        # Verify output dimensions are ~9:16
        from genlab_core.media.ffmpeg_utils import probe_video_metadata

        meta = probe_video_metadata(out)
        assert meta["width"] < meta["height"] or meta["width"] == meta["height"]
        # Width should be height * 9/16 (±1 for rounding)
        expected_w = int(meta["height"] * 9 / 16) & ~1
        assert abs(meta["width"] - expected_w) <= 2

    def test_portrait_returns_input_unchanged(self, tmp_path):
        cropper = SmartCropper()
        out = tmp_path / "cropped.mp4"
        result = cropper.auto_crop(FIXTURES / "portrait.mp4", out)
        assert result == FIXTURES / "portrait.mp4"
        assert not out.exists()

    def test_landscape_motion_produces_output(self, tmp_path):
        cropper = SmartCropper()
        out = tmp_path / "cropped.mp4"
        result = cropper.auto_crop(FIXTURES / "landscape_motion.mp4", out)
        assert result == out
        assert out.exists()

        from genlab_core.media.ffmpeg_utils import probe_video_metadata

        meta = probe_video_metadata(out)
        expected_w = int(meta["height"] * 9 / 16) & ~1
        assert abs(meta["width"] - expected_w) <= 2

    def test_pre_crop_clips_mixed(self, tmp_path):
        cropper = SmartCropper()
        clips = [
            FIXTURES / "portrait.mp4",
            FIXTURES / "landscape_static.mp4",
            FIXTURES / "landscape_motion.mp4",
        ]
        result_clips, temp_dir = cropper.pre_crop_clips(clips)

        assert len(result_clips) == 3
        # Portrait should be unchanged
        assert result_clips[0] == clips[0]
        # Landscape clips should be cropped (different paths)
        assert result_clips[1] != clips[1]
        assert result_clips[2] != clips[2]
        assert temp_dir is not None

        # All cropped outputs should exist
        for c in result_clips[1:]:
            assert Path(c).exists()
            assert Path(c).stat().st_size > 0

        # Cleanup
        import shutil
        shutil.rmtree(temp_dir)
