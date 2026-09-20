"""The finish anchor: two signals that must agree, and a window-invariance gate.

The numbers here are the measured ones. On three windows of the UFC-05 span the
derived-opponent detector returned 24.60 s, 26.20 s and 26.80 s absolute for
what is one strike, and the residual moved 54, 91 and 97 px between adjacent
samples. Those are the failures these pins encode.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from genlab_core.action import finish as F

FPS = 30.0


def pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def fake_ffmpeg(audio: bytes):
    def _run(cmd, capture_output=False):
        return subprocess.CompletedProcess(cmd, 0, stdout=audio, stderr=b"")

    return _run


# ── signal 1: audio onset ───────────────────────────────────────────────────


def test_transient_mode_finds_a_clean_impact_not_the_loudest_moment():
    """A struck object in a quiet room. The onset is the RISE, and a crowd roar
    that peaks later and louder must not win."""
    sr, n = F.ONSET_SR, 96
    a = np.random.default_rng(0).normal(0, 0.01, int(sr * n / FPS)).astype(np.float32)
    a[int(sr * 13 / FPS) : int(sr * 13 / FPS) + sr // 40] += 0.6  # the strike
    roar = int(sr * 45 / FPS)
    a[roar:] += np.linspace(0, 0.9, len(a) - roar)  # the crowd, later
    got = F.audio_onset_frame(
        "x.mp4", 0.0, n, FPS, mode=F.ONSET_MODE_TRANSIENT, run=fake_ffmpeg(pcm(a))
    )
    assert got is not None and abs(got - 13) <= 2, got


def test_level_mode_finds_a_sustained_step():
    """A level that rises and STAYS. What the transient reads as noise."""
    sr, n = F.ONSET_SR, 96
    a = np.random.default_rng(3).normal(0, 0.10, int(sr * n / FPS)).astype(np.float32)
    a[int(sr * 13 / FPS) :] *= 1.9
    got = F.audio_onset_frame("x.mp4", 0.0, n, FPS, run=fake_ffmpeg(pcm(a)))
    assert got is not None and abs(got - 13) <= 6, got


def test_the_default_mode_is_the_one_that_survived_the_gate():
    """Structural, so the default cannot be quietly moved back. The evidence is
    the measurement in `test_the_measured_three_window_spreads`, not a
    synthetic — a fixture tuned until it reproduces a known failure is weaker
    evidence than the failure itself."""
    import inspect

    assert inspect.signature(F.audio_onset_frame).parameters["mode"].default == F.ONSET_MODE_LEVEL


def test_the_measured_three_window_spreads():
    """MEASURED on the UFC-05 span, windows at 23.2 / 23.6 / 24.9 s, absolute
    times in seconds. Recorded here so the gate's verdict on each detector is a
    fact in the suite rather than a note in a docstring.

        detector                   23.2     23.6     24.9    spread
        50 ms first difference    23.600   23.950   27.000   3.400
        0.50 s level shift        25.650   25.650   25.650   0.000

    The archive's finish is 25.333; the level shift lands +0.317 s after it,
    because a crowd reacts after the punch lands.
    """
    transient = [23.600, 23.950, 27.000]
    level = [25.650, 25.650, 25.650]
    assert not F.is_window_invariant(transient)
    assert F.is_window_invariant(level)
    assert F.invariance_spread_s(transient) == pytest.approx(3.400, abs=0.001)
    assert F.invariance_spread_s(level) == pytest.approx(0.0, abs=0.001)
    assert level[0] - 25.333 == pytest.approx(0.317, abs=0.001)


def test_silence_is_no_onset_not_frame_zero():
    for mode in (F.ONSET_MODE_LEVEL, F.ONSET_MODE_TRANSIENT):
        got = F.audio_onset_frame(
            "x.mp4",
            0.0,
            96,
            FPS,
            mode=mode,
            run=fake_ffmpeg(pcm(np.zeros(F.ONSET_SR * 4, np.float32))),
        )
        assert got is None, mode


# ── signal 2: subject velocity ──────────────────────────────────────────────


def masks_from_x(xs, area=0.01, h=200, w=600):
    """One garment blob per frame, centred on x — what `seed_mask_fn` returns."""
    out = {}
    half = int((area * h * w) ** 0.5 / 2) or 4
    for f, x in enumerate(xs):
        m = np.zeros((h, w), np.float32)
        m[h // 2 - half : h // 2 + half, max(int(x) - half, 0) : int(x) + half] = 1.0
        out[f] = m
    return out


def test_velocity_peak_is_toward_the_opponent_not_merely_fast():
    """Backing off is as fast as committing. The sign is what makes it a strike."""
    x = [100.0 + i for i in range(10)]
    x += [109 + 15 * k for k in (1, 2, 3)]  # lunge right, frames 10-12
    x += [x[-1] + 1, x[-1] + 2]
    x += [x[-1] - 20 * k for k in (1, 2, 3)]  # a BIGGER retreat, 15-17
    x += [x[-1] + 1, x[-1] + 2]
    sm = F.velocity_samples(masks_from_x(x))
    assert F.subject_velocity_peak(sm, opponent_on_right=True) == 10
    assert F.subject_velocity_peak(sm, opponent_on_right=False) == 15


def test_a_collapsed_seed_is_dropped_before_it_becomes_a_lunge():
    """MEASURED on UFC-05 frame 44: the seed covered 0.11% of frame against a
    0.5% norm and its centroid sat ~200 px away. Unbounded and unfiltered, that
    read as +94.85 px/frame — no fighter moves 95 px in a thirtieth of a second.
    """
    masks = masks_from_x([100.0 + i for i in range(20)])
    collapsed = np.zeros((200, 600), np.float32)
    collapsed[100:102, 500:503] = 1.0  # a few pixels, far to the right
    masks[12] = collapsed
    sm = F.velocity_samples(masks)
    assert 12 not in [f for f, _, _ in sm], sm


def test_the_audio_bracket_is_asymmetric_because_crowds_react_late():
    lo, hi = F.audio_bound(74, 30.0)
    assert (lo, hi) == (59, 77)
    assert 74 - lo == 15 and hi - 74 == 3


def test_the_peak_is_taken_inside_the_bracket():
    """Unbounded, the pick was 34 frames from where the crowd said the strike
    was. The bracket is what makes a fragile signal usable."""
    # An earlier move that is FASTER (+30/frame) than the real lunge (+12), and
    # continuous enough to survive the filter — so only the bracket separates
    # them. Steps stay under the 40 px continuity limit, as a real body does.
    x = [100.0] * 10
    x += [100.0 + 30 * k for k in range(1, 6)]  # frames 10-14, the decoy
    x += [x[-1]] * 5  # frames 15-19, still
    x += [x[-1] + 12 * k for k in range(1, 8)]  # frames 20-26, the lunge
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    free = F.subject_velocity_peak(sm, opponent_on_right=True)
    bounded = F.subject_velocity_peak(sm, opponent_on_right=True, bound=(18, 26))
    assert free is not None and free < 18, free
    assert bounded is not None and 18 <= bounded <= 26, bounded


def test_no_usable_samples_in_the_bracket_is_none():
    sm = F.velocity_samples(masks_from_x([100.0 + i for i in range(20)]))
    assert F.subject_velocity_peak(sm, opponent_on_right=True, bound=(50, 60)) is None


# ── signal 3: the residual, made honest ─────────────────────────────────────


def test_continuity_drops_the_measured_jumps():
    """The real trace from window 23.60: 347, 293, 281, 259, 271, 362, 265.
    The 54, 91 and 97 px steps are the mask changing subject."""
    raw = [(f, y, 0.15) for f, y in zip(range(69, 90, 3), [347, 293, 281, 259, 271, 362, 265])]
    kept = [f for f, _, _ in F.continuous_samples(raw)]
    assert 84 not in kept, kept  # 271 -> 362 is a 91 px jump
    assert kept == [72, 75, 78, 81, 87], kept  # the coherent run survives


def test_one_bad_sample_does_not_drag_the_reference_with_it():
    raw = [(0, 100.0, 0.1), (1, 300.0, 0.1), (2, 110.0, 0.1)]
    assert [f for f, _, _ in F.continuous_samples(raw)] == [0, 2]


def test_continuity_does_not_depend_on_which_sample_came_first():
    """A forward scan anchored on sample 0 keeps whatever outlier happened to be
    first. The longest run has no such preference — the same property the
    invariance gate tests for, one level down."""
    trace = [347.0, 293.0, 281.0, 259.0, 271.0, 362.0, 265.0]
    with_head = [y for _, y, _ in F.continuous_samples([(i, y, 0.15) for i, y in enumerate(trace)])]
    without = [
        y for _, y, _ in F.continuous_samples([(i, y, 0.15) for i, y in enumerate(trace[1:], 1)])
    ]
    assert with_head == without, (with_head, without)


def test_largest_component_ignores_the_crowd():
    m = np.zeros((200, 200), np.float32)
    m[40:160, 20:80] = 1.0  # a body
    m[10:20, 150:190] = 1.0  # crowd, up in the corner
    big = F.largest_component(m)
    assert big[100, 50] > 0.5 and big[15, 170] < 0.5


# ── the anchor ──────────────────────────────────────────────────────────────


def test_the_video_picks_and_the_audio_only_brackets():
    """The audio lands ~0.3 s late by construction. Anchoring ON it would fire
    the flash on the crowd rather than the punch."""
    r = F.resolve(audio_f=74, velocity_f=66, bound=(59, 77), samples_in_bound=9)
    assert r.ok and r.frame == 66
    assert r.frame < r.audio_frame, "the pick must precede the crowd's reaction"


def test_no_audio_means_no_bracket_and_no_finish():
    assert F.resolve(None, 12).reason == F.FinishFailure.NO_AUDIO


def test_no_velocity_inside_the_bracket_is_unresolved_not_the_audio_frame():
    """Falling back to the audio frame would ship a finish 0.3 s late and call
    it measured."""
    r = F.resolve(74, None, bound=(59, 77))
    assert not r.ok and r.reason == F.FinishFailure.NO_VELOCITY
    assert r.frame is None


def test_the_descent_disagrees_out_loud_but_never_vetoes():
    r = F.resolve(audio_f=74, velocity_f=66, descent_f=42, bound=(59, 77))
    assert r.ok and r.frame == 66
    assert r.descent_agrees is False


def test_the_result_records_how_the_pick_was_made():
    r = F.resolve(74, 66, bound=(59, 77), samples_in_bound=3, used_tracker=True)
    assert r.used_tracker and r.samples_in_bound == 3 and r.bound == (59, 77)


# ── the gate ────────────────────────────────────────────────────────────────


def test_three_windows_that_agree_pass_the_invariance_gate():
    """Within 0.2 s — six frames — of each other."""
    assert F.is_window_invariant([25.33, 25.40, 25.28])


def test_a_detector_that_returns_the_max_in_what_it_saw_fails_the_gate():
    """The measured failure: 24.60, 26.20, 26.80 from three windows of one
    strike. Each is the largest descent inside its own window."""
    assert not F.is_window_invariant([24.60, 26.20, 26.80])
    assert F.invariance_spread_s([24.60, 26.20, 26.80]) > F.INVARIANCE_SPREAD_S


def test_one_window_cannot_disagree_with_itself():
    assert F.is_window_invariant([25.33])
    assert F.is_window_invariant([])


# ── the strike is the rising edge, not the peak ─────────────────────────────


def test_the_edge_is_picked_over_a_later_higher_peak():
    """Velocity keeps climbing into the FOLLOW-THROUGH, so the maximum lands
    after contact. Measured on UFC-05: the peak sat 6 frames after the archive's
    finish with the archive's own frame inside the bracket and available."""
    # commits at 10 (+12/frame), keeps accelerating to a peak at 16 (+34/frame)
    x = [100.0] * 10
    for step in (12, 14, 18, 22, 26, 30, 34, 34, 34):
        x.append(x[-1] + step)
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    got = F.subject_velocity_peak(sm, opponent_on_right=True)
    # the argmax of this profile is the last frame; the edge must be well before
    assert got is not None
    assert got <= 13 and got < len(x) - 4, f"{got} — picked the follow-through"


