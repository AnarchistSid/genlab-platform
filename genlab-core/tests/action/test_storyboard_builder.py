"""The builder, against UFC-05's recorded numbers.

The worker half is mocked from the archive — the vote, the window, the finish
frame and the mattes are all recorded there, so the expensive half is replayed
rather than re-run. What is exercised for real is the part that decides: window
selection, the vote floor, the shot structure, event placement, and the nine
plan-time gates.
"""

from __future__ import annotations

import pytest
from genlab_core.action import storyboard_builder as B
from genlab_core.storyboard.models import Treatment

#: UFC-05 as it was actually recorded.
UFC05_PLAN = {
    "frames": 96,
    "start_s": 674.9,
    "finish_frame": 13,
    "motion": 16.3,
    "votes": 10,
    "vote_frames": 10,
    "vote_reason": "silhouette vote 10/10, navy",
    "finisher_presence": 0.92,
    "subject_colour": {"hue_deg": 235.0, "hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.10},
    "video_rect": (0, 0, 1920, 943),
    "mag": 2.0361,
    "full_bleed_floor": 1.7778,
    "grade": {"world_luma": 0.40, "subj_luma": 1.75, "vignette": 0.30},
    "cuts": [],
}
BED = lambda: (150.0, 0.385)  # noqa: E731 — the measured v4 bed
CAND = {
    "video_id": "v",
    "title": "Raw highlights",
    "is_highlight": True,
    "download_url": "u",
    "clip_path": "/c.mp4",
}


def build(plan=None, candidate=None, score=0.8):
    return B.build(
        candidate=candidate or dict(CAND),
        niche_id="sports",
        blueprint_id="bp1",
        worker_fn=lambda: plan or dict(UFC05_PLAN),
        bed_fn=BED,
        source_score=score,
        sport="mma",
    )


# ── window selection ────────────────────────────────────────────────────────


def test_a_window_containing_a_cut_is_discarded_not_penalised():
    """A cut inside the window is a different shot, and SAM2 walks from one
    fighter to the other across it. Motion alone returns a highlight MONTAGE."""
    cuts = [1.0]
    got = B.window_candidates(cuts, 20.0, motion_at=lambda s: 100.0 if s < 2 else 1.0)
    assert all(not (c["start_s"] < 1.0 < c["start_s"] + B.WINDOW_S) for c in got)


def test_the_highest_motion_zero_cut_window_wins():
    got = B.window_candidates([], 20.0, motion_at=lambda s: s)  # later = more motion
    assert got[0]["start_s"] == pytest.approx(max(c["start_s"] for c in got), abs=1e-6)


def test_the_measured_score_travels_with_the_window():
    """It used to be computed, used to sort, and then DISCARDED — so the worker,
    which ranks by motion before the expensive vote, had nothing to rank by.
    Filling that gap by hand excluded the archive's own window from a
    verification run and produced a false area-band failure."""
    got = B.window_candidates([], 20.0, motion_at=lambda s: s * 2)
    assert all("motion_score" in c for c in got), got
    for c in got:
        assert c["motion_score"] == pytest.approx(c["start_s"] * 2, abs=1e-6)


def test_a_hand_built_candidate_without_a_score_is_refused():
    """No more invented numbers."""
    import pytest as _p

    with _p.raises(TypeError, match="motion_score"):
        B.build_plan_request("/tmp/x.mp4", [23.2], {}, "sports", "bp1", 1080)
    with _p.raises(KeyError):
        B.build_plan_request("/tmp/x.mp4", [{"start_s": 23.2}], {}, "sports", "bp1", 1080)


def test_a_builder_scored_candidate_reaches_the_job():
    cands = B.window_candidates([], 20.0, motion_at=lambda s: s)
    job = B.build_plan_request("/tmp/x.mp4", cands, {}, "sports", "bp1", 1080)
    assert all(c.get("motion_score") is not None for c in job["candidates"])


def test_no_qualifying_window_returns_empty_not_a_guess():
    assert B.window_candidates([0.5, 1.0, 1.5, 2.0], 3.0, motion_at=lambda s: 1.0) == []


def test_only_the_top_n_candidates_go_to_the_worker():
    """The vote is the expensive half — 20 SAM2 image calls per candidate."""
    got = B.window_candidates([], 60.0, motion_at=lambda s: s)
    assert len(got) == B.MAX_CANDIDATES


# ── the vote gate ───────────────────────────────────────────────────────────


def test_a_unanimous_vote_builds():
    r = build()
    assert r.ok and r.storyboard.subject.unanimous


def test_a_split_vote_is_refused_by_name():
    """Below the floor the 'winner' was a 0.05pp area difference — noise. A reel
    built on the wrong fighter is worse than no reel."""
    r = build({**UFC05_PLAN, "votes": 6})
    assert not r.ok and r.reason == B.BuildSkip.VOTE_TOO_SPLIT and "6/10" in r.detail


def test_exactly_the_floor_passes():
    assert build({**UFC05_PLAN, "votes": B.VOTE_FLOOR}).ok


def test_a_non_action_route_never_reaches_the_worker():
    calls = []

    def worker():
        calls.append(1)
        return dict(UFC05_PLAN)

    r = B.build(
        candidate={
            "video_id": "v",
            "title": "post-match interview",
            "is_highlight": True,
            "download_url": "u",
        },
        niche_id="sports",
        blueprint_id="bp1",
        worker_fn=worker,
        bed_fn=BED,
        source_score=0.8,
    )
    assert not r.ok and r.reason == B.BuildSkip.ROUTE_NOT_ACTION
    assert calls == [], "the expensive half ran for a clip that is not ACTION"


def test_an_absent_worker_is_named_not_silent():
    r = B.build(
        candidate=dict(CAND),
        niche_id="sports",
        blueprint_id="bp1",
        worker_fn=lambda: None,
        bed_fn=BED,
        source_score=0.8,
    )
    assert not r.ok and r.reason == B.BuildSkip.WORKER_UNAVAILABLE


# ── the storyboard it produces ──────────────────────────────────────────────


def test_the_grid_is_integer_frame_at_150bpm():
    """150 BPM at 30 fps is exactly 12 frames, so no event has to round."""
    sb = build().storyboard
    assert sb.beats.period_frames == pytest.approx(12.0)


def test_the_flash_lands_on_the_finish_not_on_peak_motion():
    """v3's real defect: anchored to energy, it fired on frames 93-95 — the last
    three of the segment, over the live ending."""
    sb = build().storyboard
    flashes = [e.frame for e in sb.events if e.kind == "white_flash"]
    assert flashes == [UFC05_PLAN["finish_frame"]]


def test_the_segment_ends_live():
    sb = build().storyboard
    tail = sb.total_frames - 12
    late = [e for e in sb.events if e.frame >= tail and e.kind != "aura"]
    assert late == []


def test_shots_cover_the_window_without_gaps():
    sb = build().storyboard
    assert sb.shots[0].start_frame == 0
    assert sb.shots[-1].end_frame == sb.total_frames - 1
    for a, b in zip(sb.shots, sb.shots[1:], strict=False):
        assert b.start_frame == a.end_frame + 1, f"gap after shot {a.index}"


def test_the_cold_open_replays_the_finish_frame():
    """Open on the moment, then rewind to it — the structure from CONTENT-11."""
    sb = build().storyboard
    assert sb.shots[0].source_frame == UFC05_PLAN["finish_frame"]


def test_the_treatment_is_action_and_the_subject_hue_survives():
    sb = build().storyboard
    assert sb.treatment_is(Treatment.ACTION)
    assert sb.subject.hue_deg == 235.0


def test_the_grade_is_carried_not_defaulted():
    """Per-clip solved values; a default here is the UFC-05 failure again."""
    assert build().storyboard.grade["world_luma"] == 0.40


# ── the gates ───────────────────────────────────────────────────────────────


def test_a_plan_that_fails_a_gate_is_rejected_by_that_gate_s_NAME():
    """'rejected' is not a finding; 'rejected at magnification_floor' is."""
    r = build({**UFC05_PLAN, "mag": 1.2})  # below the full-bleed floor
    assert not r.ok
    assert r.reason.startswith(B.BuildSkip.GATES_FAILED)
    assert ":" in r.reason and r.detail


def test_all_gates_are_reported_even_when_one_fails():
    r = build({**UFC05_PLAN, "mag": 1.2})
    assert len(r.gates) >= 5, "the other gates' verdicts are lost"


def test_a_passing_plan_reports_its_gates_too():
    r = build()
    assert r.ok and r.gates and all(g.passed for g in r.gates)


@pytest.mark.parametrize("finish", [0, 1, 13, 48, 90, 95])
def test_shots_tile_the_window_wherever_the_finish_falls(finish):
    """The regression: a fixed-span table assumed the finish sat mid-window.
    UFC-05's was re-picked to START at the finish (frame 13 of 96), which
    inverted the ramp span, dropped it, and left a hole between build and hit.
    Shots that do not tile are not cosmetic — the renderer reads them per frame.
    """
    shots = B.shot_list(finish, 96, lambda f: 2.0)
    assert shots, f"no shots for finish={finish}"
    assert shots[0].start_frame == 0
    assert shots[-1].end_frame == 95
    for a, b in zip(shots, shots[1:], strict=False):
        assert b.start_frame == a.end_frame + 1, f"gap after shot {a.index} (finish={finish})"
        assert a.end_frame >= a.start_frame


def test_a_one_frame_window_does_not_explode():
    assert B.shot_list(0, 1, lambda f: 2.0)[0].end_frame == 0


def test_an_empty_window_yields_no_shots():
    assert B.shot_list(0, 0, lambda f: 2.0) == []
