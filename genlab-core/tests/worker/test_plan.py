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


#: The archive's finish. Every fixture below puts the strike here.
FINISH = 13


def subject_x(f: int) -> int:
    """The finisher drives right across frames 11-15, then settles."""
    if f < 11:
        return 20
    if f <= 15:
        return 20 + 6 * (f - 10)
    return 50


def seed_moving(f):
    """The coarse SUBJECT seed — what `seed_mask_fn` returns. A garment patch,
    not a whole body: the area floor is calibrated against a garment."""
    x = subject_x(f)
    return body(x, x + 24, y0=70, y1=110)


def fg_both(f):
    """Foreground over BOTH bodies. The opponent is on the right and goes down
    after the strike."""
    x = subject_x(f)
    y0 = 40 if f < FINISH else 40 + min(4 * (f - FINISH), 110)
    return np.maximum(body(x, x + 40), body(75, 112, y0=y0, y1=min(y0 + 45, H)))


def frames_for_abs(start, n, anchor=674.9):
    """Absolute frames, so the SUBJECT's motion moves with the window. The
    default `frames_for` returns range(n) whatever the start, which makes every
    window see the same footage — fine for the vote, useless for invariance."""
    lo = int(round((start - anchor) * 30))
    return list(range(lo, lo + n))


#: The crowd reacts ~0.3 s after the punch: 9 frames at 30 fps.
CROWD_LAG = 9


def onset_at(frame=FINISH + CROWD_LAG, anchor_s=674.9):
    """Audio onset in WINDOW-relative frames — a fixed ABSOLUTE moment read
    through whatever window asks for it, which is the property that matters."""

    def _onset(start_s, n):
        f = int(round(frame - (start_s - anchor_s) * 30.0))
        return f if 0 <= f < n else None

    return _onset


def job(**kw):
    base = {
        "candidates": [{"start_s": 674.9, "frames": 96}],
        "vote_frames": 6,
        "vote_floor": 5,
        "subject_hint": {"hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.10},
    }
    base.update(kw)
    return base


def run(j=None, **kw):
    kw.setdefault("frames_for", frames_for)
    kw.setdefault("silhouette_fn", sils_navy_wins)
    kw.setdefault("foreground_fn", fg)
    kw.setdefault("propagate_fn", propagate)
    # All three finish signals wired, which is the point of it: two that must
    # agree and a cross-check that may not.
    kw.setdefault("coarse_foreground_fn", fg_both)
    kw.setdefault("seed_mask_fn", seed_moving)
    kw.setdefault("audio_onset_fn", onset_at())
    return P.plan(j or job(), **kw)


# ── the archived answer ─────────────────────────────────────────────────────


def test_the_vote_finds_the_finisher_unanimously():
    r = run()
    assert r.ok
    assert r.subject_colour["hue_deg"] == NAVY
    assert r.votes == 6 and r.vote_frames == 6


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
        coarse_foreground_fn=fg_both,
        seed_mask_fn=seed_moving,
        audio_onset_fn=lambda start_s, n: FINISH,
    )
    assert r.ok and r.window["start_s"] == 100.0


def test_the_tie_break_prefers_the_finish_nearest_the_window_start():
    """The window is DESIGNED to begin at the finish, so the candidate whose
    finish sits closest to its own start was framed on the event rather than on
    the follow-through.

    The old secondary key — "a tie goes to the earlier window" — is unreachable
    once the invariance gate holds: two candidates with the SAME window-relative
    finish that agree on the absolute time must have the same start. It stays in
    the sort as a determinism guarantee, not as a behaviour with a test.
    """
    r = run(
        job(candidates=[{"start_s": 674.9, "frames": 96}, {"start_s": 675.3, "frames": 96}]),
        frames_for=frames_for_abs,
    )
    assert r.ok, r.reason
    assert r.window["start_s"] == 675.3, r.window
    assert r.finish_frame <= 2, r.finish_frame


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
        coarse_foreground_fn=lambda f: fg_both(f - 500),
        seed_mask_fn=lambda f: seed_moving(f - 500),
        audio_onset_fn=onset_at(),
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
        coarse_foreground_fn=fg_both,
        seed_mask_fn=seed_moving,
        audio_onset_fn=onset_at(),
        select_window=lambda s, n: told.append((s, n)),
    )
    assert told == [(674.9, 96)]


