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


def test_velocity_peak_is_toward_the_opponent_not_merely_fast():
    """Backing off is as fast as committing. The sign is what makes it a strike."""
    # A SUSTAINED move, not a one-frame displacement: a single displaced
    # sample is a spike in BOTH directions and tests the smoother, not the sign.
    x = [100.0 + i for i in range(10)]  # drifting right, slowly
    x += [109 + 15 * k for k in (1, 2, 3)]  # lunge right, frames 10-12
    x += [x[-1] + 1, x[-1] + 2]  # settle
    x += [x[-1] - 20 * k for k in (1, 2, 3)]  # a BIGGER retreat, frames 15-17
    x += [x[-1] + 1, x[-1] + 2]
    cs = [(v, 50.0) for v in x]
    assert F.subject_velocity_peak(cs, opponent_on_right=True) == 10
    assert F.subject_velocity_peak(cs, opponent_on_right=False) == 15


def test_too_few_centroids_is_none():
    assert F.subject_velocity_peak([None, None, (1.0, 1.0)], True) is None


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


def test_two_signals_that_agree_anchor_on_the_audio():
    r = F.resolve(audio_f=13, velocity_f=15)
    assert r.ok and r.frame == 13


def test_two_signals_that_disagree_are_unresolved_not_averaged():
    r = F.resolve(audio_f=13, velocity_f=57)
    assert not r.ok and r.reason == F.FinishFailure.SIGNALS_DISAGREE
    assert r.frame is None


def test_a_missing_signal_is_named():
    assert F.resolve(None, 12).reason == F.FinishFailure.NO_AUDIO
    assert F.resolve(12, None).reason == F.FinishFailure.NO_VELOCITY


def test_the_descent_disagrees_out_loud_but_never_vetoes():
    """It is a cross-check. The version that anchored on it put one strike at
    three different absolute times."""
    r = F.resolve(audio_f=13, velocity_f=14, descent_f=42)
    assert r.ok and r.frame == 13
    assert r.descent_agrees is False


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
