"""Pin the pan/zoom filter module (PR 9).

Covers:
  1. Each of the 5 patterns produces the expected filter behavior
  2. Unknown patterns → NO_FILTER + warning (fail-open)
  3. no_motion + jump_cut → NO_FILTER (deliberate no-op)
  4. ken_burns_slow / punch_in filter shape
  5. dolly filter uses scale + crop, not zoompan
  6. Command shape: bt709 colorspace, libx264 CRF 20, AAC 48kHz
  7. Fail-open on: missing source, no_motion pattern, ffmpeg errors

No live FFmpeg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from genlab_core.media.pan_zoom import (
    NO_FILTER,
    PanZoomSpec,
    apply_pan_zoom,
    build_ffmpeg_command,
    build_pan_zoom_filter,
)


class TestBuildFilter:
    def test_no_motion_returns_empty(self) -> None:
        assert build_pan_zoom_filter("no_motion", 1080, 1920, 30) == NO_FILTER

    def test_jump_cut_falls_back_to_no_filter(self) -> None:
        """jump_cut isn't implementable as a simple filter."""
        assert build_pan_zoom_filter("jump_cut", 1080, 1920, 30) == NO_FILTER

    def test_unknown_pattern_returns_empty(self) -> None:
        """Fail-open: unknown pattern doesn't crash."""
        assert build_pan_zoom_filter("mystery_pattern", 1080, 1920, 30) == NO_FILTER

    def test_ken_burns_slow_uses_zoompan(self) -> None:
        f = build_pan_zoom_filter("ken_burns_slow", 1080, 1920, 30)
        assert "zoompan" in f
        # Zoom cap at 1.2
        assert "1.2" in f
        # Rate: 0.01 per second
        assert "0.01" in f
        # Output size enforced
        assert "s=1080x1920" in f
        # Framerate enforced (video not slideshow)
        assert "fps=30" in f

    def test_punch_in_uses_delayed_zoom(self) -> None:
        f = build_pan_zoom_filter("punch_in", 1080, 1920, 30)
        assert "zoompan" in f
        # Static first 1s (lt(t,1) branch = 1.0)
        assert "lt(t,1)" in f
        # Ramp to 1.3
        assert "1.3" in f
        assert "s=1080x1920" in f

    def test_dolly_uses_scale_and_crop_not_zoompan(self) -> None:
        """Dolly is horizontal pan — different mechanism from zoom."""
        f = build_pan_zoom_filter("dolly", 1080, 1920, 30)
        assert "scale=iw*1.2" in f
        assert "crop=1080:1920" in f
        # t-based x expression drives the pan
        assert "iw-1080" in f or "iw-1080" in f
        # zoompan is NOT used for dolly (different tool)
        assert "zoompan" not in f

    def test_centered_zoompan_expressions(self) -> None:
        """Zoom filters MUST keep the frame centered — otherwise zoom
        drifts to the top-left corner (default zoompan behavior)."""
        f = build_pan_zoom_filter("ken_burns_slow", 1080, 1920, 30)
        # Center-x expression: iw/2 - (iw/zoom/2)
        assert "iw/2-(iw/zoom/2)" in f
        # Center-y: ih/2 - (ih/zoom/2)
        assert "ih/2-(ih/zoom/2)" in f

    def test_custom_dimensions_propagate(self) -> None:
        f = build_pan_zoom_filter("ken_burns_slow", 720, 1280, 60)
        assert "s=720x1280" in f
        assert "fps=60" in f


class TestBuildFFmpegCommand:
    def _spec(self, tmp_path: Path, pattern: str = "ken_burns_slow") -> PanZoomSpec:
        return PanZoomSpec(
            source_video_path=tmp_path / "source.mp4",
            output_path=tmp_path / "out.mp4",
            pattern=pattern,
        )

    def test_bt709_triple(self, tmp_path: Path) -> None:
        """CLAUDE.md non-negotiable — matches PR 8 pattern."""
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "somefilter")
        for flag in ("-colorspace", "-color_primaries", "-color_trc"):
            idx = cmd.index(flag)
            assert cmd[idx + 1] == "bt709", (
                f"{flag} must be bt709, got {cmd[idx + 1]}"
            )

    def test_libx264_crf20_preset_fast(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "somefilter")
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "20"
        assert cmd[cmd.index("-preset") + 1] == "fast"

    def test_aac_stereo_48khz(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "somefilter")
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert cmd[cmd.index("-ac") + 1] == "2"
        assert cmd[cmd.index("-ar") + 1] == "48000"

    def test_yuv420p(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "somefilter")
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"

    def test_filter_flag_present(self, tmp_path: Path) -> None:
        """The -vf flag must carry the filter string."""
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "myfilter=xyz")
        assert cmd[cmd.index("-vf") + 1] == "myfilter=xyz"


class TestApplyPanZoom:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> tuple[Path, Path]:
        source = tmp_path / "source.mp4"
        out = tmp_path / "out.mp4"
        source.write_bytes(b"a" * 2000)
        return source, out

    def test_no_motion_returns_false(self, tmp_path: Path, paths) -> None:
        """no_motion is a valid arm — caller uses source unchanged.
        We return False to signal that."""
        source, out = paths
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="no_motion"
        )
        assert apply_pan_zoom(spec) is False

    def test_jump_cut_returns_false(self, tmp_path: Path, paths) -> None:
        """jump_cut isn't implemented → NO_FILTER → False."""
        source, out = paths
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="jump_cut"
        )
        assert apply_pan_zoom(spec) is False

    def test_unknown_pattern_returns_false(self, tmp_path: Path, paths) -> None:
        source, out = paths
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="mystery"
        )
        assert apply_pan_zoom(spec) is False

    def test_missing_source_returns_false(self, tmp_path: Path) -> None:
        spec = PanZoomSpec(
            source_video_path=tmp_path / "no.mp4",
            output_path=tmp_path / "out.mp4",
            pattern="ken_burns_slow",
        )
        assert apply_pan_zoom(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_path(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"o" * 2048)
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = _fake_run
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="ken_burns_slow"
        )
        assert apply_pan_zoom(spec) is True

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_not_called_for_no_motion(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        """Short-circuit: no_motion pattern must NOT invoke subprocess."""
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="no_motion"
        )
        apply_pan_zoom(spec)
        mock_run.assert_not_called()
        # Binary lookup also skipped when filter is NO_FILTER — cheaper.
        mock_binary.assert_not_called()

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_timeout_returns_false(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["ffmpeg"], timeout=300
        )
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="ken_burns_slow"
        )
        assert apply_pan_zoom(spec, timeout_seconds=1) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_nonzero_exit(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=1, stdout="", stderr="err"
        )
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="ken_burns_slow"
        )
        assert apply_pan_zoom(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_binary_unavailable(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        source, out = paths
        mock_binary.side_effect = RuntimeError("ffmpeg not found")
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="ken_burns_slow"
        )
        assert apply_pan_zoom(spec) is False
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_tiny_output_is_failure(
        self, mock_binary, mock_run, tmp_path: Path, paths
    ) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"tiny")
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = _fake_run
        spec = PanZoomSpec(
            source_video_path=source, output_path=out, pattern="ken_burns_slow"
        )
        assert apply_pan_zoom(spec) is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
