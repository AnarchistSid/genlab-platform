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

from genlab_core.action.effects._ops import dilate
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

#: The opponent must reach this share of frame to be a body rather than a limb
#: the subtraction left behind.
OPPONENT_AREA_FLOOR = 0.01

#: Sample every Nth frame for the finish. The centroid descent is a trend over
#: about a second, not a per-frame signal, so a third of the frames resolve it.
FINISH_STRIDE = 3

#: How far the subject mask is grown before subtraction. The seed is loose and
#: holey; without the dilation its gaps read as opponent.
SUBJECT_DILATE_PX = 6.0


class PlanFailure:
    NO_CANDIDATES = "no_candidates"
    VOTE_TOO_SPLIT = "vote_too_split"
    NO_MATTES = "worker_failed"
    #: Fewer than three separable frames. The window is designed to BEGIN at
    #: the finish and the treatment anchors its flash there, so a window with no
    #: derivable finish has nothing to anchor. Never a silent frame 0.
    FINISH_UNRESOLVED = "finish_unresolved"


@dataclass
class CandidateResult:
    start_s: float
    hue_deg: float | None = None
    votes: int = 0
    frames: int = 0
    unanimous: bool = False
    presence: float = 0.0
    reason: str = ""
    #: Window-relative finish, or None when fewer than three frames separate.
    finish_frame: int | None = None

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
    coarse_foreground_fn=None,
    seed_mask_fn=None,
    select_window=None,
    window_silhouette_fn=None,
    window_foreground_fn=None,
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
    tail_silhouettes: dict[int, np.ndarray] = {}

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
            # Keep them. These are SAM2-quality subject masks already paid for;
            # the finish subtracts a subject mask on every third frame and would
            # otherwise re-derive a coarser one over the same frames.
            for f in tail:
                m = next(iter(silhouette_fn(f).values()), None)
                if m is not None:
                    tail_silhouettes[f] = m
            cr.presence = presence_fraction(
                [tail_silhouettes.get(f, np.zeros((2, 2))) for f in tail]
            )
        results.append(cr)

    survivors = [c for c in results if c.survived and c.presence >= PRESENCE_MIN_FRAMES]
    if not survivors:
        return PlanResult(
            reason=PlanFailure.VOTE_TOO_SPLIT,
            candidates=results,
            vote_frames=vote_frames,
            seconds=round(now() - t0, 1),
        )

    def _frames_of(c) -> int:
        return int(
            next(
                cd.get("frames", 96)
                for cd in candidates
                if float(cd.get("start_s", 0.0)) == c.start_s
            )
        )

    # THE FINISH IS DERIVED PER SURVIVOR, BEFORE PROPAGATION. It is now cheap
    # (every third frame, half res, no SAM2), and it is the tie-break: the
    # window is DESIGNED to begin at the finish, so the candidate whose finish
    # sits closest to its own start is the one that was framed on the event
    # rather than on the follow-through. Running it before propagation also
    # means an unresolvable finish costs the cheap phase, not the expensive one.
    fg_for_finish = coarse_foreground_fn or foreground_fn

    def _subject_mask(i: int, f: int) -> np.ndarray:
        m = tail_silhouettes.get(f)
        if m is not None:
            return m
        return seed_mask_fn(f) if seed_mask_fn else np.zeros((2, 2), np.float32)

    for c in survivors:
        c.finish_frame = _finish(
            frames_for(c.start_s, _frames_of(c)),
            foreground_fn=fg_for_finish,
            subject_mask_for=_subject_mask,
        )
        logger.info("[finish] candidate %.2fs -> %s", c.start_s, c.finish_frame)

    anchored = [c for c in survivors if c.finish_frame is not None]
    if not anchored:
        return PlanResult(
            reason=PlanFailure.FINISH_UNRESOLVED,
            candidates=results,
            vote_frames=vote_frames,
            seconds=round(now() - t0, 1),
        )

    # Closest finish to the window's own start; a tie goes to the earlier window.
    chosen = sorted(anchored, key=lambda c: (c.finish_frame, c.start_s))[0]
    finish = int(chosen.finish_frame)
    n_frames = _frames_of(chosen)
    frames = frames_for(chosen.start_s, n_frames)

    # TWO INDEX SPACES. The vote walks ABSOLUTE frames of the decoded span;
    # build_mattes counts 0..n-1 WITHIN the window. The backend supplies shims
    # that translate, and `select_window` tells it which window was chosen —
    # without that the mattes come back built on the start of the span, which is
    # a wrong answer carrying no error.
    if select_window is not None:
        select_window(chosen.start_s, n_frames)
    masks, report = build_mattes(
        len(frames),
        cuts=tuple(job.get("cuts") or ()),
        foreground_fn=window_foreground_fn or foreground_fn,
        silhouette_fn=window_silhouette_fn or silhouette_fn,
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


def _match_shape(m: np.ndarray, shape) -> np.ndarray:
    """Nearest-neighbour resample. The subject mask and the foreground come from
    different backends; subtracting across a shape mismatch is silently wrong."""
    m = np.asarray(m, np.float32)
    if m.shape[:2] == tuple(shape[:2]):
        return m
    r = (np.arange(shape[0]) * (m.shape[0] / shape[0])).astype(int).clip(0, m.shape[0] - 1)
    c = (np.arange(shape[1]) * (m.shape[1] / shape[1])).astype(int).clip(0, m.shape[1] - 1)
    return m[np.ix_(r, c)]


def _finish(
    frames,
    *,
    foreground_fn,
    subject_mask_for,
    stride: int = FINISH_STRIDE,
) -> int | None:
    """Steepest descent of the OPPONENT's centroid. The opponent is DERIVED.

    It used to be read out of `silhouette_fn`, on the assumption that the dict
    carried one entry per body. The real backend returns exactly one entry --
    `{subject_hue: mask}` -- so the separability test `len(sils) < 2` was
    structurally unsatisfiable: every frame was skipped, `ys` stayed empty, and
    the function returned frame 0 by fallback on every real run. It cost 3722
    seconds to answer nothing.

    So the opponent is now what is LEFT: foreground AND NOT dilate(subject).
    birefnet on every third frame at half resolution, no SAM2 -- the centroid of
    a body does not need a precise edge, and that is the difference between
    about three minutes and about sixty-two.

    Returns the window-relative finish frame, or None when fewer than three
    frames are separable. None is not frame 0.
    """
    ys, idx = [], []
    for i in range(0, len(frames), stride):
        f = frames[i]
        fgm = np.asarray(foreground_fn(f), np.float32)
        if fgm.size < 4:
            continue
        subj = _match_shape(subject_mask_for(i, f), fgm.shape)
        opp = (fgm > 0.5) & ~(dilate(subj, SUBJECT_DILATE_PX) > 0.5)
        area = float(opp.mean())
        rows = np.nonzero(opp.any(axis=1))[0]
        if area < OPPONENT_AREA_FLOOR or not len(rows):
            logger.info("[finish] frame %d: opponent %.2f%% — not separable", i, area * 100)
            continue
        y = float(np.nonzero(opp)[0].mean())
        logger.info("[finish] frame %d: opponent %.2f%% centroid y=%.1f", i, area * 100, y)
        ys.append(y)
        idx.append(i)
    if len(ys) < 3:
        logger.warning("[finish] only %d separable frame(s) — unresolved", len(ys))
        return None
    return int(derive_finish_frame(ys, idx))
