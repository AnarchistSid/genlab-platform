"""Port 6 gate: the beat grid, against the numbers the v4/v5 bed produced.

The bed is synthesised here rather than shipped: a click track at a known BPM is
a complete test of detection, and it lets the octave check be exercised against
a case that actually has a half-tempo trap in it.
"""

from __future__ import annotations

import numpy as np
import pytest
from genlab_core.action import grid as G

FPS = 30.0
SR = 22050


def _click_track(bpm: float, seconds: float = 8.0, accent_every: int = 0) -> np.ndarray:
    """A click at `bpm`. With `accent_every`, every Nth click is louder --
    which is what creates a half/double-tempo trap for a naive detector."""
    n = int(SR * seconds)
    a = np.zeros(n, np.float32)
    period = 60.0 / bpm
    rng = np.random.default_rng(4)
    a += rng.normal(0, 0.002, n).astype(np.float32)  # floor, so flux is defined
    for k in range(int(seconds / period)):
        i = int(k * period * SR)
        amp = 1.0 if (not accent_every or k % accent_every == 0) else 0.45
        click = np.exp(-np.arange(400) / 40.0).astype(np.float32) * amp
        a[i : i + len(click)] += click[: max(0, n - i)]
    return a


def _decoder(audio: np.ndarray):
    return lambda path, sr: audio


def test_the_grid_finds_the_tempo_it_was_given():
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    assert abs(g.bpm - 150.0) < 1.0, f"detected {g.bpm}"


def test_150_bpm_is_an_integer_frame_period_at_30fps():
    """The whole point of asking the generator for 150: 12.0 frames exactly, so
    no event ever has to round onto the grid."""
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    assert g.is_integer_frame(FPS), f"period {g.period_frames(FPS)} frames"
    assert round(g.period_frames(FPS)) == 12


def test_the_octave_check_rejects_the_half_tempo_trap():
    """An accented click track scores well at BOTH the true tempo and its half.

    Without the check, a grid at half tempo makes every off-beat event look
    on-grid: the metric reads 100% while the reel visibly drifts.
    """
    g = G.detect_grid("x", decode=_decoder(_click_track(160.0, accent_every=2)))
    assert abs(g.bpm - 160.0) < 2.0, f"locked to an octave: {g.bpm}"


def test_an_octave_must_beat_the_incumbent_not_merely_tie():
    assert G.OCTAVE_MARGIN > 1.0, "an equal score is not evidence to switch"


def test_the_grid_has_more_onset_energy_on_it_than_off_it():
    """The number that says detection worked at all. A wrong grid scores ~0."""
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    assert g.lift > 0.5, f"lift {g.lift:+.3f} -- the grid is not the music's grid"


# ───────────────────────── the phase trim ───────────────────────────────────


def test_the_phase_trim_puts_beat_zero_on_frame_zero():
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    trim = G.phase_trim_seconds(g)
    assert trim == pytest.approx(g.beats[0], abs=1e-9)
    frames = G.beat_frames(g, FPS, total_frames=96)
    assert frames[0] == 0, "beat 0 must land on output frame 0"


def test_every_beat_lands_on_a_multiple_of_twelve():
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    frames = G.beat_frames(g, FPS, total_frames=96)
    assert frames == list(range(0, 96, 12)), frames


def test_all_events_placed_on_beats_score_fully_on_grid():
    g = G.detect_grid("x", decode=_decoder(_click_track(150.0)))
    beats = G.beat_frames(g, FPS, total_frames=96)
    assert G.on_grid_fraction(beats, beats) == 1.0


def test_an_off_grid_event_is_counted_as_off_grid():
    """Guard the metric: if it cannot report less than 100%, it is not a metric."""
    beats = list(range(0, 96, 12))
    assert G.on_grid_fraction([0, 12, 25, 36], beats) == pytest.approx(0.75)


# ───────────────────────── the envelope ─────────────────────────────────────


def test_beat_phase_decays_over_four_frames_and_is_zero_between():
    vals = [G.beat_phase(f, 12) for f in range(12)]
    assert vals[0] > vals[1] > vals[2] > vals[3]
    assert all(v == 0.0 for v in vals[4:]), vals


def test_the_downbeat_is_stronger_than_the_other_beats():
    """Every 4th beat carries more, so the reel has a bar and not just a pulse."""
    assert G.beat_phase(0, 12) > G.beat_phase(12, 12)
    assert G.beat_phase(48, 12) == G.beat_phase(0, 12)


def test_an_afterglow_without_its_gesture_is_rejected():
    """A trail with nothing in front of it reads as an artefact, not an echo."""
    good = [(24, "mega_bolt"), (25, "bolt_afterglow")]
    orphan = [(25, "bolt_afterglow")]
    assert G.afterglow_is_earned(good, "bolt_afterglow", "mega_bolt")
    assert not G.afterglow_is_earned(orphan, "bolt_afterglow", "mega_bolt")
