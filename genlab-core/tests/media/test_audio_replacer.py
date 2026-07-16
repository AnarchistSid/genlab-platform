"""Pin the audio replacer module (PR 7).

Covers:
  1. Filtergraph string composition (exact FFmpeg syntax)
  2. Full ffmpeg command shape (order + flag presence)
  3. Library resolution: missing/empty/populated
  4. Random pick determinism given seeded RNG
  5. Extension filter (only recognized music types picked)
  6. mix_audio_bed: happy path with mock subprocess.run
  7. mix_audio_bed: missing input files fail cleanly
  8. mix_audio_bed: timeout is fail-open
  9. mix_audio_bed: nonzero exit code fails cleanly
 10. replace_audio_for_reel: missing library → False, don't crash

No live FFmpeg invocations — all subprocess calls mocked.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.audio_replacer import (
    AudioMixSpec,
    build_audio_mix_filtergraph,
    build_ffmpeg_command,
    find_music_bed_for_mood,
    mix_audio_bed,
    replace_audio_for_reel,
)


class TestFiltergraph:
    def test_default_ducking_syntax(self) -> None:
        fg = build_audio_mix_filtergraph(-12, -6)
        assert fg == (
            "[0:a]volume=-12dB[a1];"
            "[1:a]volume=-6dB[a2];"
            "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )

    def test_anime_conservative_ducking(self) -> None:
        """FrameDrift uses -9dB source + -9dB music per config."""
        fg = build_audio_mix_filtergraph(-9, -9)
        assert "volume=-9dB[a1]" in fg
        assert "volume=-9dB[a2]" in fg

    def test_duration_first_pins_amix_behavior(self) -> None:
        """amix must use duration=first — cuts music at source length
        rather than extending the video."""
        fg = build_audio_mix_filtergraph(-12, -6)
        assert "duration=first" in fg


class TestFFmpegCommand:
    def _make_spec(self, tmp_path: Path) -> AudioMixSpec:
        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        out = tmp_path / "out.mp4"
        return AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )

    def test_command_shape_defaults(self, tmp_path: Path) -> None:
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-y" in cmd
        assert "-i" in cmd
        assert str(spec.source_video_path) in cmd
        assert str(spec.music_bed_path) in cmd
        assert str(spec.output_path) in cmd

    def test_stream_loop_infinite(self, tmp_path: Path) -> None:
        """Music input MUST have -stream_loop -1 so shorter tracks
        loop rather than the video ending in silence."""
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        # Find the -stream_loop position; must precede the music -i
        idx = cmd.index("-stream_loop")
        assert cmd[idx + 1] == "-1"
        # The very next flag must be another -i (the music input)
        assert cmd[idx + 2] == "-i"
        assert cmd[idx + 3] == str(spec.music_bed_path)

    def test_video_copy_not_reencoded(self, tmp_path: Path) -> None:
        """-c:v copy — never re-encode video during audio mixing.
        Re-encoding here would double the render time + risk quality
        drift."""
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        idx = cmd.index("-c:v")
        assert cmd[idx + 1] == "copy"

    def test_audio_reencoded_aac(self, tmp_path: Path) -> None:
        """Audio MUST be re-encoded (aac) — we're mixing streams."""
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "aac"

    def test_shortest_flag_present(self, tmp_path: Path) -> None:
        """-shortest hedges against amix duration edge cases."""
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        assert "-shortest" in cmd

    def test_aout_mapped(self, tmp_path: Path) -> None:
        """Output MUST map [aout] not 0:a?, so the filter's mix survives."""
        spec = self._make_spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        # Find -map entries
        map_indices = [i for i, v in enumerate(cmd) if v == "-map"]
        map_values = [cmd[i + 1] for i in map_indices]
        assert "0:v" in map_values
        assert "[aout]" in map_values


