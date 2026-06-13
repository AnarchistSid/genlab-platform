"""Content-aware pre-cropping for landscape source clips.

Detects faces or motion centres in landscape video clips and pre-crops
them to approximately 9:16 *before* they enter the sandwich compositor.
This makes subjects larger and more engaging in vertical format.

Three strategies, auto-selected per clip:
    1. FaceCenterCrop  — median face-X from first N frames (talking heads)
    2. MotionCenterCrop — optical-flow weighted centre (screen recordings, gameplay)
    3. CenterCrop       — simple centre crop (fallback)

If the source clip is already portrait or square, no crop is applied.

Usage::

    from genlab_core.media.smart_crop import SmartCropper

    cropper = SmartCropper()
    cropped = cropper.auto_crop(clip, output_dir / "cropped.mp4")

Integration:
    The sandwich-render path that previously consumed pre-cropped clips
    (``VideoCompositor.compose_vertical(smart_crop=True)``) was deleted in
    DEAD #1 (2026-06-13). The FrameCompositor render path does its own
    framing, so this module is currently used directly by callers that
    need pre-cropped clips (none in the prod pipeline today, but the
    cropper itself is still useful for one-off tooling).
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.ffmpeg_utils import probe_video_metadata

if TYPE_CHECKING:
    from genlab_core.media.sandbox_runner import SandboxedFFmpegRunner

logger = logging.getLogger(__name__)

# ── Optional OpenCV import ────────────────────────────────────────────────────
try:
    import cv2
    import numpy as np

    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.info("opencv-python not installed — smart crop will use centre-crop fallback")


# ── Constants ─────────────────────────────────────────────────────────────────

# Only crop clips with aspect ratio wider than this (landscape-ish).
_MIN_LANDSCAPE_ASPECT = 1.2
# Target aspect for vertical crop (9:16 = 0.5625).
_TARGET_ASPECT = 9.0 / 16.0
# Frames to probe for face detection.
_FACE_PROBE_FRAMES = 30
# Minimum face detections to trust the face-centre strategy.
_MIN_FACE_DETECTIONS = 3
# Optical-flow sample interval (seconds between sampled frames).
_FLOW_SAMPLE_INTERVAL_SEC = 1.0
# Motion magnitude threshold for "significant" movement.
_MOTION_THRESHOLD = 2.0
# FFmpeg timeout for the crop pass.
_CROP_TIMEOUT = 120
# Encode args for the crop intermediate (quality-preserving, fast).
_CROP_ENCODE_ARGS = [
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "18",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "copy",
    "-movflags",
    "+faststart",
]


# ══════════════════════════════════════════════════════════════════════════════
# Data models
# ══════════════════════════════════════════════════════════════════════════════


class CropStrategy(StrEnum):
    """Which algorithm determined the crop region."""

    FACE_CENTER = "face_center"
    MOTION_CENTER = "motion_center"
    CENTER = "center"
    NONE = "none"


class CropRegion(BaseModel):
    """Describes a crop rectangle and how it was chosen."""

    strategy: CropStrategy
    x_offset: int
    y_offset: int = 0
    crop_width: int
    crop_height: int
    source_width: int
    source_height: int
    confidence: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SmartCropper
# ══════════════════════════════════════════════════════════════════════════════


class SmartCropper:
    """Analyse clips for faces/motion and pre-crop landscape sources to ~9:16.

    Parameters
    ----------
    face_probe_frames : int
        How many initial frames to scan for faces (default 30).
    min_face_detections : int
        Minimum face hits to trust face-centre strategy (default 3).
    min_landscape_aspect : float
        Skip clips with aspect ratio below this (default 1.2).
    sandbox_runner : SandboxedFFmpegRunner | None
        Optional sandbox for the FFmpeg crop pass.
    """

    def __init__(
        self,
        face_probe_frames: int = _FACE_PROBE_FRAMES,
        min_face_detections: int = _MIN_FACE_DETECTIONS,
        min_landscape_aspect: float = _MIN_LANDSCAPE_ASPECT,
        sandbox_runner: SandboxedFFmpegRunner | None = None,
    ) -> None:
        self._face_probe_frames = face_probe_frames
        self._min_face_detections = min_face_detections
        self._min_landscape_aspect = min_landscape_aspect
        self._sandbox_runner = sandbox_runner

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self, clip: Path) -> CropRegion:
        """Analyse *clip* and return the best crop region.

        Does NOT modify the file — call :meth:`apply` with the result.
        """
        meta = probe_video_metadata(clip)
        if not meta or not meta.get("width") or not meta.get("height"):
            logger.warning("Cannot probe %s — skipping smart crop", clip)
            return _no_crop(0, 0)

        w, h = int(meta["width"]), int(meta["height"])
        aspect = w / h

        if aspect < self._min_landscape_aspect:
            logger.debug(
                "Clip %s aspect %.2f < %.2f — no crop needed",
                clip.name,
                aspect,
                self._min_landscape_aspect,
            )
            return _no_crop(w, h)

        crop_w = int(h * _TARGET_ASPECT) & ~1  # even width
        crop_h = h & ~1
        if crop_w >= w:
            return _no_crop(w, h)

        # Strategy cascade: face → motion → centre
        if _CV2_AVAILABLE:
            face_x = self._detect_face_center(clip, w, h)
            if face_x is not None:
                x_off = _clamp_offset(face_x - crop_w // 2, w, crop_w)
                return CropRegion(
                    strategy=CropStrategy.FACE_CENTER,
                    x_offset=x_off,
                    crop_width=crop_w,
                    crop_height=crop_h,
                    source_width=w,
                    source_height=h,
                    confidence=0.8,
                )

            motion_x = self._detect_motion_center(clip, w, h)
            if motion_x is not None:
                x_off = _clamp_offset(motion_x - crop_w // 2, w, crop_w)
                return CropRegion(
                    strategy=CropStrategy.MOTION_CENTER,
                    x_offset=x_off,
                    crop_width=crop_w,
                    crop_height=crop_h,
                    source_width=w,
                    source_height=h,
                    confidence=0.6,
                )

        # Fallback: centre crop
        x_off = (w - crop_w) // 2
        return CropRegion(
            strategy=CropStrategy.CENTER,
            x_offset=x_off,
            crop_width=crop_w,
            crop_height=crop_h,
            source_width=w,
            source_height=h,
            confidence=1.0,
        )

    def apply(self, clip: Path, region: CropRegion, output: Path) -> Path:
        """Apply a :class:`CropRegion` to *clip* with a single FFmpeg pass."""
        if region.strategy == CropStrategy.NONE:
            return clip

        output.parent.mkdir(parents=True, exist_ok=True)
        crop_filter = (
            f"crop={region.crop_width}:{region.crop_height}:{region.x_offset}:{region.y_offset}"
        )
        ffmpeg = get_ffmpeg_binary()
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(clip),
            "-vf",
            crop_filter,
            *_CROP_ENCODE_ARGS,
            str(output),
        ]

        if self._sandbox_runner is not None:
            result = self._sandbox_runner.run_ffmpeg_sync(cmd, timeout=_CROP_TIMEOUT)
            result.check("smart_crop")
        else:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CROP_TIMEOUT,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Smart crop failed (exit {result.returncode}):\n" + result.stderr[-2000:]
                )
        return output

    def auto_crop(self, clip: Path, output: Path) -> Path:
        """Detect strategy + apply in one call.

        Returns *output* if a crop was applied, or *clip* unchanged if no
        crop was needed (portrait/square source).
        """
        region = self.detect(clip)
        if region.strategy == CropStrategy.NONE:
            return clip

        logger.info(
            "Smart crop: %s → %s (strategy=%s, x_offset=%d, confidence=%.1f)",
            clip.name,
            output.name,
            region.strategy.value,
            region.x_offset,
            region.confidence,
        )
        return self.apply(clip, region, output)

    # ── Face detection (OpenCV Haar cascade) ──────────────────────────────────

    def _detect_face_center(
        self,
        clip: Path,
        w: int,
        h: int,
    ) -> int | None:
        """Return median face-centre X from first N frames, or None."""
        cap = cv2.VideoCapture(str(clip))
        if not cap.isOpened():
            return None

        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        probe_n = min(self._face_probe_frames, total)

        centres: list[int] = []
        for _ in range(probe_n):
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=8,
                minSize=(30, 30),
            )
            if len(faces) > 0:
                best = max(faces, key=lambda f: f[2] * f[3])
                centres.append(int(best[0] + best[2] // 2))

        cap.release()

        if len(centres) < self._min_face_detections:
            return None

        centres.sort()
        return centres[len(centres) // 2]

    # ── Motion detection (Farneback optical flow) ─────────────────────────────

    def _detect_motion_center(
        self,
        clip: Path,
        w: int,
        h: int,
    ) -> int | None:
        """Return weighted motion-centre X across sampled frames, or None."""
        cap = cv2.VideoCapture(str(clip))
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        sample_interval = max(1, int(fps * _FLOW_SAMPLE_INTERVAL_SEC))

        # Downscale factor for optical-flow speed
        ds = 4
        small_w, small_h = w // ds, h // ds

        prev_gray = None
        motion_weights: list[tuple[int, float]] = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_interval == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (small_w, small_h))

                if prev_gray is not None:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray,
                        small,
                        None,
                        0.5,
                        3,
                        15,
                        3,
                        5,
                        1.2,
                        0,
                    )
                    mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                    significant = mag > _MOTION_THRESHOLD

                    if np.any(significant):
                        col_motion = np.sum(mag * significant, axis=0)
                        total = float(np.sum(col_motion))
                        if total > 0:
                            centre_x = (
                                int(
                                    np.average(
                                        np.arange(small_w),
                                        weights=col_motion,
                                    )
                                )
                                * ds
                            )
                            motion_weights.append((centre_x, total))

                prev_gray = small
            frame_idx += 1

        cap.release()

        if not motion_weights:
            return None

        total_w = sum(mw for _, mw in motion_weights)
        if total_w == 0:
            return None

        return int(sum(cx * mw for cx, mw in motion_weights) / total_w)

    # ── Batch helper ──────────────────────────────────────────────────────────

    def pre_crop_clips(
        self,
        clips: list[Path],
    ) -> tuple[list[Path], str | None]:
        """Pre-crop a list of clips, returning (new_clips_list, temp_dir).

        Portrait/square clips are returned as-is.  Landscape clips get
        cropped into a temp directory.  Caller must clean up *temp_dir*
        when done (if not None).
        """
        temp_dir: str | None = None
        result: list[Path] = []

        for i, clip in enumerate(clips):
            region = self.detect(clip)
            if region.strategy == CropStrategy.NONE:
                result.append(clip)
                continue

            if temp_dir is None:
                temp_dir = tempfile.mkdtemp(prefix="genlab_smartcrop_")

            out = Path(temp_dir) / f"cropped_{i}{clip.suffix}"
            logger.info(
                "Smart crop [%d/%d]: %s → %s (%s, x=%d)",
                i + 1,
                len(clips),
                clip.name,
                out.name,
                region.strategy.value,
                region.x_offset,
            )
            self.apply(clip, region, out)
            result.append(out)

        return result, temp_dir


# ── Module-level helpers ──────────────────────────────────────────────────────


def smart_crop_available() -> bool:
    """Check whether OpenCV is installed for face/motion detection."""
    return _CV2_AVAILABLE


def _clamp_offset(x: int, source_w: int, crop_w: int) -> int:
    return max(0, min(x, source_w - crop_w))


def _no_crop(w: int, h: int) -> CropRegion:
    return CropRegion(
        strategy=CropStrategy.NONE,
        x_offset=0,
        crop_width=w,
        crop_height=h,
        source_width=w,
        source_height=h,
    )
