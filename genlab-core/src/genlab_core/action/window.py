"""Pick the ACTION segment, and ask WHO is in it.

The old selector asked two questions of a candidate window -- is there motion,
and are there no cuts -- and both are about whether the shot is TRACKABLE. Run
on a UFC knockdown it returned a window in which the camera follows the man who
is about to lose, the finisher only becomes co-dominant in the last quarter, and
tracking the correct subject through it yields a matte covering 1.29% of frame
with 12 empty frames. The window was trackable. It was trackable of the wrong
person.

So identity is the third criterion, and it belongs HERE rather than downstream:
a window that cannot hold its subject is not a window this template can use, and
no amount of care in the renderer fixes that. Concretely --

    high motion  AND  zero cuts  AND
    the finisher's largest component >= MIN_SUBJECT_FRAC of the foreground
    on >= MIN_SUBJECT_FRAMES of sampled frames.

The finisher is chosen by ``subject.choose_subject`` on the window's LAST frame,
which is the same rule the renderer then holds across annotations -- the
selector and the renderer must not disagree about who the subject is.

Cheap things first: motion and cut detection run over the whole clip in two
ffmpeg passes. The identity criterion needs a saliency matte per sampled frame,
so it only runs on windows that already cleared motion and cuts.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from genlab_core.action.subject import choose_subject, garment_groups

logger = logging.getLogger(__name__)

SCENE_THRESHOLD = 0.4        # ffmpeg scene score that counts as a hard cut
# The finisher's share of the GARMENT visible in the foreground, not of the raw
# foreground. Measured on real frames, a garment component runs 3.6-13.0% of the
# foreground silhouette -- a person's trunks are a small part of their body -- so
# a 20%-of-foreground bar is unreachable by construction and scored 9 of 10
# windows at exactly 0.0%. Against the garment total the same frames separate
# cleanly: a fighter who is present and prominent reads 39-72%, one who is
# marginal or absent reads 0. Per-person body pixels would be the ideal
# denominator, but the two bodies are one merged component -- which is the whole
# reason identity is derived from hue in the first place.
MIN_SUBJECT_FRAC = 0.20
MIN_SUBJECT_FRAMES = 0.80    # ...on this share of sampled frames
IDENTITY_SAMPLES = 8         # frames sampled per window for the identity test


@dataclass(frozen=True)
class WindowScore:
    start_s: float
    motion: float
    cuts: int
    subject_hue: float | None
    subject_frames_frac: float
    eligible: bool
    reason: str

    def row(self) -> str:
        hue = "--" if self.subject_hue is None else f"{self.subject_hue:5.1f}"
        return (f"  t={self.start_s:7.2f}s  motion={self.motion:6.2f}  "
                f"cuts={self.cuts:2d}  subject_hue={hue}  "
                f"held={self.subject_frames_frac * 100:5.1f}%  "
                f"{'ELIGIBLE' if self.eligible else self.reason}")


# NOTE ON FFMPEG VERBOSITY, measured 2026-09-17: `-v error` SILENCES
# `metadata=print`. The filter still runs, the process still exits 0, and zero
# lines come back -- which reads exactly like "this clip has no motion" rather
# than "the instrument was muted". Use `-hide_banner -nostats` instead, which
# quiets the banner and the progress line while leaving filter output intact.
def cut_times(path: str, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Timestamps of hard cuts, via ffmpeg scene detection."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf",
         f"select='gt(scene,{threshold})',metadata=print:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    out = []
    for line in (r.stdout or "").splitlines():
        if "pts_time:" in line:
            try:
                out.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                continue
    return out


def duration_s(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float((r.stdout or "0").strip() or 0.0)


def motion_profile(path: str) -> tuple[list[float], float]:
    """Per-FRAME luma difference, and the clip duration in seconds.

    Returns frames, not buckets. An earlier version returned "(values, bucket_s)"
    and the caller multiplied len(values) by the bucket to get a duration, which
    turned a 754-second clip into a 9054-second one and sent every seek past the
    end of the file. The sample rate is whatever the clip's fps is; derive it
    from the real duration instead of assuming one.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf",
         "signalstats,metadata=print:key=lavfi.signalstats.YDIF", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    vals = [float(x.split("=")[1]) for x in (r.stderr or "").splitlines() if "YDIF=" in x]
    if not vals:
        raise RuntimeError("no YDIF samples — motion profile unmeasurable")
    dur = duration_s(path)
    if dur <= 0:
        raise RuntimeError(f"could not read duration of {path}")
    return vals, dur