def test_the_edge_threshold_is_a_share_of_the_brackets_own_maximum():
    """Not an absolute px/frame: a slow grapple and a fast strike have different
    maxima and the same shape."""
    assert F.EDGE_FRACTION == 0.70
    slow = [100.0] * 8 + [100.0 + 3 * k for k in range(1, 10)]
    fast = [100.0] * 8 + [100.0 + 30 * k for k in range(1, 10)]
    a = F.subject_velocity_peak(
        F.velocity_samples(masks_from_x(slow, h=200, w=900)), opponent_on_right=True
    )
    b = F.subject_velocity_peak(
        F.velocity_samples(masks_from_x(fast, h=200, w=900)), opponent_on_right=True
    )
    assert a == b, (a, b)


def test_no_motion_toward_the_opponent_is_none_not_frame_zero():
    x = [100.0 - i for i in range(20)]  # retreating the whole time
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    assert F.subject_velocity_peak(sm, opponent_on_right=True) is None


# ── a maximum on the bracket edge is a maximum of the bracket ───────────────


def test_a_pick_on_the_lower_edge_widens_once_and_resolves():
    """MEASURED on UFC-05 window 24.9: pick and peak were both frame 7 of a
    (7, 25) bracket — the signal was still rising where the bracket stopped."""
    # a localised rise: accelerate into the strike, decelerate after it
    x = [100.0] * 10
    for step in (5, 10, 20, 30, 20, 10, 5):
        x.append(x[-1] + step)
    x += [x[-1]] * 12
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    f, bound = F.pick_with_edge_guard(sm, opponent_on_right=True, bound=(13, 25))
    assert f is not None, "widening should have found the real rise"
    assert bound[0] < 13, bound


def test_still_on_an_edge_after_widening_is_unresolved():
    """A monotone ramp has its maximum wherever you stop looking."""
    x = [100.0 + 10 * k for k in range(30)]
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    f, _ = F.pick_with_edge_guard(sm, opponent_on_right=True, bound=(10, 20))
    assert f is None


def test_a_pick_in_the_middle_is_left_alone():
    x = [100.0] * 8 + [100.0 + 12 * k for k in range(1, 6)] + [160.0] * 12
    sm = F.velocity_samples(masks_from_x(x, h=200, w=900))
    f, bound = F.pick_with_edge_guard(sm, opponent_on_right=True, bound=(4, 18))
    assert f is not None and bound == (4, 18)