class TestFindMusicBed:
    def test_missing_library_returns_none(self, tmp_path: Path) -> None:
        # No assets/music_beds directory at all
        result = find_music_bed_for_mood(tmp_path, "cinematic")
        assert result is None

    def test_missing_mood_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "assets" / "music_beds").mkdir(parents=True)
        # Library exists but no cinematic/ subdir
        result = find_music_bed_for_mood(tmp_path, "cinematic")
        assert result is None

    def test_empty_mood_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "assets" / "music_beds" / "cinematic").mkdir(parents=True)
        result = find_music_bed_for_mood(tmp_path, "cinematic")
        assert result is None

    def test_wrong_extension_ignored(self, tmp_path: Path) -> None:
        """A .txt file in the mood dir is not a music file."""
        mood_dir = tmp_path / "assets" / "music_beds" / "cinematic"
        mood_dir.mkdir(parents=True)
        (mood_dir / "notes.txt").write_text("some notes")
        result = find_music_bed_for_mood(tmp_path, "cinematic")
        assert result is None

    def test_picks_from_populated_dir(self, tmp_path: Path) -> None:
        mood_dir = tmp_path / "assets" / "music_beds" / "cinematic"
        mood_dir.mkdir(parents=True)
        track_a = mood_dir / "track_a.mp3"
        track_b = mood_dir / "track_b.mp3"
        track_a.write_bytes(b"fake")
        track_b.write_bytes(b"fake")
        result = find_music_bed_for_mood(tmp_path, "cinematic")
        assert result is not None
        assert result.name in {"track_a.mp3", "track_b.mp3"}

    def test_deterministic_with_seeded_rng(self, tmp_path: Path) -> None:
        mood_dir = tmp_path / "assets" / "music_beds" / "cinematic"
        mood_dir.mkdir(parents=True)
        for name in ("track_a.mp3", "track_b.mp3", "track_c.mp3"):
            (mood_dir / name).write_bytes(b"fake")
        # Same seed → same pick
        first = find_music_bed_for_mood(tmp_path, "cinematic", rng=random.Random(42))
        second = find_music_bed_for_mood(tmp_path, "cinematic", rng=random.Random(42))
        assert first == second

    def test_all_supported_extensions(self, tmp_path: Path) -> None:
        """.mp3, .m4a, .wav, .ogg, .opus all recognized."""
        mood_dir = tmp_path / "assets" / "music_beds" / "epic"
        mood_dir.mkdir(parents=True)
        for ext in (".mp3", ".m4a", ".wav", ".ogg", ".opus"):
            (mood_dir / f"track{ext}").write_bytes(b"fake")
        # Pick 20 times — over these 5 files, all should appear at least
        # once given uniform picks (probability approx 1 for 5 picks each
        # per file with 20 draws).
        picked_names = set()
        for i in range(50):
            r = find_music_bed_for_mood(tmp_path, "epic", rng=random.Random(i))
            if r is not None:
                picked_names.add(r.name)
        assert len(picked_names) == 5


class TestMixAudioBed:
    @pytest.fixture
    def mix_paths(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        out = tmp_path / "out.mp4"
        source.write_bytes(b"a" * 2000)  # non-trivial fake video
        music.write_bytes(b"m" * 2000)
        return source, music, out

    def test_missing_source_returns_false(self, tmp_path: Path) -> None:
        spec = AudioMixSpec(
            source_video_path=tmp_path / "no.mp4",
            music_bed_path=tmp_path / "no.mp3",
            output_path=tmp_path / "out.mp4",
        )
        assert mix_audio_bed(spec) is False

    def test_missing_music_returns_false(self, tmp_path: Path, mix_paths) -> None:
        source, _, out = mix_paths
        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=tmp_path / "no.mp3",
            output_path=out,
        )
        assert mix_audio_bed(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_path(self, mock_binary, mock_run, mix_paths) -> None:
        source, music, out = mix_paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            # Simulate ffmpeg writing an output file.
            out.write_bytes(b"o" * 2048)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run

        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )
        assert mix_audio_bed(spec) is True
        assert mock_run.called

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_timeout_returns_false(self, mock_binary, mock_run, mix_paths) -> None:
        source, music, out = mix_paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300)

        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )
        assert mix_audio_bed(spec, timeout_seconds=1) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_nonzero_exit_returns_false(self, mock_binary, mock_run, mix_paths) -> None:
        source, music, out = mix_paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=1, stdout="", stderr="fake error"
        )

        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )
        assert mix_audio_bed(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_binary_unavailable_returns_false(
        self, mock_binary, mock_run, mix_paths
    ) -> None:
        source, music, out = mix_paths
        mock_binary.side_effect = RuntimeError("ffmpeg not found")

        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )
        assert mix_audio_bed(spec) is False
        # subprocess.run must NOT be called when binary is missing
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_tiny_output_treated_as_failure(self, mock_binary, mock_run, mix_paths) -> None:
        """FFmpeg sometimes writes a broken tiny file on partial failure."""
        source, music, out = mix_paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"tiny")  # < 1024 bytes
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run

        spec = AudioMixSpec(
            source_video_path=source,
            music_bed_path=music,
            output_path=out,
        )
        assert mix_audio_bed(spec) is False


class TestReplaceAudioForReel:
    def test_missing_library_returns_false(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        out = tmp_path / "out.mp4"
        # niche_root without music_beds library
        result = replace_audio_for_reel(
            niche_root=tmp_path,
            source_video_path=source,
            output_path=out,
            mood_tag="cinematic",
        )
        assert result is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
