"""R-39: ``ValidateVideos`` must auto-fix the two new issue codes
that previously caused render-then-drop silent failures.

Before R-39 ``_can_fix`` returned False for ``too_long`` and
``no_audio_stream``, so:

  * a 90-second composite (gaming compilation, multi-clip movie
    cuts) was rendered fully, validated, then dropped at the
    ``valid: False`` gate — wasted minutes of FFmpeg work AND a
    silent loss of a publishable post (R-65's "dark day").
  * a YouTube source with no audio track (rare but real: silent
    cinematic trailers, screen recordings) hit the same
    render-then-drop loop.

After R-39 both issues join the fixable set. ``too_long`` is
trimmed with ``-t SPEC.max_duration``; ``no_audio_stream`` is
backfilled with a silent ``anullsrc`` audio bed.

These tests pin each fix path independently AND verify the
combined case (both issues at once produces a single ffmpeg pass
that handles both).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from genlab_core.pipeline.stages.validate_videos import SPEC, ValidateVideos

# ── _can_fix ──────────────────────────────────────────────────────────


def test_r39_can_fix_too_long() -> None:
    """``too_long:<seconds>`` joins the fixable set."""
    assert ValidateVideos._can_fix(["too_long:65.0s"]) is True


def test_r39_can_fix_no_audio_stream() -> None:
    """``no_audio_stream`` joins the fixable set."""
    assert ValidateVideos._can_fix(["no_audio_stream"]) is True


def test_r39_can_fix_combined_too_long_and_no_audio_stream() -> None:
    """A render with BOTH problems is still fixable — one ffmpeg
    pass handles both."""
    assert ValidateVideos._can_fix(["no_audio_stream", "too_long:90.0s"]) is True


def test_r39_can_fix_combined_with_legacy_codec_issue() -> None:
    """An R-39 issue alongside an existing fixable issue keeps the
    combination fixable."""
    assert ValidateVideos._can_fix(["wrong_codec", "too_long:75.5s"]) is True


def test_r39_unfixable_too_short_still_unfixable() -> None:
    """We didn't add ``too_short`` to the fixable set — there's no
    safe way to extend a 5s clip to 15s. Pin that.
    """
    assert ValidateVideos._can_fix(["too_short:5.0s"]) is False


# ── _fix command shape ────────────────────────────────────────────────


def _run_fix(issues: list[str], tmp_path: Path) -> list[str]:
    """Invoke ``_fix`` with subprocess.run mocked; return the
    captured ffmpeg argv. The output file is created on disk
    (mock side-effect) so ``out.exists()`` returns True and
    ``_fix`` returns the path (not None)."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    out = src.with_stem(f"{src.stem}_fixed")

    captured: dict = {}

    def fake_run(cmd, **_):
        captured["cmd"] = cmd
        out.write_bytes(b"y")  # _fix checks ``out.exists()``
        return MagicMock(returncode=0)

    with (
        patch(
            "genlab_core.pipeline.stages.validate_videos.get_ffmpeg_binary",
            return_value="ffmpeg",
        ),
        patch(
            "genlab_core.pipeline.stages.validate_videos.subprocess.run",
            side_effect=fake_run,
        ),
    ):
        result = ValidateVideos._fix(src, probe={}, issues=issues)

    assert result is not None
    return captured["cmd"]


def test_r39_fix_too_long_appends_t_max_duration(tmp_path: Path) -> None:
    """``too_long`` adds ``-t <SPEC.max_duration>`` AFTER the codec args
    so the trim applies to the output length, not the decoder seek."""
    cmd = _run_fix(["too_long:75.0s"], tmp_path)

    assert "-t" in cmd
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == str(SPEC["max_duration"])
    # ``-t`` must land AFTER ``-c:v libx264`` (output-side trim).
    assert cmd.index("-c:v") < t_idx


def test_r39_fix_no_audio_injects_anullsrc(tmp_path: Path) -> None:
    """``no_audio_stream`` mux in a silent stereo 48kHz bed via
    ``-f lavfi -i anullsrc=...`` with input mapping so the video
    keeps its visuals."""
    cmd = _run_fix(["no_audio_stream"], tmp_path)

    # anullsrc bed is input 0 — ``-f lavfi`` precedes its ``-i``.
    assert "-f" in cmd
    f_idx = cmd.index("-f")
    assert cmd[f_idx + 1] == "lavfi"
    assert any("anullsrc=" in arg for arg in cmd), f"anullsrc not in ffmpeg cmd: {cmd!r}"
    # Map directives route audio from input 0, video from input 1.
    assert "-map" in cmd
    map_idxs = [i for i, v in enumerate(cmd) if v == "-map"]
    map_args = [cmd[i + 1] for i in map_idxs]
    assert "1:v:0" in map_args
    assert "0:a" in map_args
    # ``-shortest`` caps output to the video length so silence doesn't trail.
    assert "-shortest" in cmd


def test_r39_fix_combined_too_long_and_no_audio(tmp_path: Path) -> None:
    """One ffmpeg pass handles both problems — anullsrc bed AND
    -t trim — so we don't transcode the file twice."""
    cmd = _run_fix(["no_audio_stream", "too_long:120.0s"], tmp_path)

    # Both fix paths fired.
    assert any("anullsrc=" in arg for arg in cmd)
    assert "-t" in cmd
    assert cmd[cmd.index("-t") + 1] == str(SPEC["max_duration"])


def test_r39_fix_legacy_codec_path_unchanged_when_no_r39_issues(tmp_path: Path) -> None:
    """A pure codec/format fix (no R-39 issues) keeps the legacy
    single-input shape — no anullsrc, no -t. Regression pin so the
    R-39 additions don't slip into the common-case fix path."""
    cmd = _run_fix(["wrong_codec", "wrong_pix_fmt"], tmp_path)

    assert not any("anullsrc=" in arg for arg in cmd)
    assert "-t" not in cmd
    # Only one input, the source file.
    i_idxs = [i for i, v in enumerate(cmd) if v == "-i"]
    assert len(i_idxs) == 1