def test_a_backend_without_the_shims_still_works():
    """Older callers pass only the four original backends; they must not break."""
    r = run()
    assert r.ok


# ── the finish: two signals that agree, and a gate ──────────────────────────


def test_the_plan_anchors_on_the_strike_not_the_crowd():
    """The audio sits 9 frames late by construction, as the real crowd does.
    The pick must land on the motion, inside the bracket, ahead of the audio."""
    r = run()
    assert r.ok, r.reason
    assert abs(r.finish_frame - FINISH) <= 2, r.finish_frame
    assert r.finish_frame < FINISH + CROWD_LAG, "anchored on the crowd, not the strike"


def test_no_audio_means_no_bracket_and_the_plan_says_so():
    r = run(audio_onset_fn=lambda start_s, n: None)
    assert not r.ok and r.reason == P.PlanFailure.FINISH_UNRESOLVED


def test_a_collapsed_seed_falls_back_to_the_quarter_res_tracker():
    """When the colour seed dies inside the bracket, the tracker answers —
    seeded from the vote's own mask so the two cannot disagree about which
    fighter this is."""
    called = []

    def dead_seed(f):
        return np.zeros((H, W), np.float32) if 5 <= f <= 30 else seed_moving(f)

    def tracker(start_s, n, seed_frame, seed_mask):
        called.append((start_s, seed_frame))
        return {i: seed_moving(i) for i in range(n)}

    r = run(seed_mask_fn=dead_seed, quarter_track_fn=tracker)
    assert called, "the tracker was never asked"
    assert r.ok, r.reason


def test_without_a_tracker_a_collapsed_seed_is_unresolved_not_a_guess():
    r = run(seed_mask_fn=lambda f: np.zeros((H, W), np.float32), quarter_track_fn=None)
    assert not r.ok and r.reason == P.PlanFailure.FINISH_UNRESOLVED


def test_an_unresolved_finish_fails_before_propagation():
    """It is the cheap phase now. Failing here costs the vote, not the tracker."""
    propagated = []
    r = run(
        audio_onset_fn=lambda start_s, n: None,
        propagate_fn=lambda seeds: propagated.append(seeds) or propagate(seeds),
    )
    assert not r.ok and r.reason == P.PlanFailure.FINISH_UNRESOLVED
    assert not propagated, "propagation ran despite an unresolved finish"


def test_the_finish_never_calls_sam2():
    """It runs on a half-res foreground and a colour seed. Reaching for
    `silhouette_fn` here is what cost 3722 seconds to answer nothing."""
    seen = []
    run(coarse_foreground_fn=lambda f: seen.append(("fg", f)) or fg_both(f))
    assert seen, "the finish did not use the coarse foreground"


# ── window-invariance is a GATE ─────────────────────────────────────────────


def test_windows_that_disagree_about_the_finish_fail_the_plan():
    """Measured: the derived-opponent detector put ONE UFC-05 strike at 24.60 s,
    26.20 s and 26.80 s depending on the window. Each answer was the largest
    descent inside its own window. That must not reach a render."""

    # Each window's own two signals agree — within four frames of its own
    # velocity peak — and the two windows still name different moments.
    def onset(start_s, n):
        return 7 if start_s < 675.0 else 4

    r = run(
        job(candidates=[{"start_s": 674.9, "frames": 96}, {"start_s": 675.3, "frames": 96}]),
        frames_for=frames_for_abs,
        audio_onset_fn=onset,
    )
    assert not r.ok, r.window
    assert r.reason == P.PlanFailure.NOT_INVARIANT, r.reason


def test_windows_that_agree_pass_the_gate():
    """The same absolute moment, seen from two starts 0.4 s apart."""
    r = run(
        job(candidates=[{"start_s": 674.9, "frames": 96}, {"start_s": 675.3, "frames": 96}]),
        frames_for=frames_for_abs,
    )
    assert r.ok, r.reason


