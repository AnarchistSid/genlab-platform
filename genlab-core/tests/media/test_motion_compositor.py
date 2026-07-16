"""Pin the motion graphics compositor (PR 8).

Covers:
  1. Filtergraph correctness for 1, 2, 3 segments
  2. Full ffmpeg argv shape (colorspace, codec, preset, audio)
  3. Segment collection: intro-only, outro-only, both, neither
  4. Asset path resolution across common extensions
  5. Fail-open: source missing / assets missing / no assets
  6. Timeout / nonzero exit / binary unavailable → False
  7. Tiny output → False
  8. Both-None case is deliberate no-op (returns False, not raise)
  9. MotionAssetChoice orchestrator resolves assets from niche_root

No live FFmpeg — subprocess.run mocked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.motion_compositor import (
    MotionAssetChoice,
    MotionCompositeSpec,
    _collect_segments,
    _resolve_asset_path,
    build_concat_filtergraph,
    build_ffmpeg_command,
    composite_for_reel,
    composite_motion_graphics,
)


class TestFiltergraph:
    def test_single_segment_still_valid(self) -> None:
        """n_segments=1 produces a valid pass-through filtergraph."""
        fg = build_concat_filtergraph(1, 1080, 1920)
        # Contains v0 label
        assert "[0:v]" in fg
        assert "[v0]" in fg
        # Contains the concat call with n=1
        assert "concat=n=1:v=1:a=1" in fg

    def test_three_segment_intro_source_outro(self) -> None:
        """The canonical intro+source+outro case."""
        fg = build_concat_filtergraph(3, 1080, 1920)
        # All 3 video inputs referenced
        for i in range(3):
            assert f"[{i}:v]" in fg
            assert f"[v{i}]" in fg
        # Concat inputs interleave normalized-video + normalized-audio
        # per-segment: [vN][aN]. The [aN] labels come from the aformat
        # audio-normalization stage (added 2026-07-06 to fix the concat
        # filter's exit=-22 when sample-rates or channel-layouts differ
        # across inputs).
        assert "[v0][a0][v1][a1][v2][a2]" in fg
        assert "concat=n=3:v=1:a=1[outv][outa]" in fg

    def test_three_segment_normalizes_audio_before_concat(self) -> None:
        """LIVE-FIRE BUG 2026-07-06: concat filter fails with
        ``[aost#0:1/aac] error code: -22 (Invalid argument)`` when
        inputs have mismatched audio sample rates or channel layouts.
        Fix: aformat each audio stream to 48kHz stereo BEFORE concat.
        Regression = movies pipeline fails 1-in-5 intro/outro renders
        with 0-byte outputs."""
        fg = build_concat_filtergraph(3, 1080, 1920)
        for i in range(3):
            # Each input's audio must be aformat-normalized to a
            # labeled [aN] stream before it enters the concat.
            assert f"[{i}:a]aformat=" in fg, f"input {i} audio missing aformat normalization"
            assert f"[a{i}]" in fg, f"input {i} aformat output label [a{i}] missing"
        # The specific normalization params must match the outer
        # -ar 48000 -ac 2 encode spec — otherwise concat still fails.
        assert "sample_rates=48000" in fg
        assert "channel_layouts=stereo" in fg

    def test_single_segment_also_normalizes_audio(self) -> None:
        """Even n=1 (no intro/outro, just source) needs the aformat
        pass — the concat filter still runs, and if the source's audio
        differs from the outer encode spec the output is silent."""
        fg = build_concat_filtergraph(1, 1080, 1920)
        assert "[0:a]aformat=" in fg
        assert "[a0]" in fg

    def test_scale_preserves_aspect(self) -> None:
        """Filter uses force_original_aspect_ratio=decrease + pad —
        never distorts inputs."""
        fg = build_concat_filtergraph(2, 1080, 1920)
        assert "force_original_aspect_ratio=decrease" in fg
        assert "pad=1080:1920" in fg

    def test_pad_color_black(self) -> None:
        """Letterbox color must be black (matches FrameCompositor)."""
        fg = build_concat_filtergraph(2, 1080, 1920)
        assert "color=black" in fg

    def test_zero_segments_raises(self) -> None:
        with pytest.raises(ValueError, match="n_segments must be >= 1"):
            build_concat_filtergraph(0, 1080, 1920)

    def test_custom_dimensions_propagate(self) -> None:
        fg = build_concat_filtergraph(2, 720, 1280)
        assert "scale=720:1280" in fg
        assert "pad=720:1280" in fg


class TestSegmentCollection:
    def _spec(self, intro=None, outro=None, tmp_path=None) -> MotionCompositeSpec:
        return MotionCompositeSpec(
            source_video_path=tmp_path / "source.mp4",
            output_path=tmp_path / "out.mp4",
            intro_path=intro,
            outro_path=outro,
        )

    def test_intro_source_outro(self, tmp_path: Path) -> None:
        intro = tmp_path / "intro.mp4"
        outro = tmp_path / "outro.mp4"
        spec = self._spec(intro, outro, tmp_path)
        segs = _collect_segments(spec)
        assert segs == [intro, tmp_path / "source.mp4", outro]

    def test_intro_source(self, tmp_path: Path) -> None:
        intro = tmp_path / "intro.mp4"
        spec = self._spec(intro, None, tmp_path)
        segs = _collect_segments(spec)
        assert segs == [intro, tmp_path / "source.mp4"]

    def test_source_outro(self, tmp_path: Path) -> None:
        outro = tmp_path / "outro.mp4"
        spec = self._spec(None, outro, tmp_path)
        segs = _collect_segments(spec)
        assert segs == [tmp_path / "source.mp4", outro]

    def test_source_only(self, tmp_path: Path) -> None:
        spec = self._spec(None, None, tmp_path)
        segs = _collect_segments(spec)
        assert segs == [tmp_path / "source.mp4"]


class TestResolveAssetPath:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = _resolve_asset_path(
            tmp_path, "intro_animation", "assets/motion/intros", "logo_zoom"
        )
        assert result is None

    def test_mp4_extension_preferred(self, tmp_path: Path) -> None:
        (tmp_path / "assets" / "motion" / "intros").mkdir(parents=True)
        f_mp4 = tmp_path / "assets" / "motion" / "intros" / "logo_zoom.mp4"
        f_mp4.write_bytes(b"fake")
        result = _resolve_asset_path(
            tmp_path, "intro_animation", "assets/motion/intros", "logo_zoom"
        )
        assert result == f_mp4

    def test_mov_fallback(self, tmp_path: Path) -> None:
        """.mov is tried if .mp4 doesn't exist."""
        (tmp_path / "assets" / "motion" / "intros").mkdir(parents=True)
        f_mov = tmp_path / "assets" / "motion" / "intros" / "logo_zoom.mov"
        f_mov.write_bytes(b"fake")
        result = _resolve_asset_path(
            tmp_path, "intro_animation", "assets/motion/intros", "logo_zoom"
        )
        assert result == f_mov

    def test_empty_template_name_returns_none(self, tmp_path: Path) -> None:
        result = _resolve_asset_path(tmp_path, "intro_animation", "assets/motion/intros", "")
        assert result is None

    def test_empty_asset_dir_returns_none(self, tmp_path: Path) -> None:
        result = _resolve_asset_path(tmp_path, "intro_animation", "", "logo_zoom")
        assert result is None


class TestFFmpegCommand:
    def _spec(self, tmp_path: Path) -> MotionCompositeSpec:
        return MotionCompositeSpec(
            source_video_path=tmp_path / "source.mp4",
            output_path=tmp_path / "out.mp4",
            intro_path=tmp_path / "intro.mp4",
            outro_path=tmp_path / "outro.mp4",
        )

    def test_command_shape(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        segs = [
            tmp_path / "intro.mp4",
            tmp_path / "source.mp4",
            tmp_path / "outro.mp4",
        ]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-y" in cmd
        # 3 -i entries
        assert cmd.count("-i") == 3
        # -map for both outv and outa
        map_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
        assert "[outv]" in map_values
        assert "[outa]" in map_values

    def test_bt709_colorspace_enforced(self, tmp_path: Path) -> None:
        """CLAUDE.md requires bt709 for all renders."""
        spec = self._spec(tmp_path)
        segs = [tmp_path / "intro.mp4"]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        cs_idx = cmd.index("-colorspace")
        assert cmd[cs_idx + 1] == "bt709"
        cp_idx = cmd.index("-color_primaries")
        assert cmd[cp_idx + 1] == "bt709"
        ct_idx = cmd.index("-color_trc")
        assert cmd[ct_idx + 1] == "bt709"

    def test_libx264_crf20(self, tmp_path: Path) -> None:
        """Matches PLATFORM_SPECS baseline."""
        spec = self._spec(tmp_path)
        segs = [tmp_path / "s.mp4"]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "libx264"
        crf_idx = cmd.index("-crf")
        assert cmd[crf_idx + 1] == "20"
        preset_idx = cmd.index("-preset")
        assert cmd[preset_idx + 1] == "fast"

    def test_aac_audio(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        segs = [tmp_path / "s.mp4"]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        ca_idx = cmd.index("-c:a")
        assert cmd[ca_idx + 1] == "aac"

    def test_48khz_stereo_audio(self, tmp_path: Path) -> None:
        """48kHz stereo matches FrameCompositor default."""
        spec = self._spec(tmp_path)
        segs = [tmp_path / "s.mp4"]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        ac_idx = cmd.index("-ac")
        assert cmd[ac_idx + 1] == "2"
        ar_idx = cmd.index("-ar")
        assert cmd[ar_idx + 1] == "48000"

    def test_yuv420p_pixel_format(self, tmp_path: Path) -> None:
        """yuv420p for max compatibility with mobile clients."""
        spec = self._spec(tmp_path)
        segs = [tmp_path / "s.mp4"]
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", segs)
        pix_idx = cmd.index("-pix_fmt")
        assert cmd[pix_idx + 1] == "yuv420p"


class TestCompositeMotionGraphics:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        source = tmp_path / "source.mp4"
        intro = tmp_path / "intro.mp4"
        outro = tmp_path / "outro.mp4"
        out = tmp_path / "out.mp4"
        source.write_bytes(b"a" * 2000)
        intro.write_bytes(b"i" * 2000)
        outro.write_bytes(b"o" * 2000)
        return source, intro, outro, out

    def test_missing_source_returns_false(self, tmp_path: Path) -> None:
        spec = MotionCompositeSpec(
            source_video_path=tmp_path / "no.mp4",
            output_path=tmp_path / "out.mp4",
            intro_path=tmp_path / "no_intro.mp4",
        )
        assert composite_motion_graphics(spec) is False

    def test_both_assets_none_returns_false(self, tmp_path: Path) -> None:
        """No-op case — returns False so caller uses source unchanged."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=tmp_path / "out.mp4",
            intro_path=None,
            outro_path=None,
        )
        assert composite_motion_graphics(spec) is False

    def test_referenced_intro_missing_returns_false(self, tmp_path: Path) -> None:
        """Selector picked an intro but the file's not on disk."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=tmp_path / "out.mp4",
            intro_path=tmp_path / "no_intro.mp4",
        )
        assert composite_motion_graphics(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_path(self, mock_binary, mock_run, paths) -> None:
        source, intro, outro, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"o" * 2048)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run

        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=out,
            intro_path=intro,
            outro_path=outro,
        )
        assert composite_motion_graphics(spec) is True
        assert mock_run.called

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_timeout_returns_false(self, mock_binary, mock_run, paths) -> None:
        source, intro, outro, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300)

        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=out,
            intro_path=intro,
        )
        assert composite_motion_graphics(spec, timeout_seconds=1) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_nonzero_exit_returns_false(self, mock_binary, mock_run, paths) -> None:
        source, intro, outro, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=1, stdout="", stderr="err"
        )

        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=out,
            intro_path=intro,
        )
        assert composite_motion_graphics(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_unavailable(self, mock_binary, mock_run, paths) -> None:
        source, intro, outro, out = paths
        mock_binary.side_effect = RuntimeError("ffmpeg not found")

        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=out,
            intro_path=intro,
        )
        assert composite_motion_graphics(spec) is False
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_tiny_output_is_failure(self, mock_binary, mock_run, paths) -> None:
        source, intro, outro, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"tiny")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run

        spec = MotionCompositeSpec(
            source_video_path=source,
            output_path=out,
            intro_path=intro,
        )
        assert composite_motion_graphics(spec) is False


