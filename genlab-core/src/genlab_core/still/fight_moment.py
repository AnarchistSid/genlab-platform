"""A fight's peak is a SEQUENCE, not a frame.

ANIME-PEAK-01 §2. The sports selector finds one strike. A fight's peak has a
shape the animation itself marks out:

    wind-up  ->  clash  ->  IMPACT  ->  fall / aftermath

The impact is the easiest of the four to find and the only one the medium
labels explicitly: anime marks a hit with a 1-3 frame luma flash — the frame
goes white, or to a single saturated colour — at the same instant the audio
steps up. That coincidence is the signal. Motion alone finds a camera pan;
audio alone finds a shout; a luma spike alone finds a cut to a bright shot.

Nothing here is tuned by eye. ``calibrate`` compares the selector's impact
against hand marks and reports the error, and the module carries whatever
that measurement says rather than a threshold chosen because it looked right.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: The sequence bounds §2 asks for.
WINDUP_MIN_S, WINDUP_MAX_S = 2.0, 4.0
WINDOW_MIN_S, WINDOW_MAX_S = 6.0, 14.0
#: A flash is short. Longer than this and it is a bright SHOT, not an impact.
FLASH_MAX_FRAMES = 3
#: Impact-frame agreement the calibration must reach.
CALIBRATION_TOLERANCE_S = 0.30


@dataclass(frozen=True)
class Beat:
    """One candidate impact: where, and what agreed."""

    t: float
    luma_jump: float
    audio_step: float
    motion: float
    score: float

    def row(self) -> str:
        return (
            f"  t={self.t:6.2f}s  luma+{self.luma_jump:6.1f}  "
            f"audio+{self.audio_step:5.2f}  motion={self.motion:6.2f}  "
            f"score={self.score:6.2f}"
        )


@dataclass(frozen=True)
class Sequence:
    """The window to cut: wind-up through fall, around one impact."""

    impact_t: float
    start_s: float
    end_s: float
    beat: Beat

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def row(self) -> str:
        return (
            f"impact {self.impact_t:6.2f}s  window {self.start_s:6.2f}-{self.end_s:6.2f}s "
            f"({self.duration_s:4.1f}s)"
        )


def luma_series(path: Path) -> tuple[np.ndarray, float]:
    """Per-frame mean luma, and the frame rate it was sampled at."""
    from genlab_core.action.window import container_fps

    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-an",
            "-vf",
            "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    vals = [float(x.split("=")[1]) for x in (r.stderr or "").splitlines() if "YAVG=" in x]
    if not vals:
        raise RuntimeError(f"no luma samples from {path}")
    return np.array(vals, dtype=np.float32), container_fps(str(path))


def audio_envelope(path: Path, *, sr: int = 8000, hop: float = 0.02) -> np.ndarray:
    """RMS in dB per ``hop`` seconds. Empty when the file has no audio."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
        timeout=900,
    )
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if x.size == 0:
        return np.array([], dtype=np.float32)
    n = int(hop * sr)
    frames = x[: (len(x) // n) * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-12)


def find_beats(video: Path, audio: Path | None = None, *, top_n: int = 12) -> list[Beat]:
    """Candidate impacts: a SHORT luma spike, an audio step, and motion.

    The luma test is a spike against the local baseline, not an absolute
    brightness — an impact frame on a dark scene is a jump to grey, and on a
    bright one it is a jump to white. Requiring a short spike is what
    separates the animators' impact frame from a cut to a brighter shot,
    which raises luma and keeps it there.
    """
    from genlab_core.action.window import motion_profile

    luma, fps = luma_series(video)
    prof = motion_profile(str(video))
    mot = np.array(prof.values, dtype=np.float32)
    env = audio_envelope(audio or video)
    hop = 0.02

    # Local baseline = median over +/- 0.5 s, so the spike is relative.
    half = max(1, int(0.5 * fps))
    pad = np.pad(luma, (half, half), mode="edge")
    base = np.array([np.median(pad[i : i + 2 * half + 1]) for i in range(len(luma))])
    jump = luma - base

    beats: list[Beat] = []
    for i in range(1, len(luma) - FLASH_MAX_FRAMES - 1):
        if jump[i] < 12.0:
            continue
        # It must COME BACK — a sustained rise is a different shot.
        if not any(jump[i + k] < jump[i] * 0.4 for k in range(1, FLASH_MAX_FRAMES + 1)):
            continue
        if jump[i - 1] >= jump[i]:
            continue  # keep only the peak of the spike
        t = i / fps
        a = 0.0
        if env.size:
            k = int(t / hop)
            pre = env[max(0, k - 15) : max(1, k)]
            post = env[k : k + 15]
            if pre.size and post.size:
                a = float(post.max() - np.median(pre))
        m = float(mot[min(i, len(mot) - 1)])
        # Each term normalised to roughly 0-1 before weighting, so no single
        # term can dominate by unit. Audio is weighted highest because it is
        # the one that separates a hit from a bright cut.
        score = (
            min(jump[i] / 60.0, 1.0) * 1.0
            + min(max(a, 0.0) / 12.0, 1.0) * 1.4
            + min(m / 40.0, 1.0) * 0.8
        )
        beats.append(Beat(t, float(jump[i]), a, m, float(score)))

    beats.sort(key=lambda b: -b.score)
    logger.info(
        "[fight] %d candidate impacts in %s; top %d kept",
        len(beats),
        video.name,
        min(top_n, len(beats)),
    )
    return beats[:top_n]


def sequence_for(
    beat: Beat, duration_s: float, *, windup_s: float = 3.0, fall_s: float = 5.0
) -> Sequence:
    """Wind-up through fall around one impact, clamped to the §2 window band."""
    start = max(0.0, beat.t - max(WINDUP_MIN_S, min(windup_s, WINDUP_MAX_S)))
    end = min(duration_s, beat.t + fall_s)
    if end - start < WINDOW_MIN_S:
        end = min(duration_s, start + WINDOW_MIN_S)
    if end - start > WINDOW_MAX_S:
        end = start + WINDOW_MAX_S
    return Sequence(beat.t, start, end, beat)


#: Below this, a "mark" almost certainly came FROM the pick it is being
#: compared against. A frame is 1/24 s = 0.042 s; a human mark cannot land
#: within milliseconds of a detector's output.
_CIRCULAR_ERROR_S = 0.02


def calibrate(
    picks: list[float], marks: list[float], tolerance_s: float = CALIBRATION_TOLERANCE_S
) -> dict:
    """The selector's impacts against INDEPENDENT marks. Reports; does not tune.

    Reported as the error per mark AND the fraction inside tolerance, because
    a mean error hides one catastrophic miss among four good hits — and one
    catastrophic miss is a reel built on the wrong second.

    ``circular`` is the important field. The first run of this returned a
    100% hit rate with a median error of 0.002 s, because the marks had been
    taken from the selector's own top candidates and then confirmed by eye.
    That measures the detector against itself. Confirming a pick visually
    establishes PRECISION — this pick is a real impact — and says nothing
    about RECALL, which is whether the impacts it missed were the better
    ones. A mark must be made before the picks are seen, or it is not a mark.
    """
    errs = []
    for m in marks:
        if not picks:
            errs.append(float("inf"))
            continue
        errs.append(min(abs(p - m) for p in picks))
    inside = [e for e in errs if e <= tolerance_s]
    finite = [e for e in errs if e != float("inf")]
    circular = bool(finite) and all(e < _CIRCULAR_ERROR_S for e in finite)
    if circular:
        logger.warning(
            "[fight] calibration looks CIRCULAR: every error is under %.3f s, which "
            "is below one frame. The marks were almost certainly taken from the "
            "picks. This measures precision-by-inspection, not accuracy — treat the "
            "hit rate as unestablished.",
            _CIRCULAR_ERROR_S,
        )
    return {
        "marks": len(marks),
        "errors_s": [round(e, 3) for e in errs],
        "within_tolerance": len(inside),
        "tolerance_s": tolerance_s,
        "hit_rate": round(len(inside) / len(marks), 3) if marks else 0.0,
        "worst_s": round(max(errs), 3) if errs else None,
        "median_s": round(float(np.median(errs)), 3) if errs else None,
        "circular": circular,
        "established": "precision only — marks are not independent"
        if circular
        else "accuracy against independent marks",
    }


# ── ANIME-PEAK-02 §2: blind marks, order enforced ────────────────────────


class MarksNotBlind(RuntimeError):
    """The marks were written after the selector ran. Not a calibration."""


@dataclass(frozen=True)
class Marks:
    """One fight's hand marks, with the evidence that they came first."""

    fight: str
    impacts: list[float]
    windup_starts: list[float] = None      # type: ignore[assignment]
    falls: list[float] = None              # type: ignore[assignment]
    sha256: str = ""
    written_at: float = 0.0
    marker_note: str = ""


def write_marks(path: Path, fight: str, impacts: list[float], *,
                windup_starts: list[float] | None = None,
                falls: list[float] | None = None, note: str = "") -> Marks:
    """Record marks and hash them. MUST happen before the selector runs."""
    import hashlib
    import json
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"fight": fight, "impacts": impacts,
            "windup_starts": windup_starts or [], "falls": falls or [],
            "marker_note": note, "written_at": time.time()}
    blob = json.dumps(body, sort_keys=True).encode()
    body["sha256"] = hashlib.sha256(blob).hexdigest()[:16]
    path.write_text(json.dumps(body, indent=2))
    logger.info("[marks] %s: %d impacts, sha %s", fight, len(impacts), body["sha256"])
    return Marks(fight, impacts, windup_starts or [], falls or [],
                 body["sha256"], body["written_at"], note)


def load_marks(path: Path) -> Marks:
    import json

    d = json.loads(path.read_text())
    return Marks(d["fight"], d["impacts"], d.get("windup_starts", []),
                 d.get("falls", []), d.get("sha256", ""), d.get("written_at", 0.0),
                 d.get("marker_note", ""))


def calibrate_blind(marks_path: Path, picks_path: Path,
                    tolerance_s: float = CALIBRATION_TOLERANCE_S) -> dict:
    """Calibrate ONLY if the marks predate the selector's output.

    The order is the whole mechanism. Marks made after seeing the picks are
    not marks — the first attempt at this reported a 100% hit rate at a
    0.002 s median error because the marks WERE the picks. A file-mtime
    comparison is not proof of intent, but it makes the cheap version of the
    mistake impossible and leaves a record of which came first.

    Reports PRECISION (picks near a mark) and RECALL (marks with no pick
    near them) separately, because a selector that fires often will score
    well on one and badly on the other, and recall is the one that says
    whether the best moment was found.
    """
    import json

    if not marks_path.exists():
        raise MarksNotBlind(f"no marks at {marks_path}")
    if picks_path.exists() and picks_path.stat().st_mtime < marks_path.stat().st_mtime:
        raise MarksNotBlind(
            f"{picks_path.name} predates {marks_path.name} — the selector ran "
            f"before the marks were written, so these are not blind marks")

    m = load_marks(marks_path)
    picks = [float(x) for x in json.loads(picks_path.read_text())["picks"]]

    matched_marks = [k for k in m.impacts if any(abs(p - k) <= tolerance_s for p in picks)]
    matched_picks = [p for p in picks if any(abs(p - k) <= tolerance_s for k in m.impacts)]
    recall = len(matched_marks) / len(m.impacts) if m.impacts else 0.0
    precision = len(matched_picks) / len(picks) if picks else 0.0
    missed = [k for k in m.impacts if k not in matched_marks]
    return {"fight": m.fight, "marks_sha256": m.sha256, "n_marks": len(m.impacts),
            "n_picks": len(picks), "tolerance_s": tolerance_s,
            "recall": round(recall, 3), "precision": round(precision, 3),
            "missed_marks_s": [round(x, 2) for x in missed],
            "blind": True}
