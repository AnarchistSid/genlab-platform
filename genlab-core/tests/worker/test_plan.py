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

import re

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


# ── the two index spaces ────────────────────────────────────────────────────


def test_build_mattes_is_given_WINDOW_relative_frames_not_absolute():
    """The bug the first real run exposed.

    The vote walks ABSOLUTE frames of the decoded span; build_mattes counts
    0..n-1 WITHIN the window. Left unreconciled the mattes come back built on
    the START of the span rather than the chosen window — a wrong answer with no
    error attached. The backend supplies shims; the plan must use them.
    """
    seen = {"vote": [], "matte": []}

    def absolute_sils(f):
        seen["vote"].append(f)
        return sils_navy_wins(f)

    def window_sils(f):
        seen["matte"].append(f)
        return sils_navy_wins(f)

    P.plan(
        job(candidates=[{"start_s": 674.9, "frames": 96}]),
        frames_for=lambda s, n: list(range(500, 500 + n)),  # absolute, offset
        silhouette_fn=absolute_sils,
        foreground_fn=fg,
        propagate_fn=propagate,
        window_silhouette_fn=window_sils,
        window_foreground_fn=fg,
    )
    assert seen["matte"], "build_mattes never received the window-relative shim"
    assert max(seen["matte"]) < 96, "build_mattes was handed absolute indices"
    assert max(seen["vote"]) >= 500, "the vote was not walking absolute frames"


def test_the_chosen_window_is_announced_to_the_backend():
    """`select_window` is how the backend learns which 96 of the decoded span to
    propagate. Without it SAM2 tracks the whole span — 546 frames against 96 on
    the first real run, at ~4 s each."""
    told = []
    P.plan(
        job(candidates=[{"start_s": 674.9, "frames": 96}]),
        frames_for=frames_for,
        silhouette_fn=sils_navy_wins,
        foreground_fn=fg,
        propagate_fn=propagate,
        select_window=lambda s, n: told.append((s, n)),
    )
    assert told == [(674.9, 96)]


def test_a_backend_without_the_shims_still_works():
    """Older callers pass only the four original backends; they must not break."""
    r = run()
    assert r.ok


def test_finish_does_not_depend_on_the_mattes():
    """`_finish` reads silhouettes and frame indices only.

    It carried an unused `masks` parameter, which made it look like it had to
    run after the propagation. It does not — and running it there put ~96 fresh
    image-predictor calls under the peak the propagation had just built.
    """
    import inspect

    assert "masks" not in inspect.signature(P._finish).parameters
    src = inspect.getsource(P._finish)
    assert "masks" not in src, "_finish reads the mattes again"


def test_finish_reports_progress():
    """A phase that logs nothing is indistinguishable from a hang. The first
    real run spent over an hour here in silence. One line per sampled frame."""
    import inspect

    assert "[finish] frame %d:" in inspect.getsource(P._finish)


# ── the finish: the opponent is DERIVED, not tracked ────────────────────────


def fg_opponent_drops_at(drop: int):
    """Foreground covering BOTH bodies. The finisher is static; the opponent
    stands until `drop` and is then on the floor."""

    def _fg(f):
        m = body(20, 60)
        y0 = 40 if f < drop else 150
        return np.maximum(m, body(70, 110, y0=y0, y1=y0 + 25))

    return _fg


def seed_navy(_f):
    """A COARSE subject mask. It only has to be good enough to subtract."""
    return body(20, 60)


def test_finish_is_the_archived_frame_13_within_one():
    """The archive's finish is frame 13. Sampling every third frame, the
    steepest descent resolves to 12 — the moment the strike lands rather than
    the moment he lands, which is what `window.finish_frame` is for."""
    got = P._finish(
        list(range(96)),
        foreground_fn=fg_opponent_drops_at(13),
        subject_mask_for=lambda i, f: seed_navy(f),
    )
    assert got is not None and abs(got - 13) <= 1, got


def test_fewer_than_three_separable_frames_is_unresolved_not_zero():
    """`_finish` used to read the opponent out of `silhouette_fn`, whose real
    backend returns exactly ONE entry — so `len(sils) < 2` was structurally
    unsatisfiable and every real run fell through to frame 0. Silently. A
    window with no derivable finish must SAY so."""
    got = P._finish(
        list(range(96)),
        foreground_fn=lambda f: body(20, 60),  # foreground IS the subject
        subject_mask_for=lambda i, f: seed_navy(f),
    )
    assert got is None


