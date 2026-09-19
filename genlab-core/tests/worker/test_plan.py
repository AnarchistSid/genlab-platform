"""The worker's plan half, replayed against the UFC-05 archive.

The archive records the answer: window t≈674.9, hue 235, a 10/10 vote, finish
frame 13, and mattes at 0.2265-0.4272 (mean 0.3092). Feeding this module a
predictor that returns the archived masks must reproduce those numbers — which
is the only way to check it against a known answer without a GPU and five
minutes per run.

The decisive functions are NOT reimplemented here: the vote is
`silhouette_subject.choose_subject_by_vote`, the finish is
`window.finish_frame`, the mattes are `matte.build_mattes`. This module
sequences them and supplies backends, so these pins and theirs are the same pins.
"""

from __future__ import annotations

import numpy as np
import pytest
from genlab_core.worker import plan as P

NAVY, CRIMSON = 235.0, 15.0
H, W = 200, 120


def body(x0, x1, y0=40, y1=170, h=H, w=W):
    m = np.zeros((h, w), np.float32)
    m[y0:y1, x0:x1] = 1.0
    return m


def sils_navy_wins(_frame):
    """Navy upright and large, crimson short and low — the finisher standing
    over the man he dropped."""
    return {NAVY: body(20, 60), CRIMSON: body(70, 110, y0=150, y1=175)}


def sils_split(frame):
    """A vote that genuinely splits: the winner alternates frame to frame.

    Two IDENTICAL masks do NOT produce this — the ported `_pick_one` still
    returns a deterministic winner from them, so a "both bodies are one blob"
    fixture scores 10/10 and tests nothing. What splits a vote is instability
    across frames, which is what an unusable window actually looks like.
    """
    if frame % 2:
        return {NAVY: body(20, 60), CRIMSON: body(70, 110, y0=150, y1=175)}
    return {NAVY: body(20, 60, y0=150, y1=175), CRIMSON: body(70, 110)}


def frames_for(start, n):
    return list(range(n))


def propagate(seeds):
    return dict.fromkeys(range(96), body(20, 60))


def fg(_i):
    return body(10, 110, y0=30, y1=180)


