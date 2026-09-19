"""The decode rate must be the rate the plan's index math assumes.

Measured on the UFC-05 span: the container is 59.94 fps, the job says 30, and
`frames_for` converts times to indices with the JOB's number. Candidate 24.9 s
resolved to frame 81 of a 59.94 fps decode, which is 23.55 s — every window
landed 1.35 s early and covered 1.6 s of footage instead of 3.2 s. Nothing
errored and the mattes came back 96/96, which is exactly why this is pinned
against a real file rather than reasoned about.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from genlab_core.action.sam2_backend import _container_fps, _decode_frames

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def clip_60fps(tmp_path_factory):
    """One second of 60 fps video — a rate no niche renders at."""
    p = tmp_path_factory.mktemp("fps") / "sixty.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=60:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return str(p)


def test_the_container_rate_is_reported_as_it_is(clip_60fps):
    assert abs(_container_fps(clip_60fps) - 60.0) < 0.01


def test_decoding_without_a_rate_follows_the_container(clip_60fps):
    """The old behaviour. Harmless on its own — wrong the moment an index is
    converted to a time with a different number."""
    assert len(_decode_frames(clip_60fps)) == pytest.approx(60, abs=2)


def test_decoding_at_the_jobs_rate_makes_one_index_one_thirtieth_of_a_second(clip_60fps):
    got = _decode_frames(clip_60fps, fps=30.0)
    assert len(got) == pytest.approx(30, abs=2), len(got)


def test_a_96_frame_window_is_32_seconds_of_footage(clip_60fps):
    """The unit the whole plan is written in: 96 frames, 3.2 s, at 30 fps."""
    got = _decode_frames(clip_60fps, start_s=0.0, duration_s=96 / 30.0, fps=30.0)
    assert len(got) == pytest.approx(30, abs=2)  # the clip is only 1 s long
    full = _decode_frames(clip_60fps, fps=30.0)
    assert len(full) / 30.0 == pytest.approx(1.0, abs=0.1)
