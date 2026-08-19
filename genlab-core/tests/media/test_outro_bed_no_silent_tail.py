"""NARR-10 (2026-08-20) — the outro CTA card must not append dead air.

Outro assets are designed as silent cards. Measured on production:

    BlackboxBrief/assets/motion/outros/comment.mp4
      2.500s · aac stream present · mean_volume -91.0 dB (digital silence)

Concatenating that after the audio mix appended a 2.517s silent tail to the
round-2 render — and to production reels on three of four measurable niches,
roughly 13% of an 18.6s reel, ending exactly where completion is decided.

The fix substitutes the music bed for the outro segment's own audio. These
tests assert on RENDERED OUTPUT, not on the filter string, because the whole
lesson of the ducking gap was that a string assertion passes on a graph that
does the wrong thing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.motion_compositor import (
    MotionCompositeSpec,
    build_ffmpeg_command,
)


def _ffmpeg() -> str:
    binary = get_ffmpeg_binary()
    if not binary:
        pytest.skip("ffmpeg not available")
    return binary


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        pytest.fail(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")


def _make_clip(ffmpeg: str, path: Path, seconds: float, hz: int | None) -> Path:
    """A clip with either a tone or a genuinely silent audio track."""
    audio = (
        f"sine=frequency={hz}:duration={seconds}" if hz
        else f"anullsrc=r=48000:cl=stereo:d={seconds}"
    )
    _run([
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc=size=320x568:rate=15:duration={seconds}",
        "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(path),
    ])
    return path


def _tail_silence_seconds(ffmpeg: str, media: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(media),
         "-af", "silencedetect=noise=-30dB:d=0.3", "-f", "null", "-"],
        capture_output=True, text=True, timeout=180,
    )
    hits = re.findall(r"silence_duration:\s*([0-9.]+)", proc.stderr or "")
    return float(hits[-1]) if hits else 0.0


def _durations(ffmpeg: str, media: Path) -> tuple[float, float]:
    probe = subprocess.run(
        [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
         "-show_entries", "stream=codec_type,duration",
         "-of", "default=noprint_wrappers=1", str(media)],
        capture_output=True, text=True, timeout=60,
    )
    v = a = 0.0
    kind = None
    for line in (probe.stdout or "").splitlines():
        if line.startswith("codec_type="):
            kind = line.split("=", 1)[1]
        elif line.startswith("duration="):
            try: val = float(line.split("=", 1)[1])
            except ValueError: continue
            if kind == "video": v = val
            elif kind == "audio": a = val
    return v, a


class TestOutroBed:
    def test_silent_outro_appends_dead_air_without_the_bed(self, tmp_path: Path):
        """Characterise the defect, so the fix below is measured against it."""
        ffmpeg = _ffmpeg()
        source = _make_clip(ffmpeg, tmp_path / "src.mp4", 6.0, 300)
        outro = _make_clip(ffmpeg, tmp_path / "outro.mp4", 2.5, None)
        out = tmp_path / "no_bed.mp4"

        _run(build_ffmpeg_command(
            MotionCompositeSpec(
                source_video_path=source, output_path=out, outro_path=outro,
            ),
            ffmpeg, [source, outro],
        ))

        tail = _tail_silence_seconds(ffmpeg, out)
        assert tail >= 1.0, (
            f"expected the silent-outro defect to reproduce, got {tail:.2f}s "
            "of trailing silence — if this fails the fixture no longer models "
            "production outro assets"
        )

    def test_bed_removes_the_silent_tail(self, tmp_path: Path):
        ffmpeg = _ffmpeg()
        source = _make_clip(ffmpeg, tmp_path / "src.mp4", 6.0, 300)
        outro = _make_clip(ffmpeg, tmp_path / "outro.mp4", 2.5, None)
        bed = _make_clip(ffmpeg, tmp_path / "bed.mp4", 4.0, 440)
        out = tmp_path / "with_bed.mp4"

        _run(build_ffmpeg_command(
            MotionCompositeSpec(
                source_video_path=source, output_path=out, outro_path=outro,
                outro_bed_path=bed,
            ),
            ffmpeg, [source, outro],
        ))

        tail = _tail_silence_seconds(ffmpeg, out)
        assert tail < 1.0, (
            f"outro still appends {tail:.2f}s of silence despite the bed — "
            "the CTA card is dead air exactly where completion is decided"
        )

    def test_audio_and_video_durations_agree(self, tmp_path: Path):
        ffmpeg = _ffmpeg()
        source = _make_clip(ffmpeg, tmp_path / "src.mp4", 6.0, 300)
        outro = _make_clip(ffmpeg, tmp_path / "outro.mp4", 2.5, None)
        bed = _make_clip(ffmpeg, tmp_path / "bed.mp4", 4.0, 440)
        out = tmp_path / "with_bed.mp4"

        _run(build_ffmpeg_command(
            MotionCompositeSpec(
                source_video_path=source, output_path=out, outro_path=outro,
                outro_bed_path=bed,
            ),
            ffmpeg, [source, outro],
        ))

        video_s, audio_s = _durations(ffmpeg, out)
        assert abs(video_s - audio_s) <= 0.2, (
            f"audio {audio_s:.3f}s vs video {video_s:.3f}s — the bed must span "
            "the outro without over- or under-running it"
        )

    def test_bed_absent_leaves_legacy_behaviour_untouched(self):
        """Non-narration renders must be unchanged — this ships narration-only."""
        from genlab_core.media.motion_compositor import build_concat_filtergraph

        legacy = build_concat_filtergraph(2, 1080, 1920)
        assert "volume=" not in legacy
        assert "atrim" not in legacy
        assert legacy == build_concat_filtergraph(
            2, 1080, 1920, bed_segment_index=None, bed_input_index=None,
        )
