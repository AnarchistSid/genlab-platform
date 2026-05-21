"""Tests that all FFmpeg encoding calls include bt709 colorspace, yuv420p, and correct CRF.

Sprint 1 fixes: F-01 (CRF 23), F-02 (no bt709), F-03 (no yuv420p).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _extract_cmd(mock_run) -> list[str]:
    """Extract the FFmpeg command list from a mocked subprocess.run call."""
    call_args = mock_run.call_args
    return call_args[0][0] if call_args[0] else call_args.kwargs.get("cmd", [])


def _assert_quality_args(cmd: list[str], expected_crf: str = "17"):
    """Assert that the FFmpeg command contains quality encoding args."""
    assert "-pix_fmt" in cmd, "Missing -pix_fmt"
    pix_idx = cmd.index("-pix_fmt")
    assert cmd[pix_idx + 1] == "yuv420p", f"pix_fmt should be yuv420p, got {cmd[pix_idx + 1]}"

    assert "-color_primaries" in cmd, "Missing -color_primaries"
    cp_idx = cmd.index("-color_primaries")
    assert cmd[cp_idx + 1] == "bt709", f"color_primaries should be bt709, got {cmd[cp_idx + 1]}"

    assert "-color_trc" in cmd, "Missing -color_trc"
    ct_idx = cmd.index("-color_trc")
    assert cmd[ct_idx + 1] == "bt709", f"color_trc should be bt709, got {cmd[ct_idx + 1]}"

    assert "-colorspace" in cmd, "Missing -colorspace"
    cs_idx = cmd.index("-colorspace")
    assert cmd[cs_idx + 1] == "bt709", f"colorspace should be bt709, got {cmd[cs_idx + 1]}"

    assert "-crf" in cmd, "Missing -crf"
    crf_idx = cmd.index("-crf")
    assert cmd[crf_idx + 1] == expected_crf, f"CRF should be {expected_crf}, got {cmd[crf_idx + 1]}"

    # Must NOT contain CRF 23 anywhere
    for i, arg in enumerate(cmd):
        if arg == "-crf":
            assert cmd[i + 1] != "23", "CRF 23 found — should have been replaced"


# ---------------------------------------------------------------------------
# ffmpeg_gaming.normalize_clip
# ---------------------------------------------------------------------------


class TestNormalizeClipEncoding:
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    def test_uses_quality_args(self, mock_probe, mock_run):
        from niches.gaming.media.ffmpeg_gaming import normalize_clip

        mock_probe.return_value = {"video": {"width": 1920, "height": 1080}}
        mock_run.return_value = MagicMock(returncode=0)

        normalize_clip("/in.mp4", "/out.mp4")

        cmd = _extract_cmd(mock_run)
        _assert_quality_args(cmd, expected_crf="17")

    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    def test_preserves_caller_fps(self, mock_probe, mock_run):
        from niches.gaming.media.ffmpeg_gaming import normalize_clip

        mock_probe.return_value = {"video": {"width": 1920, "height": 1080}}
        mock_run.return_value = MagicMock(returncode=0)

        normalize_clip("/in.mp4", "/out.mp4", target_fps=30)

        cmd = _extract_cmd(mock_run)
        r_idx = cmd.index("-r")
        assert cmd[r_idx + 1] == "30", "fps should respect caller's target_fps"


# ---------------------------------------------------------------------------
# ffmpeg_gaming.concat_with_transitions
# ---------------------------------------------------------------------------


class TestConcatWithTransitionsEncoding:
    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    @patch("niches.gaming.media.ffmpeg_gaming.get_duration")
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    def test_uses_quality_args_with_audio(self, mock_run, mock_dur, mock_probe):
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        mock_dur.side_effect = [10.0, 10.0, 10.0]
        mock_probe.return_value = {"audio": {"channels": 2, "codec": "aac", "sample_rate": 44100}}
        mock_run.return_value = MagicMock(returncode=0)

        transitions = [
            {"type": "zoom_impact", "duration": 0.5},
            {"type": "white_flash", "duration": 0.5},
        ]
        concat_with_transitions(["/a.mp4", "/b.mp4", "/c.mp4"], "/out.mp4", transitions)

        cmd = _extract_cmd(mock_run)
        _assert_quality_args(cmd, expected_crf="17")

    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    @patch("niches.gaming.media.ffmpeg_gaming.get_duration")
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    def test_uses_quality_args_without_audio(self, mock_run, mock_dur, mock_probe):
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        mock_dur.side_effect = [10.0, 10.0]
        mock_probe.return_value = {"audio": {"channels": 0}}
        mock_run.return_value = MagicMock(returncode=0)

        transitions = [{"type": "zoom_impact", "duration": 0.5}]
        concat_with_transitions(["/a.mp4", "/b.mp4"], "/out.mp4", transitions)

        cmd = _extract_cmd(mock_run)
        _assert_quality_args(cmd, expected_crf="17")


# ---------------------------------------------------------------------------
# ffmpeg_gaming.add_text_overlay
# ---------------------------------------------------------------------------


class TestAddTextOverlayEncoding:
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    def test_uses_quality_args(self, mock_run):
        from niches.gaming.media.ffmpeg_gaming import add_text_overlay

        mock_run.return_value = MagicMock(returncode=0)

        overlays = [
            {
                "text": "Test",
                "font_file": "/font.ttf",
                "size": 48,
                "color": "white",
                "position": "center",
                "start_time": 0.0,
                "end_time": 3.0,
            }
        ]
        add_text_overlay("/in.mp4", "/out.mp4", overlays)

        cmd = _extract_cmd(mock_run)
        _assert_quality_args(cmd, expected_crf="17")


# ---------------------------------------------------------------------------
# render_gaming_video._render_with_ffmpeg
# ---------------------------------------------------------------------------


class TestRenderWithFfmpegEncoding:
    @patch("niches.gaming.stages.render_gaming_video.get_duration", return_value=25.0)
    @patch("niches.gaming.stages.render_gaming_video.subprocess.run")
    @patch("niches.gaming.stages.render_gaming_video.probe")
    def test_uses_quality_args(self, mock_probe, mock_run, mock_dur):
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        mock_probe.return_value = {"audio": {"channels": 2}}
        mock_run.return_value = MagicMock(returncode=0)

        stage = RenderGamingVideo()

        with patch.object(Path, "exists", return_value=True):
            stage._render_with_ffmpeg("/clip.mp4", "Test hook", "/out.mp4")

        cmd = _extract_cmd(mock_run)
        _assert_quality_args(cmd, expected_crf="17")

    @patch("niches.gaming.stages.render_gaming_video.get_duration", return_value=25.0)
    @patch("niches.gaming.stages.render_gaming_video.subprocess.run")
    @patch("niches.gaming.stages.render_gaming_video.probe")
    def test_no_crf_23(self, mock_probe, mock_run, mock_dur):
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        mock_probe.return_value = {"audio": {"channels": 0}}
        mock_run.return_value = MagicMock(returncode=0)

        stage = RenderGamingVideo()

        with patch.object(Path, "exists", return_value=True):
            stage._render_with_ffmpeg("/clip.mp4", "Hook", "/out.mp4")

        cmd = _extract_cmd(mock_run)
        for i, arg in enumerate(cmd):
            if arg == "-crf":
                assert cmd[i + 1] != "23", "CRF 23 should not appear in encoding args"

    @patch("niches.gaming.stages.render_gaming_video.get_duration", return_value=90.0)
    @patch("niches.gaming.stages.render_gaming_video.subprocess.run")
    @patch("niches.gaming.stages.render_gaming_video.probe")
    def test_long_clip_gets_truncated(self, mock_probe, mock_run, mock_dur):
        """Clips >60s trigger truncation before render."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        mock_probe.return_value = {"audio": {"channels": 2}}
        mock_run.return_value = MagicMock(returncode=0)

        stage = RenderGamingVideo()

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "stat", return_value=MagicMock(st_size=1024)),
        ):
            stage._render_with_ffmpeg("/clip.mp4", "Test hook", "/out.mp4")

        # First call should be the truncation command
        first_call_cmd = mock_run.call_args_list[0][0][0]
        assert "ffmpeg" in first_call_cmd[0] or first_call_cmd[0] == "ffmpeg"
        assert "-t" in first_call_cmd
        t_idx = first_call_cmd.index("-t")
        assert first_call_cmd[t_idx + 1] == "60"

    @patch("niches.gaming.stages.render_gaming_video.get_duration", return_value=30.0)
    @patch("niches.gaming.stages.render_gaming_video.subprocess.run")
    @patch("niches.gaming.stages.render_gaming_video.probe")
    def test_dynamic_timeout_scales_with_duration(self, mock_probe, mock_run, mock_dur):
        """Timeout should be max(300, duration * 3) for medium preset."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        mock_probe.return_value = {"audio": {"channels": 0}}
        mock_run.return_value = MagicMock(returncode=0)

        stage = RenderGamingVideo()

        with patch.object(Path, "exists", return_value=True):
            stage._render_with_ffmpeg("/clip.mp4", "Hook", "/out.mp4")

        # 30s clip: max(300, 30*3)=300. Medium multiplier=1.0 -> timeout=300
        call_kwargs = mock_run.call_args
        assert call_kwargs[1].get("timeout", 0) == 300


# ---------------------------------------------------------------------------
# clip_sourcer._normalize_to_h264
# ---------------------------------------------------------------------------


class TestNormalizeToH264Encoding:
    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_has_bt709_and_yuv420p(self, mock_run):
        from niches.gaming.tools.clip_sourcer import _normalize_to_h264

        # First call: ffprobe returns vp9 codec
        probe_result = MagicMock(returncode=0, stdout="vp9\n")
        # Second call: ffmpeg transcode succeeds
        transcode_result = MagicMock(returncode=0)
        mock_run.side_effect = [probe_result, transcode_result]

        _normalize_to_h264("/fake/clip.mp4")

        # Second call is the transcode
        transcode_cmd = mock_run.call_args_list[1][0][0]
        _assert_quality_args(transcode_cmd, expected_crf="18")

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_no_crf_23(self, mock_run):
        from niches.gaming.tools.clip_sourcer import _normalize_to_h264

        probe_result = MagicMock(returncode=0, stdout="av1\n")
        transcode_result = MagicMock(returncode=0)
        mock_run.side_effect = [probe_result, transcode_result]

        _normalize_to_h264("/fake/clip.mp4")

        transcode_cmd = mock_run.call_args_list[1][0][0]
        for i, arg in enumerate(transcode_cmd):
            if arg == "-crf":
                assert transcode_cmd[i + 1] != "23", "CRF 23 should not appear"


# ---------------------------------------------------------------------------
# Regression guard: no CRF 23 in source files
# ---------------------------------------------------------------------------


class TestNoCrf23InSource:
    """Scan source files to ensure CRF 23 is not hardcoded anywhere."""

    _FILES = [
        Path(__file__).resolve().parent.parent.parent
        / "niches"
        / "gaming"
        / "media"
        / "ffmpeg_gaming.py",
        Path(__file__).resolve().parent.parent.parent
        / "niches"
        / "gaming"
        / "stages"
        / "render_gaming_video.py",
        Path(__file__).resolve().parent.parent.parent
        / "niches"
        / "gaming"
        / "tools"
        / "clip_sourcer.py",
    ]

    @pytest.mark.parametrize("filepath", _FILES, ids=lambda p: p.name)
    def test_no_crf_23_literal(self, filepath):
        if not filepath.exists():
            pytest.skip(f"{filepath} not found")
        content = filepath.read_text()
        # Check for patterns like '"23"' near 'crf' (within 30 chars)
        import re

        matches = re.findall(r'["\']crf["\'].*?["\']23["\']', content, re.IGNORECASE)
        alt_matches = re.findall(r'["\']23["\'].*?["\']crf["\']', content, re.IGNORECASE)
        assert not matches, f"Found CRF 23 in {filepath.name}: {matches}"
        assert not alt_matches, f"Found CRF 23 in {filepath.name}: {alt_matches}"
