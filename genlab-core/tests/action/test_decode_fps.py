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
from genlab_core.action.sam2_backend import FpsMismatch, _container_fps, _decode_frames
from genlab_core.action.window import container_fps, motion_profile

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


def test_a_mismatched_rate_is_refused_not_logged(clip_60fps):
    """Part 29 §1. This used to decode happily at the job's rate.

    Asking for 30 against a 60 fps container is the shape that made every
    window land 1.35 s early while returning 96/96 mattes. It now refuses.
    """
    with pytest.raises(FpsMismatch, match=r"fps_mismatch:30\.00 vs 60\.00"):
        _decode_frames(clip_60fps, fps=30.0)


def test_the_duration_as_fps_case_is_refused(clip_60fps):
    """The actual sports-c787edca13 bug: `prof, fps = motion_profile(clip)`.

    The clip was 40.658 s long, so the plan ran at "fps = 40.66" against a
    60 fps container. 94 minutes of compute, every internal gate green, and
    the window it chose was a post-fight interview.
    """
    with pytest.raises(FpsMismatch, match=r"fps_mismatch:40\.66 vs 60\.00"):
        _decode_frames(clip_60fps, fps=40.658141)


def test_decoding_at_the_container_rate_is_allowed(clip_60fps):
    got = _decode_frames(clip_60fps, fps=60.0)
    assert len(got) == pytest.approx(60, abs=2), len(got)


def test_the_builders_rate_and_the_workers_rate_come_from_one_function(clip_60fps):
    """`window.container_fps` is what the builder puts in MatteRequest.fps,
    and `_container_fps` is what the worker asserts against. If they ever
    disagree the assertion is theatre."""
    assert container_fps(clip_60fps) == pytest.approx(_container_fps(clip_60fps), abs=0.01)


def test_motion_profile_second_field_is_a_duration_not_a_rate(clip_60fps):
    """The mis-binding was legal because both fields are floats."""
    prof = motion_profile(clip_60fps)
    assert prof.duration_s == pytest.approx(1.0, abs=0.2), "second field is SECONDS"
    assert len(prof.values) == pytest.approx(60, abs=3), "one sample per frame"
    assert prof.duration_s != pytest.approx(container_fps(clip_60fps), abs=1.0), (
        "on this fixture a duration and a rate are far apart; on the real clip "
        "they were 40.658 and 60.0, and binding one to the other was silent"
    )


def test_the_plan_builder_does_not_bind_the_profile_to_a_rate():
    """Structural pin: the exact line that caused this must not come back.

    `prof, fps = motion_profile(clip)` reads fine and is wrong. Grepping for
    it is cheap; the alternative is discovering it again from a 94-minute job
    whose output looked clean.
    """
    import ast
    import inspect

    from genlab_core.pipeline.stages import craft_render

    src = inspect.getsource(craft_render)
    tree = ast.parse(src)

    # AST, not grep: the comment explaining this bug quotes the bad line
    # verbatim, and a substring search cannot tell code from prose.
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        fn = getattr(call, "func", None)
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "motion_profile":
            continue
        for target in node.targets:
            if isinstance(target, ast.Tuple) and len(target.elts) == 2:
                second = target.elts[1]
                bound = getattr(second, "id", "")
                if bound in {"fps", "rate", "framerate", "fps_"}:
                    offenders.append(f"line {node.lineno}: bound to {bound!r}")

    assert not offenders, (
        "motion_profile's second field is a DURATION in seconds, not a rate: "
        + "; ".join(offenders)
        + ". Take the frame rate from window.container_fps."
    )
    assert "container_fps(clip)" in src, "the rate must come from the container"


def test_a_category_less_niche_searches_more_than_a_category_one():
    """Anime has no YouTube category, so its keyword-search cap IS its supply.

    The others get `mostPopular` for their category and use search as a top-up.
    Measured live on 2026-09-20 with three of anime's eight keywords: 45 raw
    results, 25 past relevance, 21 clearing the velocity floor — while the
    pipeline ingested 2. The cap was the ceiling, and quota was never the
    constraint (321 of 10,000 units used).
    """
    import inspect

    from genlab_core.media import trending_video_fetcher as T

    src = inspect.getsource(T)
    assert "_max_searches = 6 if not _has_category else 2" in src
    assert len(T.NICHE_SEARCH_KEYWORDS["anime"]) >= 6, (
        "the cap is pointless if fewer keywords are defined than it allows"
    )