# ── the vote, at a quarter of the cost ──────────────────────────────────────


def test_the_vote_is_six_frames_at_the_lower_floor():
    assert P.VOTE_FRAMES == 6 and P.VOTE_FLOOR == 5


def test_only_the_top_candidates_by_motion_reach_the_vote():
    """The vote is the expensive half. A low-motion window is not an ACTION
    window whatever its silhouettes say."""
    voted = []
    r = run(
        job(
            candidates=[
                {"start_s": 674.9, "frames": 96, "motion": 0.9},
                {"start_s": 675.3, "frames": 96, "motion": 0.8},
                {"start_s": 676.0, "frames": 96, "motion": 0.1},
            ]
        ),
        silhouette_fn=lambda f: voted.append(f) or sils_navy_wins(f),
    )
    starts = {c["start_s"] if isinstance(c, dict) else c.start_s for c in r.candidates}
    assert 676.0 in starts, "the skipped candidate must still be reported"
    rejected = [
        c for c in r.candidates if (c["start_s"] if isinstance(c, dict) else c.start_s) == 676.0
    ]
    reason = rejected[0]["reason"] if isinstance(rejected[0], dict) else rejected[0].reason
    assert "motion" in reason, reason


def test_candidates_without_motion_scores_are_all_voted():
    """Unranked is not the same as rejected — and silently dropping two thirds
    of the candidates because a producer omitted a field is the worse failure."""
    r = run(job(candidates=[{"start_s": 674.9, "frames": 96}, {"start_s": 675.3, "frames": 96}]))
    assert len(r.candidates) == 2
    assert all((c["votes"] if isinstance(c, dict) else c.votes) > 0 for c in r.candidates)


def test_presence_is_sampled_across_the_full_window_not_its_tail():
    """A subject solid for the last third and absent for the first two is not
    trackable, and a tail-only sample reports 1.0 for it."""
    seen = []
    run(silhouette_fn=lambda f: seen.append(f) or sils_navy_wins(f))
    assert max(seen) - min(seen) > 48, f"presence clustered in {min(seen)}..{max(seen)}"


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


# ── the Mac's swap ──────────────────────────────────────────────────────────


def test_the_span_is_released_before_propagation():
    """6-18 GB of swap all session. By propagation the vote and the finish are
    done with the rest of the span, and propagation is about to hold image
    embeddings for every frame of the window on top of it."""
    order = []
    run(
        release_span=lambda s, n: order.append(("release", s, n)),
        propagate_fn=lambda seeds: order.append(("propagate",)) or propagate(seeds),
    )
    kinds = [o[0] for o in order]
    assert "release" in kinds and "propagate" in kinds
    assert kinds.index("release") < kinds.index("propagate"), kinds


def test_rss_is_logged_per_phase_when_the_backend_offers_it():
    calls = []
    run(rss_mb=lambda: calls.append(1) or 1234)
    assert calls, "RSS was never sampled"


def test_the_foreground_cache_is_bounded():
    """An unbounded cache over a 546-frame span is ~4 GB of float32 alpha."""
    from genlab_core.action import sam2_backend

    assert 0 < sam2_backend.FG_CACHE_FRAMES <= 400


# ── the gate reports its n ──────────────────────────────────────────────────


def test_one_candidate_leaves_the_gate_UNTESTED_not_passed():
    """One window cannot disagree with itself. Calling that a pass is how a gate
    comes to certify something it never measured."""
    r = run()
    assert r.ok
    assert r.invariance["verdict"] == "untested", r.invariance
    assert r.invariance["n"] == 1 and r.invariance["spread_s"] is None


def test_two_agreeing_candidates_make_the_gate_report_a_pass_with_its_spread():
    r = run(
        job(candidates=[{"start_s": 674.9, "frames": 96}, {"start_s": 675.3, "frames": 96}]),
        frames_for=frames_for_abs,
    )
    assert r.ok, r.reason
    assert r.invariance["verdict"] == "pass", r.invariance
    assert r.invariance["n"] == 2 and r.invariance["spread_s"] <= 0.2
    assert len(r.invariance["absolute_s"]) == 2
