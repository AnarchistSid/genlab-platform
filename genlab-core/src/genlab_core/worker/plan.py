"""The worker's plan half: vote, pick the window, find the finish, matte it.

RENDER-01 Part 13. The fifth link. The chain was flag -> stage -> builder ->
(nothing) -> mattes -> render; this is the missing middle, and it lives on the
Mac because every step in it needs SAM2.

NO SECOND IMPLEMENTATION
------------------------
Everything decisive here is a call into the already-ported, already-pinned
functions — `silhouette_subject.choose_subject_by_vote`, `window.finish_frame`,
`matte.build_mattes`, `matte.crop_rect_for`. This module supplies backends and
sequences them. A vote reimplemented on the Mac would be a second thing to keep
correct, and the port arc spent four rounds learning what that costs.

THE ORDER IS THE POINT
----------------------
Candidates are filtered by CUTS before any SAM2 call. The vote is ~20 image
calls per candidate at roughly 2 s each; running it on a window that a free
ffmpeg scene-scan already disqualified is the expensive half doing rejected work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from genlab_core.action.matte import build_mattes, crop_rect_for, warp_to_crop
from genlab_core.action.silhouette_subject import choose_subject_by_vote
from genlab_core.action.window import finish_frame as derive_finish_frame

logger = logging.getLogger(__name__)

#: A candidate must reach this to be believed. Below it the "winner" was a
#: 0.05pp area difference between two halves of one merged blob — noise.
VOTE_FLOOR = 8
VOTE_FRAMES = 10

#: A silhouette smaller than this share of frame is not a fighter.
AREA_FLOOR = 0.04

#: "The finisher is present" on a frame means its largest component clears the
#: area floor. Presence is the share of frames where that holds; a window whose
#: subject vanishes for a fifth of its length has nothing to track.
PRESENCE_MIN_FRAMES = 0.80


class PlanFailure:
    NO_CANDIDATES = "no_candidates"
    VOTE_TOO_SPLIT = "vote_too_split"
    NO_MATTES = "worker_failed"


@dataclass
class CandidateResult:
    start_s: float
    hue_deg: float | None = None
    votes: int = 0
    frames: int = 0
    unanimous: bool = False
    presence: float = 0.0
    reason: str = ""

    @property
    def survived(self) -> bool:
        return self.hue_deg is not None and self.votes >= VOTE_FLOOR


@dataclass
class PlanResult:
    ok: bool = False
    reason: str = ""
    window: dict = field(default_factory=dict)
    subject_colour: dict = field(default_factory=dict)
    finish_frame: int = 0
    votes: int = 0
    vote_frames: int = VOTE_FRAMES
    vote_reason: str = ""
    finisher_presence: float = 0.0
    frames: int = 0
    mattes: dict = field(default_factory=dict)
    area_per_frame: list = field(default_factory=list)
    empty_count: int = 0
    candidates: list = field(default_factory=list)
    seconds: float = 0.0

    def as_payload(self) -> dict:
        """What goes back in the result file — mattes are written separately."""
        d = asdict(self)
        d.pop("mattes", None)
        d["candidates"] = [asdict(c) if not isinstance(c, dict) else c for c in self.candidates]
        return d


def presence_fraction(masks, area_floor: float = AREA_FLOOR) -> float:
    """Share of frames where the subject is actually there to track."""
    if not masks:
        return 0.0
    present = sum(1 for m in masks if float((np.asarray(m) > 0.5).mean()) >= area_floor)
    return present / len(masks)


def plan(
    job: dict,
    *,
    frames_for,
    silhouette_fn,
    foreground_fn,
    propagate_fn,
    cuts_in_window=None,
    now=time.time,
) -> PlanResult:
    """Vote across candidates, pick a window, derive the finish, matte it.

    Every backend is injected so the pins can replay the UFC-05 archive: the
    silhouettes, the vote, the window and the mattes are all recorded there, and
    replaying them is the only way to check this against a known answer without
    a GPU and five minutes per run.
    """
    t0 = now()
    candidates = job.get("candidates") or []
    if not candidates:
        return PlanResult(reason=PlanFailure.NO_CANDIDATES, seconds=round(now() - t0, 1))

    vote_frames = int(job.get("vote_frames", VOTE_FRAMES))
    floor = int(job.get("vote_floor", VOTE_FLOOR))
    results: list[CandidateResult] = []

    for cand in candidates:
        start = float(cand.get("start_s", 0.0))
        n = int(cand.get("frames", 96))

        # CUTS FIRST, before any SAM2 call. A cut inside the window is a new
        # shot: the tracker follows whatever now occupies those coordinates, and
        # on the UFC window that meant walking from one fighter to the other.
        # Rejecting here costs an ffmpeg scan; rejecting after the vote costs
        # ~20 SAM2 image calls.
        if cuts_in_window and cuts_in_window(start, n):
            results.append(CandidateResult(start_s=start, reason="cut inside the window"))
            continue

        frames = frames_for(start, n)
        if not frames:
            results.append(CandidateResult(start_s=start, reason="no frames"))
            continue

        vote = choose_subject_by_vote(frames, silhouette_fn, vote_frames=vote_frames)
        cr = CandidateResult(
            start_s=start,
            hue_deg=vote.hue_deg,
            votes=vote.votes,
            frames=vote.frames,
            unanimous=vote.unanimous,
            reason=vote.reason,
        )
        if vote.hue_deg is not None and vote.votes >= floor:
            tail = frames[-vote_frames:]
            cr.presence = presence_fraction(
                [next(iter(silhouette_fn(f).values()), np.zeros((2, 2))) for f in tail]
            )
        results.append(cr)

    survivors = [c for c in results if c.survived]
    if not survivors:
        return PlanResult(
            reason=PlanFailure.VOTE_TOO_SPLIT,
            candidates=results,
            vote_frames=vote_frames,
            seconds=round(now() - t0, 1),
        )

    # Highest presence; a tie goes to the EARLIER window — later windows in a
    # highlight drift toward the re-entanglement where the vote flips back.
    chosen = sorted(survivors, key=lambda c: (-c.presence, c.start_s))[0]
    n_frames = int(
        next(
            c.get("frames", 96)
            for c in candidates
            if float(c.get("start_s", 0.0)) == chosen.start_s
        )
    )
    frames = frames_for(chosen.start_s, n_frames)

    masks, report = build_mattes(
        len(frames),
        cuts=tuple(job.get("cuts") or ()),
        foreground_fn=foreground_fn,
        silhouette_fn=silhouette_fn,
        propagate_fn=propagate_fn,
        subject_hue=float(chosen.hue_deg),
    )
    if not masks:
        return PlanResult(
            reason=PlanFailure.NO_MATTES, candidates=results, seconds=round(now() - t0, 1)
        )

    masks = _warp_all(masks, job)
    areas = [float((np.asarray(m) > 0.5).mean()) for _, m in sorted(masks.items())]
    empty = sum(1 for a in areas if a < 0.005)

    finish = _finish(masks, silhouette_fn, frames)
    spec = job.get("subject_hint") or {}
    subject_colour = {
        "hue_deg": float(chosen.hue_deg),
        "hue_tol": float(spec.get("hue_tol", 25.0)),
        "sat_min": float(spec.get("sat_min", 0.25)),
        "val_min": float(spec.get("val_min", 0.10)),
    }

    return PlanResult(
        ok=True,
        window={"start_s": chosen.start_s, "frames": len(masks)},
        subject_colour=subject_colour,
        finish_frame=finish,
        votes=chosen.votes,
        vote_frames=chosen.frames or vote_frames,
        vote_reason=chosen.reason,
        finisher_presence=chosen.presence,
        frames=len(masks),
        mattes=masks,
        area_per_frame=[round(a, 5) for a in areas],
        empty_count=empty,
        candidates=results,
        seconds=round(now() - t0, 1),
    )


def _warp_all(masks: dict, job: dict) -> dict:
    """Native -> reel space. A matte in the wrong space is silently wrong."""
    rows = job.get("crop_plan") or {}
    if not rows:
        logger.warning("[plan] no crop_plan — masks stay in NATIVE space")
        return masks
    out = {}
    for f, m in masks.items():
        row = rows.get(str(f)) or rows.get(f)
        if not row:
            out[f] = m
            continue
        rect = crop_rect_for(
            mag=float(row["mag"]),
            cx=float(row["cx"]),
            cy=float(row["cy"]),
            src_h=int(row["src_h"]),
            src_w=m.shape[1],
        )
        out[f] = warp_to_crop(m, rect)
    return out


def _finish(masks: dict, silhouette_fn, frames) -> int:
    """Steepest descent of the OPPONENT's centroid, on SEPARABLE frames only.

    The first attempt at this returned frame 1, because two frames where
    separation had failed were outliers in the gradient. The separability filter
    is what makes the signal usable.
    """
    ys, idx = [], []
    for i, f in enumerate(frames):
        sils = silhouette_fn(f)
        if len(sils) < 2:  # not separable: both bodies are one blob
            continue
        other = sorted(sils.items())[-1][1]
        rows = np.nonzero(np.asarray(other) > 0.5)[0]
        if not len(rows):
            continue
        ys.append(float(rows.mean()))
        idx.append(i)
    if len(ys) < 3:
        return 0
    return int(derive_finish_frame(ys, idx))