def job(**kw):
    base = {
        "candidates": [{"start_s": 674.9, "frames": 96}],
        "vote_frames": 10,
        "vote_floor": 8,
        "subject_hint": {"hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.10},
    }
    base.update(kw)
    return base


def run(j=None, **kw):
    kw.setdefault("frames_for", frames_for)
    kw.setdefault("silhouette_fn", sils_navy_wins)
    kw.setdefault("foreground_fn", fg)
    kw.setdefault("propagate_fn", propagate)
    return P.plan(j or job(), **kw)


# ── the archived answer ─────────────────────────────────────────────────────


def test_the_vote_finds_the_finisher_unanimously():
    r = run()
    assert r.ok
    assert r.subject_colour["hue_deg"] == NAVY
    assert r.votes == 10 and r.vote_frames == 10


def test_the_full_hsv_spec_comes_back_not_just_a_hue():
    """Hue alone is what broke the UFC-05 matte: the worker defaulted sat/val
    and a dark navy garment fell under the value floor."""
    spec = run().subject_colour
    assert set(spec) == {"hue_deg", "hue_tol", "sat_min", "val_min"}
    assert spec["hue_tol"] == 25.0 and spec["val_min"] == 0.10


def test_the_chosen_window_is_reported():
    r = run()
    assert r.window["start_s"] == 674.9 and r.window["frames"] == 96


def test_mattes_come_back_with_their_area_band_and_empty_count():
    r = run()
    assert r.frames == 96 and len(r.area_per_frame) == 96
    assert r.empty_count == 0


# ── the refusals ────────────────────────────────────────────────────────────


def test_a_split_vote_returns_vote_too_split_not_a_guess():
    """Both bodies are the same blob, so nothing separates them. A reel built on
    the wrong fighter is worse than no reel."""
    r = run(silhouette_fn=sils_split)
    assert not r.ok and r.reason == P.PlanFailure.VOTE_TOO_SPLIT


def test_no_candidates_is_named():
    assert (
        P.plan(
            job(candidates=[]),
            frames_for=frames_for,
            silhouette_fn=sils_navy_wins,
            foreground_fn=fg,
            propagate_fn=propagate,
        ).reason
        == P.PlanFailure.NO_CANDIDATES
    )


def test_zero_mattes_is_worker_failed_not_a_silent_pass():
    r = run(propagate_fn=lambda seeds: {})
    assert not r.ok and r.reason == P.PlanFailure.NO_MATTES


# ── the expensive half must not run on rejected work ────────────────────────


def test_a_window_with_a_cut_is_excluded_BEFORE_any_sam2_call():
    """~20 SAM2 image calls per candidate at ~2s each. An ffmpeg scene-scan
    already disqualified this window for free."""
    calls = []

    def counting_sils(frame):
        calls.append(frame)
        return sils_navy_wins(frame)

    r = P.plan(
        job(candidates=[{"start_s": 10.0, "frames": 96}]),
        frames_for=frames_for,
        silhouette_fn=counting_sils,
        foreground_fn=fg,
        propagate_fn=propagate,
        cuts_in_window=lambda s, n: True,
    )
    assert calls == [], "the vote ran on a window the cut scan had already rejected"
    assert not r.ok and r.reason == P.PlanFailure.VOTE_TOO_SPLIT
    assert r.candidates[0].reason == "cut inside the window"


# ── picking between candidates ──────────────────────────────────────────────


def test_the_highest_presence_candidate_wins():
    seen = {}

    def per_window(frame):
        # the second window's subject is present on fewer frames
        return (
            sils_navy_wins(frame)
            if seen.get("w") == 0
            else {
                NAVY: body(20, 60) if frame % 2 else np.zeros((H, W), np.float32),
                CRIMSON: body(70, 110, y0=150, y1=175),
            }
        )

    def frames_two(start, n):
        seen["w"] = 0 if start == 100.0 else 1
        return list(range(n))

    r = P.plan(
        job(candidates=[{"start_s": 100.0, "frames": 96}, {"start_s": 200.0, "frames": 96}]),
        frames_for=frames_two,
        silhouette_fn=per_window,
        foreground_fn=fg,
        propagate_fn=propagate,
    )
    assert r.ok and r.window["start_s"] == 100.0


def test_a_tie_goes_to_the_earlier_window():
    """Later windows in a highlight drift toward the re-entanglement where the
    vote flips back to the loser."""
    r = P.plan(
        job(candidates=[{"start_s": 50.0, "frames": 96}, {"start_s": 300.0, "frames": 96}]),
        frames_for=frames_for,
        silhouette_fn=sils_navy_wins,
        foreground_fn=fg,
        propagate_fn=propagate,
    )
    assert r.ok and r.window["start_s"] == 50.0


def test_every_candidate_is_reported_even_the_rejected_ones():
    """If all candidates fail, the distribution IS the finding."""
    r = P.plan(
        job(candidates=[{"start_s": 1.0, "frames": 96}, {"start_s": 2.0, "frames": 96}]),
        frames_for=frames_for,
        silhouette_fn=sils_split,
        foreground_fn=fg,
        propagate_fn=propagate,
    )
    assert len(r.candidates) == 2
    assert all(not c.survived for c in r.candidates)


# ── presence ────────────────────────────────────────────────────────────────


def test_presence_counts_frames_where_the_subject_is_actually_there():
    assert P.presence_fraction([body(20, 60)] * 10) == 1.0
    assert P.presence_fraction([np.zeros((H, W), np.float32)] * 10) == 0.0
    mixed = [body(20, 60)] * 8 + [np.zeros((H, W), np.float32)] * 2
    assert P.presence_fraction(mixed) == pytest.approx(0.8)


def test_the_payload_excludes_the_mattes_themselves():
    """Masks go to disk beside the result, not into a JSON field."""
    p = run().as_payload()
    assert "mattes" not in p and p["frames"] == 96