def _subject_hold(rgbs, fgs) -> tuple[float | None, float]:
    """Choose the finisher on the LAST frame; measure how often it holds."""
    subj = choose_subject(rgbs[-1], fgs[-1], rgbs, fgs, last_frame_index=len(rgbs) - 1)
    if subj is None:
        return None, 0.0
    held = 0
    for rgb, fg in zip(rgbs, fgs, strict=False):
        groups = garment_groups(rgb, fg)
        if not groups:
            continue
        garment_total = float(sum(g["body_px"] for g in groups)) or 1.0
        mine = [g for g in groups
                if abs((g["hue"] - subj.hue_deg + 180) % 360 - 180) <= 45]
        if mine and max(g["body_px"] for g in mine) / garment_total >= MIN_SUBJECT_FRAC:
            held += 1
    return subj.hue_deg, held / max(len(rgbs), 1)


def scan(path: str, frame_fn: Callable, matte_fn: Callable, *,
         duration_s: float = 3.2, stride_s: float = 1.0,
         skip_s: float = 30.0, top_n: int = 12) -> list[WindowScore]:
    """Score every candidate window in the clip. Returns them ranked.

    ``frame_fn(path, t) -> rgb`` and ``matte_fn(rgb) -> float mask`` are injected
    so this module stays free of torch/rembg, matching ``source_score``.
    """
    vals, total = motion_profile(path)
    cuts = cut_times(path)
    fps_est = len(vals) / max(total, 1e-6)
    logger.info("[window] %.1fs clip, %d motion samples (%.1f fps), %d cuts",
                total, len(vals), fps_est, len(cuts))

    starts = np.arange(skip_s, max(total - duration_s - skip_s, skip_s + 1), stride_s)
    rough: list[tuple[float, float, int]] = []
    for s in starts:
        i0, i1 = int(s * fps_est), int((s + duration_s) * fps_est)
        seg = vals[i0:i1]
        if not seg:
            continue
        n_cuts = sum(1 for c in cuts if s <= c <= s + duration_s)
        rough.append((float(s), float(np.mean(seg)), n_cuts))

    clean = [r for r in rough if r[2] == 0]
    logger.info("[window] %d windows, %d with zero cuts; testing identity on the "
                "top %d by motion", len(rough), len(clean), top_n)
    clean.sort(key=lambda r: -r[1])

    scored: list[WindowScore] = []
    for s, mot, n_cuts in clean[:top_n]:
        ts = [s + duration_s * i / (IDENTITY_SAMPLES - 1) for i in range(IDENTITY_SAMPLES)]
        rgbs = [frame_fn(path, t) for t in ts]
        fgs = [matte_fn(r) for r in rgbs]
        hue, frac = _subject_hold(rgbs, fgs)
        if hue is None:
            reason = "no subject on the last frame"
        elif frac < MIN_SUBJECT_FRAMES:
            reason = (f"subject holds only {frac * 100:.0f}% of frames "
                      f"(want {MIN_SUBJECT_FRAMES * 100:.0f}%)")
        else:
            reason = ""
        scored.append(WindowScore(start_s=s, motion=mot, cuts=n_cuts,
                                  subject_hue=hue, subject_frames_frac=frac,
                                  eligible=bool(hue is not None and frac >= MIN_SUBJECT_FRAMES),
                                  reason=reason))
    scored.sort(key=lambda w: (not w.eligible, -w.subject_frames_frac, -w.motion))
    return scored


def finish_frame(opponent_centroid_y: list[float], frame_indices: list[int]) -> int:
    """The frame where the loser starts to GO DOWN -- the strike that ends it.

    Anchors move with the window, so a flash must be tied to the EVENT and never
    to a frame offset. ACTION-UFC-04 anchored its drawing flash to peak motion
    and, once the window was re-picked to start at the finish, the flash fired on
    frames 93-95 -- the last three frames of the segment, on the follow-through
    rather than the strike, and over the live ending the segment is supposed to
    end on.

    The signal is the opponent's silhouette centroid falling: while he is
    standing it is flat, and when he is dropped it rises down-frame fast. The
    finish is the frame of steepest sustained descent, which is the moment the
    strike lands rather than the moment he lands.

    Centroids come from the same silhouettes the subject vote uses, so the
    selector's idea of "the opponent" and the renderer's cannot diverge.
    """
    if len(opponent_centroid_y) < 3:
        return frame_indices[0] if frame_indices else 0
    y = np.asarray(opponent_centroid_y, float)
    k = min(3, len(y))
    sm = np.convolve(np.pad(y, (k // 2, k // 2), mode="edge"), np.ones(k) / k, "valid")
    grad = np.diff(sm)
    i = int(np.argmax(grad))
    logger.info("[finish] steepest opponent descent %+.3f between frames %d and %d "
                "-> finish frame %d", float(grad[i]), frame_indices[i],
                frame_indices[i + 1], frame_indices[i])
    return frame_indices[i]