class TestCompositeForReel:
    def test_missing_assets_returns_false(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        out = tmp_path / "out.mp4"
        choice = MotionAssetChoice(
            niche_root=tmp_path,
            intro_template="logo_zoom",
            outro_template="follow",
        )
        # No asset files exist → both resolve to None → composite_for_reel
        # sees both intro & outro None → returns False (no-op)
        assert composite_for_reel(choice, source, out) is False

    def test_only_intro_resolved(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        out = tmp_path / "out.mp4"
        # Seed intro but not outro
        intro_dir = tmp_path / "assets" / "motion" / "intros"
        intro_dir.mkdir(parents=True)
        (intro_dir / "logo_zoom.mp4").write_bytes(b"i" * 2000)

        choice = MotionAssetChoice(
            niche_root=tmp_path,
            intro_template="logo_zoom",
            outro_template="follow",  # asset doesn't exist
        )
        # intro resolves, outro None → composite still fires
        # (2-segment case). We patch subprocess to avoid running ffmpeg.
        with (
            patch("subprocess.run") as mock_run,
            patch("genlab_core.media.ffmpeg.get_ffmpeg_binary") as mock_bin,
        ):
            mock_bin.return_value = "/usr/bin/ffmpeg"

            def _fake_run(*args, **kwargs):
                out.write_bytes(b"o" * 2048)
                return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

            mock_run.side_effect = _fake_run
            assert composite_for_reel(choice, source, out) is True


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
