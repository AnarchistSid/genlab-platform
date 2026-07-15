"""Pin — `_validate_download` rejects clips shorter than the platform floor.

Background — 2026-07-15:
    Investigation of a stale_drafted health-monitor warning surfaced
    two gaming blueprints stuck at DRAFTED for 1-6 days, both failing
    render with ``too_short:5.0s``. Root cause: Twitch trending clips
    with 5-second source duration. The Twitch fetcher fix
    (`a3ebabf9`) added a fetcher-level filter — but Reddit's RSS path
    doesn't expose duration, YouTube already had a filter, and any
    future video source could hit the same shape without needing a
    per-source filter.

    This is the source-agnostic backstop: reject at ffprobe time
    (in ``_validate_download``, which every downloaded clip flows
    through) so nothing shorter than 15s ever reaches compose/render.

Design:
    - Match ``validate_videos.SPEC.min_duration`` (15.0s) exactly.
    - Duration=0 is preserved as "ffprobe failed / unknown" — NOT
      treated as too_short. Callers already handle unknown-duration
      via other paths (e.g. downstream retry).
    - ``0 < duration < 15`` returns valid=False with a clear
      ``too_short:X.Xs`` reason so operators can distinguish this
      failure from file-size / no-stream failures.

Pins:
    - 5s clip → invalid with too_short reason
    - 15s clip → valid (boundary)
    - 60s clip → valid
    - Duration=0 (probe failure) → NOT treated as too_short
    - Floor MUST equal validate_videos.SPEC.min_duration (cross-file
      pin — raising SPEC.min_duration without updating the download
      gate would let short clips slip through again)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.media.download_top_videos import (
    _MIN_DURATION_SECONDS,
    _validate_download,
)


@pytest.fixture
def valid_mp4(tmp_path):
    """A file large enough to pass the file-size check + has bytes.

    We monkey-patch ``_has_video_stream`` + ``_probe_duration`` in
    individual tests to isolate the duration-gate behaviour from
    ffprobe availability.
    """
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * (200 * 1024))  # 200 KB > _MIN_FILE_SIZE
    return p


class TestMinDurationGate:
    def test_short_clip_rejected(self, valid_mp4):
        """5s clip (Sheepy-shape) → too_short:5.0s."""
        with (
            patch(
                "genlab_core.media.download_top_videos._has_video_stream",
                return_value=True,
            ),
            patch(
                "genlab_core.media.download_top_videos._probe_duration",
                return_value=5.0,
            ),
        ):
            result = _validate_download(str(valid_mp4))
        assert result["valid"] is False
        assert "too_short:5.0s" in result["reason"]
        assert result["duration_seconds"] == 5.0

    def test_at_floor_accepted(self, valid_mp4):
        """15s clip → valid. Comparison is strict `< 15`, so 15.0 passes."""
        with (
            patch(
                "genlab_core.media.download_top_videos._has_video_stream",
                return_value=True,
            ),
            patch(
                "genlab_core.media.download_top_videos._probe_duration",
                return_value=15.0,
            ),
        ):
            result = _validate_download(str(valid_mp4))
        assert result["valid"] is True
        assert result["duration_seconds"] == 15.0

    def test_normal_clip_accepted(self, valid_mp4):
        """60s clip → valid."""
        with (
            patch(
                "genlab_core.media.download_top_videos._has_video_stream",
                return_value=True,
            ),
            patch(
                "genlab_core.media.download_top_videos._probe_duration",
                return_value=60.0,
            ),
        ):
            result = _validate_download(str(valid_mp4))
        assert result["valid"] is True

    def test_probe_failure_not_treated_as_too_short(self, valid_mp4):
        """Duration=0 means ffprobe couldn't read the file. That's
        different from a genuinely-short clip and MUST NOT be misclassified
        as too_short — callers depend on the reason string to route the
        failure (retry vs skip).
        """
        with (
            patch(
                "genlab_core.media.download_top_videos._has_video_stream",
                return_value=True,
            ),
            patch(
                "genlab_core.media.download_top_videos._probe_duration",
                return_value=0.0,
            ),
        ):
            result = _validate_download(str(valid_mp4))
        # valid=True because we ONLY reject 0 < duration < 15.
        # A duration=0 (probe-failure) clip is left for the caller to
        # handle via other paths — this gate is specifically for the
        # "short-clip DRAFTED bomb" case where the clip is real but tiny.
        assert result["valid"] is True
        assert "too_short" not in result["reason"]
        assert result["duration_seconds"] == 0.0

    def test_boundary_just_under_15_rejected(self, valid_mp4):
        """14.9s → still short. Exact boundary matters for pin stability."""
        with (
            patch(
                "genlab_core.media.download_top_videos._has_video_stream",
                return_value=True,
            ),
            patch(
                "genlab_core.media.download_top_videos._probe_duration",
                return_value=14.9,
            ),
        ):
            result = _validate_download(str(valid_mp4))
        assert result["valid"] is False
        assert "too_short:14.9s" in result["reason"]


class TestFloorMatchesValidateVideosSpec:
    """Cross-file pin: raising SPEC.min_duration in validate_videos
    without updating _MIN_DURATION_SECONDS in the download gate would
    reintroduce the exact class-of-bug this gate was built to catch.
    Forcing both edits to land in the same PR."""

    def test_download_gate_floor_matches_validate_videos_spec(self):
        from genlab_core.pipeline.stages.validate_videos import SPEC

        assert _MIN_DURATION_SECONDS == SPEC["min_duration"], (
            "download_top_videos._MIN_DURATION_SECONDS must equal "
            "validate_videos.SPEC.min_duration. If you raised SPEC.min_duration, "
            "update _MIN_DURATION_SECONDS in download_top_videos.py to match. "
            "The whole point of THIS gate is to reject at probe time what "
            "would otherwise be rejected at validate_videos time — with a "
            "day of wasted render pipeline work in between."
        )
