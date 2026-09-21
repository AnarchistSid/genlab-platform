"""Pins for still/pv.py — the PV as anime's footage (ANIME-13 §2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from genlab_core.still import pv as P


def _clip(dst: Path, seconds: int = 12) -> Path:
    """A synthetic PV: a still head, a busy middle, a still tail."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s=320x180:d={seconds}:r=24",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=s=320x180:d={seconds}:r=24",
            "-filter_complex",
            "[1:v]trim=0:4,setpts=PTS-STARTPTS[busy];"
            "[0:v]trim=0:4,setpts=PTS-STARTPTS[a];"
            "[0:v]trim=0:4,setpts=PTS-STARTPTS[b];"
            "[a][busy][b]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ],
        check=True,
        timeout=180,
    )
    return dst


class TestMomentSelection:
    def test_peaks_land_in_the_moving_section(self, tmp_path):
        """Motion AND zero cuts. The head and tail are static navy; every
        peak must come from the middle."""
        clip = _clip(tmp_path / "pv.mp4")
        moments = P.peak_moments(clip, n=3, length_s=1.0)
        assert moments, "no moments found in a clip with an obvious busy section"
        assert all(3.5 <= m.start_s <= 8.5 for m in moments), [m.row() for m in moments]

    def test_moments_do_not_overlap(self, tmp_path):
        clip = _clip(tmp_path / "pv.mp4")
        ms = P.peak_moments(clip, n=4, length_s=1.0)
        for a, b in zip(ms, ms[1:]):
            assert not a.overlaps(b), f"{a.row()} overlaps {b.row()}"

    def test_length_outside_the_grammar_is_refused(self, tmp_path):
        clip = _clip(tmp_path / "pv.mp4")
        with pytest.raises(ValueError):
            P.peak_moments(clip, length_s=4.0)
        with pytest.raises(ValueError):
            P.peak_moments(clip, length_s=0.2)

    def test_cold_open_is_one_second_of_the_strongest_motion(self, tmp_path):
        clip = _clip(tmp_path / "pv.mp4")
        m = P.cold_open(clip)
        assert m.kind == P.COLD_OPEN
        assert m.duration_s == 1.0
        assert 3.5 <= m.start_s <= 8.5, m.row()

    def test_head_and_tail_are_skipped(self, tmp_path):
        """A PV opens on a distributor logo and closes on a date slate."""
        clip = _clip(tmp_path / "pv.mp4")
        for m in P.peak_moments(clip, n=5, length_s=1.0):
            assert m.start_s >= P._HEAD_SKIP_S


class TestEndSlate:
    def test_absence_is_reported_as_absence_not_as_a_guess(self, tmp_path):
        """'If present' is load-bearing: a wrong frame labelled 'the title
        card' would be the most confidently wrong shot in the reel."""
        flat = tmp_path / "flat.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=navy:s=320x180:d=9:r=24",
                "-c:v",
                "libx264",
                "-crf",
                "24",
                "-pix_fmt",
                "yuv420p",
                str(flat),
            ],
            check=True,
            timeout=120,
        )
        assert P.text_slate(flat) is None


class TestCutting:
    def test_a_cut_is_full_bleed_portrait_and_bt709(self, tmp_path):
        clip = _clip(tmp_path / "pv.mp4")
        out = tmp_path / "shot.mp4"
        assert P.cut_moment(clip, P.PVMoment(5.0, 1.2, 9.9), out)

        def f(key: str) -> str:
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    f"stream={key}",
                    "-of",
                    "csv=p=0",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return r.stdout.strip().splitlines()[0]

        assert (f("width"), f("height")) == ("1080", "1920")
        assert f("color_space") == "bt709"

    def test_seek_is_frame_accurate(self, tmp_path):
        """-ss BEFORE -i snaps to the nearest keyframe: two starts 40 ms apart
        decoded the SAME frame when that was last got wrong. The tell was two
        identical measurements."""
        clip = _clip(tmp_path / "pv.mp4")
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        assert P.cut_moment(clip, P.PVMoment(5.00, 0.8, 1.0), a)
        assert P.cut_moment(clip, P.PVMoment(5.25, 0.8, 1.0), b)

        def first_frame_bytes(v: Path) -> bytes:
            png = v.with_suffix(".png")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(v), "-frames:v", "1", str(png)],
                check=True,
                timeout=60,
            )
            return png.read_bytes()

        assert first_frame_bytes(a) != first_frame_bytes(b)


def test_download_honours_a_binary_override():
    """A stale yt-dlp caps every format at 360p and looks like a cookie
    problem. Measured on one URL with no cookies either side: 2026.03.03
    offered one format (640x360); 2026.08.19 offered four at 1920x1080."""
    from genlab_core.media import download_top_videos as D

    src = Path(D.__file__).read_text()
    assert 'os.environ.get("YT_DLP_BINARY"' in src