def test_an_unresolvable_finish_fails_the_plan_before_propagation():
    """The window is designed to BEGIN at the finish and the treatment anchors
    its flash there. No finish, no anchor — and failing here costs the cheap
    phase rather than the expensive one."""
    propagated = []
    r = run(
        coarse_foreground_fn=lambda f: body(20, 60),
        seed_mask_fn=seed_navy,
        propagate_fn=lambda seeds: propagated.append(seeds) or propagate(seeds),
    )
    assert not r.ok and r.reason == P.PlanFailure.FINISH_UNRESOLVED
    assert not propagated, "propagation ran despite an unresolvable finish"


def test_the_finish_never_calls_sam2():
    """It runs on a half-res birefnet foreground and a colour seed. Reaching for
    `silhouette_fn` here is what cost 3722 seconds to answer nothing.

    Pinned structurally rather than by a call count: vote, presence tail and
    the matte seeds all use SAM2 legitimately, so a threshold over the total
    would drift with any of them.
    """
    import ast
    import inspect
    import textwrap

    params = inspect.signature(P._finish).parameters
    assert not any("silhouette" in p for p in params), params
    # the CODE, not the docstring — which explains this history on purpose
    tree = ast.parse(textwrap.dedent(inspect.getsource(P._finish)))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not any("silhouette" in n for n in names), names


def test_the_finish_samples_every_third_frame_on_the_coarse_foreground():
    """32 samples across a 96-frame window, on the half-res foreground."""
    seen = []
    P._finish(
        list(range(96)),
        foreground_fn=lambda f: seen.append(f) or fg_opponent_drops_at(13)(f),
        subject_mask_for=lambda i, f: seed_navy(f),
    )
    assert seen == list(range(0, 96, 3))
    assert P.FINISH_STRIDE == 3


# ── the tie-break is the design intent, not the clock ───────────────────────


def test_tie_break_prefers_the_window_that_starts_on_its_finish():
    """Two unanimous candidates. The LATER one's finish sits at frame 2; the
    earlier one's at 60. The window is designed to begin at the finish, so the
    later one was framed on the event and the earlier on the follow-through."""

    def fg_per_window(f):
        # candidate A starts at frame 0, candidate B at frame 300.
        m = body(20, 60)
        local = f if f < 200 else f - 300
        drop = 61 if f < 200 else 3
        y0 = 40 if local < drop else 150
        return np.maximum(m, body(70, 110, y0=y0, y1=y0 + 25))

    r = run(
        job(candidates=[{"start_s": 0.0, "frames": 96}, {"start_s": 10.0, "frames": 96}]),
        frames_for=lambda s, n: list(range(int(s * 30), int(s * 30) + n)),
        coarse_foreground_fn=fg_per_window,
        seed_mask_fn=seed_navy,
    )
    assert r.ok, r.reason
    assert r.window["start_s"] == 10.0, "the earlier window won on the clock"
    assert r.finish_frame <= 3, r.finish_frame


def test_presence_is_a_filter_not_just_a_number():
    """PRESENCE_MIN_FRAMES sat unused. A window whose subject vanishes for a
    fifth of its length has nothing to track, whatever the vote said."""
    assert P.PRESENCE_MIN_FRAMES == 0.80
    r = run(silhouette_fn=lambda f: {NAVY: np.zeros((H, W), np.float32)})
    assert not r.ok


def test_every_backend_the_module_exports_is_a_parameter_plan_accepts():
    """`_run_plan` splats `plan_backends(...)` straight into `plan(...)`. A key
    added on one side and not the other is a TypeError on the worker, an hour
    into a run, on the Mac — which is the worst place to find it."""
    import inspect

    from genlab_core.action import sam2_backend

    src = inspect.getsource(sam2_backend.plan_backends)
    exported = set(re.findall(r'^\s+"(\w+)":', src[src.rindex("return {") :], re.M))
    accepted = set(inspect.signature(P.plan).parameters)
    assert exported and exported <= accepted, exported - accepted
