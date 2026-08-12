"""Pin the first-frame brightener primitive + YouTube wire.

Contract:

  * `brighten_first_frames(input, output, boost, duration_s) -> bool`
  * Returns True when output is a valid re-encoded video with
    ffmpeg's `eq=brightness=X:enable='lt(t,Y)'` filter applied
  * Returns False on any failure:
      - Input missing
      - ffmpeg binary missing
      - ffmpeg exit non-zero
      - Timeout
      - Output missing or < 1024 bytes
      - Any exception
  * ffmpeg command uses `-c:a copy` (no audio re-encode) and
    libx264 CRF20 preset=fast (matches PLATFORM_SPECS baseline)

Structural pin:

  * platforms/youtube.py wires the brightener behind
    GENLAB_FIRST_FRAME_AUTOFIX_ENABLED and swaps video_path to
    the brightened output when successful
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.first_frame_brightener import (
    _DEFAULT_BOOST,
    _DEFAULT_DURATION_S,
    brighten_first_frames,
)


@pytest.fixture(autouse=True)
def _clear_ffmpeg_lru_cache():
    """Clear the get_ffmpeg_binary lru_cache before + after each
    test so a test setting FFMPEG_BINARY doesn't poison later
    tests (same issue as test_first_frame_validator.py)."""
    from genlab_core.media.ffmpeg import get_ffmpeg_binary
    get_ffmpeg_binary.cache_clear()
    yield
    get_ffmpeg_binary.cache_clear()


@pytest.fixture
def input_video(tmp_path):
    p = tmp_path / "input.mp4"
    p.write_bytes(b"fake input")
    return p


class TestSuccessPath:
    def test_returns_true_when_ffmpeg_succeeds(self, tmp_path, monkeypatch, input_video):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        output = tmp_path / "output.mp4"

        def _fake_run(*_a, **_k):
            output.write_bytes(b"x" * 2048)  # simulate ffmpeg writing valid output
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=_fake_run):
            result = brighten_first_frames(input_video, output)
        assert result is True
        assert output.exists()

    def test_uses_eq_brightness_filter(self, tmp_path, monkeypatch, input_video):
        """The ffmpeg command must use `eq=brightness=X:enable='lt(t,Y)'`
        so the boost is time-limited to the first duration_s seconds."""
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        output = tmp_path / "output.mp4"
        captured_cmd: list[str] = []

        def _fake_run(cmd, *_a, **_k):
            captured_cmd.extend(cmd)
            output.write_bytes(b"x" * 2048)
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=_fake_run):
            brighten_first_frames(input_video, output, boost=0.2, duration_s=0.15)

        vf_idx = captured_cmd.index("-vf") + 1
        assert "eq=brightness=0.2" in captured_cmd[vf_idx]
        assert "enable='lt(t,0.15)'" in captured_cmd[vf_idx]

    def test_audio_stream_copied_not_reencoded(self, tmp_path, monkeypatch, input_video):
        """Audio must be `-c:a copy` — re-encoding audio adds
        unnecessary time + quality loss."""
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        output = tmp_path / "output.mp4"
        captured_cmd: list[str] = []

        def _fake_run(cmd, *_a, **_k):
            captured_cmd.extend(cmd)
            output.write_bytes(b"x" * 2048)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            brighten_first_frames(input_video, output)

        ca_idx = captured_cmd.index("-c:a")
        assert captured_cmd[ca_idx + 1] == "copy"

    def test_default_boost_and_duration_reasonable(self):
        """Defaults sanity-check: boost must lift dark frames (30-40 YAVG)
        above the validator threshold (60); duration must exceed one
        frame time but stay below flash-blindness threshold."""
        assert 0.10 <= _DEFAULT_BOOST <= 0.30
        assert 0.05 <= _DEFAULT_DURATION_S <= 0.15  # 50ms - 150ms


class TestFailOpen:
    def test_input_missing_returns_false(self, tmp_path):
        result = brighten_first_frames(
            tmp_path / "nonexistent.mp4", tmp_path / "out.mp4",
        )
        assert result is False

    def test_ffmpeg_binary_missing_returns_false(self, tmp_path, monkeypatch, input_video):
        with patch(
            "genlab_core.media.ffmpeg.get_ffmpeg_binary",
            side_effect=RuntimeError("FFmpeg not found"),
        ):
            result = brighten_first_frames(input_video, tmp_path / "out.mp4")
        assert result is False

    def test_ffmpeg_exit_nonzero_returns_false(self, tmp_path, monkeypatch, input_video):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stderr="ffmpeg error"),
        ):
            result = brighten_first_frames(input_video, tmp_path / "out.mp4")
        assert result is False

    def test_timeout_returns_false(self, tmp_path, monkeypatch, input_video):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
        ):
            result = brighten_first_frames(input_video, tmp_path / "out.mp4")
        assert result is False

    def test_generic_exception_returns_false(self, tmp_path, monkeypatch, input_video):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch("subprocess.run", side_effect=OSError("simulated")):
            result = brighten_first_frames(input_video, tmp_path / "out.mp4")
        assert result is False

    def test_output_too_small_returns_false(self, tmp_path, monkeypatch, input_video):
        """ffmpeg reports success but produced a truncated file."""
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        output = tmp_path / "output.mp4"

        def _fake_run(*_a, **_k):
            output.write_bytes(b"truncated")  # < 1024 bytes
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            result = brighten_first_frames(input_video, output)
        assert result is False

    def test_output_missing_returns_false(self, tmp_path, monkeypatch, input_video):
        """ffmpeg reports success but produced no file."""
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = brighten_first_frames(input_video, tmp_path / "out.mp4")
        assert result is False


class TestYouTubeWire:
    def test_youtube_source_wires_autofix(self):
        """Structural pin: platforms/youtube.py imports and calls
        brighten_first_frames behind GENLAB_FIRST_FRAME_AUTOFIX_ENABLED."""
        import pathlib

        yt_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "youtube.py"
        )
        src = yt_path.read_text()
        assert "GENLAB_FIRST_FRAME_AUTOFIX_ENABLED" in src
        assert "brighten_first_frames" in src

    def test_autofix_only_when_validator_dark(self):
        """The autofix must gate on the validator's dark verdict —
        no point re-encoding when the frame is already bright."""
        import pathlib

        yt_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "youtube.py"
        )
        src = yt_path.read_text()
        # `not quality.passed` guards the autofix branch — verify it exists
        assert "not quality.passed" in src

    def test_autofix_swaps_video_path_on_success(self):
        """When brighten succeeds, video_path variable is reassigned
        to the brightened output. Guards against silent bug where
        brightener runs but original path is still published."""
        import pathlib

        yt_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "youtube.py"
        )
        src = yt_path.read_text()
        assert "video_path = brightened" in src


class TestInstagramWire:
    def test_instagram_source_wires_autofix(self):
        """Structural pin: platforms/instagram.py wires the brightener
        behind GENLAB_FIRST_FRAME_AUTOFIX_ENABLED."""
        import pathlib

        ig_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "instagram.py"
        )
        src = ig_path.read_text()
        assert "GENLAB_FIRST_FRAME_AUTOFIX_ENABLED" in src
        assert "brighten_first_frames" in src

    def test_instagram_wire_only_on_local_paths(self):
        """IG accepts either a local path OR an already-uploaded URL.
        The autofix must skip when the caller passed an HTTPS URL —
        we can't re-encode a remote file, and the video is already
        past the point of fixing without re-uploading a different
        file. The wire is inside `if not video_url.startswith('http'):`."""
        import pathlib

        ig_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "instagram.py"
        )
        src = ig_path.read_text()
        # The autofix branch must be inside a `not http` guard so
        # already-uploaded URLs skip the primitive
        assert "if not video_url.startswith(\"http\"):" in src

    def test_instagram_wire_swaps_video_url_not_path(self):
        """YT swaps video_path; IG swaps video_url (str, not Path)
        because that's what the downstream _publish_reel takes.
        Pin the correct variable name to catch a copy-paste from
        YT."""
        import pathlib

        ig_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "instagram.py"
        )
        src = ig_path.read_text()
        assert "video_url = str(brightened)" in src


class TestFacebookWire:
    def test_facebook_source_wires_autofix(self):
        """Structural pin: FB publish path wires the brightener."""
        import pathlib

        fb_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "facebook.py"
        )
        src = fb_path.read_text()
        assert "GENLAB_FIRST_FRAME_AUTOFIX_ENABLED" in src
        assert "brighten_first_frames" in src
        # Guard against remote-URL branch
        assert 'if not video_url.startswith("http"):' in src
        # Reassigns video_url on brighten success (FB path passes URL
        # to _publish_video downstream)
        assert "video_url = str(brightened)" in src


class TestThreadsWire:
    def test_threads_source_wires_autofix(self):
        import pathlib

        th_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "threads.py"
        )
        src = th_path.read_text()
        assert "GENLAB_FIRST_FRAME_AUTOFIX_ENABLED" in src
        assert "brighten_first_frames" in src

    def test_threads_wire_gates_on_video_media_type(self):
        """Threads accepts video, image, or text media_type. Autofix
        must only fire when media_type == 'video' — no point brightening
        a still image or text-only post."""
        import pathlib

        th_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "threads.py"
        )
        src = th_path.read_text()
        assert 'if media_type == "video" and media_paths:' in src

    def test_threads_wire_reassigns_first_media_path(self):
        """Threads takes media_paths as a list. When brighten succeeds,
        the first entry must be swapped for the brightened Path so the
        downstream _publish_video uses it. Pin `media_paths = [brightened,`
        prefix to catch a copy-paste that reassigns wrong variable."""
        import pathlib

        th_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "threads.py"
        )
        src = th_path.read_text()
        assert "media_paths = [brightened" in src
