"""The beat grid ACTION cuts to.

RENDER-01 Port 6. Oracle: `beats.py` (detection) plus the phase-trim and event
placement the v4/v5 build scripts held inline.

WHY A GRID AT ALL
-----------------
ACTION is music-led: the bed comes first, the beat grid comes off the bed, and
the cuts and effects land on the grid. (TALK is speech-led -- which element
everything ducks to IS the difference between the two templates.)

THE INTEGER-FRAME TRICK
-----------------------
Ask the bed generator for a tempo whose period is a whole number of frames:
150 BPM at 30 fps is exactly 12 frames. Then phase-trim the bed so beat 0 lands
on output frame 0, and every later beat sits on an exact multiple of 12. No
event ever has to round, so "on grid" is exact rather than within-tolerance.

THE OCTAVE CHECK IS LOAD-BEARING
--------------------------------
Flux autocorrelation peaks at the half and the double just as hard as at the
true tempo. A grid at half tempo makes every OFF-beat event look on-grid, so the
metric would read 100% while the reel visibly drifted. The check re-scores both
octaves with the same scorer and takes one only if it beats the incumbent by a
margin -- an equal score is not evidence.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_LO_BPM = 120.0
DEFAULT_HI_BPM = 180.0
OCTAVE_MARGIN = 1.02  # an octave must BEAT the incumbent, not merely tie


@dataclass(frozen=True)
class BeatGrid:
    bpm: float
    period_s: float
    duration_s: float
    beats: tuple[float, ...]  # seconds
    strength: tuple[float, ...]
    mean_flux_on_beat: float
    mean_flux_overall: float

    @property
    def lift(self) -> float:
        """How much more onset energy sits on the grid than off it.

        A grid that is not actually the music's grid scores near zero here, so
        this is the number that says whether detection worked at all.
        """
        return self.mean_flux_on_beat - self.mean_flux_overall

    def period_frames(self, fps: float) -> float:
        return self.period_s * fps

    def is_integer_frame(self, fps: float, tol: float = 1e-6) -> bool:
        p = self.period_frames(fps)
        return abs(p - round(p)) < tol


class BeatFit(NamedTuple):
    """A tempo fit. ``(score, phase)`` — two floats in opposite units, which is
    the shape that bound a duration to `fps` in craft_render (Part 30 §1)."""

    score: float
    phase: float


def _decode_mono(path: str, sr: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


def onset_envelope(
    path: str, sr: int = 22050, hop: int = 256, nfft: int = 1024, decode=_decode_mono
) -> tuple[np.ndarray, float, float]:
    """Spectral-flux onset envelope. Returns (flux_z, env_fps, duration_s).

    No librosa or aubio on this machine, hence the hand-rolled version. `decode`
    is injectable so the grid logic can be tested without an audio file.
    """
    a = decode(path, sr)
    n = (len(a) - nfft) // hop
    if n <= 1:
        return np.zeros(0, np.float32), sr / hop, len(a) / sr
    win = np.hanning(nfft).astype(np.float32)
    frames = np.stack([a[i * hop : i * hop + nfft] * win for i in range(n)])
    spec = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 100.0)
    flux = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
    return (flux - flux.mean()) / (flux.std() + 1e-9), sr / hop, len(a) / sr


def detect_grid(
    path: str,
    lo_bpm: float = DEFAULT_LO_BPM,
    hi_bpm: float = DEFAULT_HI_BPM,
    decode=_decode_mono,
) -> BeatGrid:
    flux, fps_env, dur = onset_envelope(path, decode=decode)
    if not len(flux):
        raise ValueError(f"no onset envelope from {path}")

    def fit(bpm: float) -> BeatFit:
        step = (60.0 / bpm) * fps_env
        best = BeatFit(score=-1e9, phase=0.0)
        for ph in np.arange(0, step, 0.2):
            idx = np.round(np.arange(ph, len(flux) - 1, step)).astype(int)
            v = float(flux[idx].mean())
            if v > best.score:
                best = BeatFit(score=v, phase=float(ph))
        return best

    score, bpm = max((fit(b).score, b) for b in np.arange(lo_bpm, hi_bpm + 0.01, 0.25))
    for alt in (bpm / 2, bpm * 2):
        if lo_bpm <= alt <= hi_bpm and fit(alt).score > score * OCTAVE_MARGIN:
            score, bpm = fit(alt).score, alt
            logger.info("octave check moved the tempo to %.2f BPM", bpm)

    phase = fit(bpm).phase
    step = (60.0 / bpm) * fps_env
    idx = np.arange(phase, len(flux) - 1, step)
    return BeatGrid(
        bpm=float(bpm),
        period_s=60.0 / float(bpm),
        duration_s=dur,
        beats=tuple((idx / fps_env).tolist()),
        strength=tuple(flux[np.round(idx).astype(int)].tolist()),
        mean_flux_on_beat=float(flux[np.round(idx).astype(int)].mean()),
        mean_flux_overall=float(flux.mean()),
    )


def phase_trim_seconds(grid: BeatGrid) -> float:
    """Seconds to trim off the bed so beat 0 lands on output frame 0.

    v4's bed detected at 150.00 BPM with beat 0 at 0.385 s; trimming 0.385 s put
    beat 0 on frame 0 and every later beat on an exact multiple of 12 frames.
    """
    return float(grid.beats[0]) if grid.beats else 0.0


def beat_frames(grid: BeatGrid, fps: float, total_frames: int, trimmed: bool = True) -> list[int]:
    """Frame indices carrying a beat, after the phase trim."""
    origin = 0.0 if trimmed else phase_trim_seconds(grid)
    period = grid.period_frames(fps)
    out, k = [], 0
    while True:
        f = origin * fps + k * period
        if f >= total_frames:
            return out
        out.append(int(round(f)))
        k += 1


def beat_phase(frame: int, period_frames: int, downbeat_every: int = 4) -> float:
    """Envelope that decays over the 4 frames after each beat, stronger on the
    downbeat. Zero everywhere else -- effects are earned by the beat."""
    k = frame % period_frames
    if k > 3:
        return 0.0
    on_downbeat = (frame - k) % (period_frames * downbeat_every) == 0
    return (1.6 if on_downbeat else 1.0) * (1.0 - k / 4.0)


def on_grid_fraction(event_frames, beats: list[int], tol: int = 0) -> float:
    """Share of events landing on a beat. With an integer-frame grid, tol=0."""
    ev = list(event_frames)
    if not ev:
        return 1.0
    bs = set(beats)
    hit = (
        sum(1 for f in ev if any(abs(f - b) <= tol for b in bs))
        if tol
        else sum(1 for f in ev if f in bs)
    )
    return hit / len(ev)


def afterglow_is_earned(events: list[tuple[int, str]], glow: str, gesture: str) -> bool:
    """An afterglow may only appear on the frame after its own gesture.

    A trail with nothing in front of it reads as a rendering artefact rather
    than as the tail of a movement -- the effect has to be anchored to the thing
    it is the echo of.
    """
    by_frame = {f: k for f, k in events}
    return all(by_frame.get(f - 1) == gesture for f, k in events if k == glow)
