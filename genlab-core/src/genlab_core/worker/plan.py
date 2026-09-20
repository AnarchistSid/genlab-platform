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
from genlab_core.action.finish import (
    MIN_VALID_SAMPLES,
    FinishFailure,
    audio_bound,
    audio_onset_frame,
    descent_frame,
    invariance_spread_s,
    is_window_invariant,
    largest_component,
    subject_velocity_peak,
    velocity_samples,
)
from genlab_core.action.finish import resolve as finish_resolve
from genlab_core.action.matte import build_mattes, crop_rect_for, warp_to_crop
from genlab_core.action.silhouette_subject import choose_subject_by_vote

logger = logging.getLogger(__name__)

#: A candidate must reach this to be believed. Below it the "winner" was a
#: 0.05pp area difference between two halves of one merged blob — noise.
#: Part 16: 6 frames at half resolution instead of 10 at full. The vote is a
#: garment-AREA comparison, and halving resolution halves both areas — the ratio
#: that decides it is unchanged, at a quarter of the SAM2 cost.
VOTE_FLOOR = 5
VOTE_FRAMES = 6

#: A silhouette smaller than this share of frame is not a fighter.
AREA_FLOOR = 0.04

#: "The finisher is present" on a frame means its largest component clears the
#: area floor. Presence is the share of frames where that holds; a window whose
#: subject vanishes for a fifth of its length has nothing to track.
PRESENCE_MIN_FRAMES = 0.80

#: The opponent must reach this share of frame to be a body rather than a limb
#: the subtraction left behind.
OPPONENT_AREA_FLOOR = 0.01

#: Sample every Nth frame for the descent cross-check. The trend runs over about
#: a second, not per-frame, so a third of the frames resolve it.
FINISH_STRIDE = 3

#: Presence is sampled across the FULL window rather than clustered in its tail:
#: a subject that is solid for the last third and absent for the first two is
#: not trackable, and a tail-only sample cannot tell.
PRESENCE_SAMPLES = 10

#: Only the strongest candidates by motion reach the vote. The vote is the
#: expensive half and a low-motion window is not an ACTION window.
VOTE_CANDIDATES = 2

#: How far the subject mask is grown before subtraction. The seed is loose and
#: holey; without the dilation its gaps read as opponent.
SUBJECT_DILATE_PX = 6.0


class PlanFailure:
    NO_CANDIDATES = "no_candidates"
    #: Candidates arrived without the measured motion the ranking needs. The
    #: builder writes it; anything else is a number someone made up, and three
    #: made-up scores once excluded the archive's own window from a
    #: verification run and produced a false area-band failure.
    UNSCORED = "candidates_unscored"
    VOTE_TOO_SPLIT = "vote_too_split"
    NO_MATTES = "worker_failed"
    #: Fewer than three separable frames. The window is designed to BEGIN at
    #: the finish and the treatment anchors its flash there, so a window with no
    #: derivable finish has nothing to anchor. Never a silent frame 0.
    FINISH_UNRESOLVED = "finish_unresolved"
    #: The finish moved when the window moved. Same footage, different start,
    #: different absolute answer — the detector found a maximum, not an event.
    NOT_INVARIANT = "finish_not_window_invariant"


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
    finish_reason: str = ""

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
    #: {"n", "spread_s", "verdict", "absolute_s"}. verdict is "untested" on n=1
    #: — one window cannot disagree with itself, and calling that a pass is how
    #: a gate comes to certify something it never measured.
    invariance: dict = field(default_factory=dict)
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
    quarter_track_fn=None,
    half_silhouette_fn=None,
    audio_onset_fn=None,
    release_span=None,
    rss_mb=None,
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

    unscored = [c for c in candidates if c.get("motion_score") is None]
    if unscored:
        logger.error(
            "[plan] %d of %d candidate(s) carry no motion_score — refusing. "
            "window_candidates() measures it; do not hand-build candidates.",
            len(unscored),
            len(candidates),
        )
        return PlanResult(
            reason=PlanFailure.UNSCORED, candidates=results, seconds=round(now() - t0, 1)
        )

    # ONLY THE STRONGEST REACH THE VOTE. The vote is the expensive half — six
    # SAM2 image calls per candidate — and a low-motion window is not an ACTION
    # window whatever its silhouettes say. Candidates carrying no motion score
    # cannot be ranked, so they all go through and the log says so.
    ranked = sorted(candidates, key=lambda c: -float(c["motion_score"]))
    skipped = ranked[VOTE_CANDIDATES:]
    for c in skipped:
        results.append(
            CandidateResult(start_s=float(c.get("start_s", 0.0)), reason="not top-2 by motion")
        )
    ranked = ranked[:VOTE_CANDIDATES]
    if skipped:
        logger.info(
            "[plan] %d candidate(s) below the top-%d by motion — not voted",
            len(skipped),
            VOTE_CANDIDATES,
        )

    sil = half_silhouette_fn or silhouette_fn
    if rss_mb:
        logger.info("[plan] RSS before the vote: %d MB", rss_mb())
    for cand in ranked:
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

        vote = choose_subject_by_vote(frames, sil, vote_frames=vote_frames)
        cr = CandidateResult(
            start_s=start,
            hue_deg=vote.hue_deg,
            votes=vote.votes,
            frames=vote.frames,
            unanimous=vote.unanimous,
            reason=vote.reason,
        )
        if vote.hue_deg is not None and vote.votes >= floor:
            # ACROSS THE FULL WINDOW, not its tail. A subject solid for the last
            # third and absent for the first two is not trackable, and a
            # tail-only sample reports 1.0 for it.
            step = max(len(frames) // PRESENCE_SAMPLES, 1)
            tail = frames[::step][:PRESENCE_SAMPLES]
            # Keep them. These are SAM2-quality subject masks already paid for;
            # the finish subtracts a subject mask on every third frame and would
            # otherwise re-derive a coarser one over the same frames.
            for f in tail:
                m = next(iter(sil(f).values()), None)
                if m is not None:
                    tail_silhouettes[f] = m
            cr.presence = presence_fraction(
                [tail_silhouettes.get(f, np.zeros((2, 2))) for f in tail]
            )
        if rss_mb:
            logger.info("[plan] RSS after vote %.2fs: %d MB", start, rss_mb())
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

    # THE FINISH, PER SURVIVOR, BEFORE PROPAGATION.
    #
    # Two independent signals that must AGREE, plus the descent as a cross-check
    # that may disagree out loud but never anchors. The version that anchored on
    # the descent alone placed one UFC-05 strike at 24.60 s, 26.20 s and 26.80 s
    # depending on which window it was sampled from.
    fps = float(job.get("fps", 30.0))
    clip = job.get("clip_path") or job.get("clip") or ""
    fg_for_finish = coarse_foreground_fn or foreground_fn
    onset = audio_onset_fn or (
        lambda start_s, n: audio_onset_frame(clip, start_s, n, fps) if clip else None
    )

    for c in survivors:
        n = _frames_of(c)
        wf = frames_for(c.start_s, n)

        # THE AUDIO BOUNDS. Window-invariant, and late by a known ~0.3 s.
        a_f = onset(c.start_s, n)
        if a_f is None:
            c.finish_reason = FinishFailure.NO_AUDIO
            logger.info("[finish] candidate %.2fs -> None (%s)", c.start_s, c.finish_reason)
            continue
        lo_b, hi_b = audio_bound(a_f, fps)

        # THE VIDEO PICKS, inside the bracket. Only the bracketed frames are
        # sampled: the seed is cheap but not free, and everything outside is
        # work whose answer would be discarded.
        want = [i for i in range(len(wf)) if lo_b - 2 <= i <= hi_b + 2]
        seeds = {i: sub_mask(seed_mask_fn, wf[i], None) for i in want}
        samples = velocity_samples(seeds)
        used_tracker = False
        if len(samples) < MIN_VALID_SAMPLES and quarter_track_fn is not None:
            # The colour seed has not answered inside the bracket. Ask the
            # tracker, seeded from the vote's own mask so the two cannot
            # disagree about which fighter this is.
            best = max(
                seeds, key=lambda k: float((np.asarray(seeds[k]) > 0.5).mean()), default=None
            )
            if best is not None:
                logger.info(
                    "[finish] only %d valid seed sample(s) in the bracket — quarter-res tracker",
                    len(samples),
                )
                tracked = quarter_track_fn(c.start_s, n, best, seeds[best])
                if tracked:
                    samples = velocity_samples({k: v for k, v in tracked.items() if k in want})
                    used_tracker = True

        right, left = _opponent_side(seeds, fg_for_finish, wf)
        v_f = subject_velocity_peak(samples, opponent_on_right=right >= left, bound=(lo_b, hi_b))

        # The descent stays a cross-check and never anchors.
        d_samples = []
        for i in range(0, len(wf), FINISH_STRIDE):
            fgm = np.asarray(fg_for_finish(wf[i]), np.float32)
            if fgm.size < 4:
                continue
            subj = _match_shape(sub_mask(seed_mask_fn, wf[i], fgm.shape), fgm.shape)
            resid = ((fgm > 0.5) & ~(dilate(subj, SUBJECT_DILATE_PX) > 0.5)).astype(np.float32)
            opp = largest_component(resid)
            area = float(opp.mean())
            if area < OPPONENT_AREA_FLOOR:
                continue
            ys = np.nonzero(opp > 0.5)[0]
            if len(ys):
                d_samples.append((i, float(ys.mean()), area))

        res = finish_resolve(
            a_f,
            v_f,
            descent_frame(d_samples),
            bound=(lo_b, hi_b),
            samples_in_bound=len(samples),
            used_tracker=used_tracker,
        )
        c.finish_frame = res.frame
        c.finish_reason = res.reason
        logger.info(
            "[finish] candidate %.2fs -> %s (%s)", c.start_s, res.frame, res.reason or "anchored"
        )
        if rss_mb:
            logger.info("[plan] RSS after finish %.2fs: %d MB", c.start_s, rss_mb())

    anchored = [c for c in survivors if c.finish_frame is not None]
    if not anchored:
        return PlanResult(
            reason=PlanFailure.FINISH_UNRESOLVED,
            candidates=results,
            vote_frames=vote_frames,
            seconds=round(now() - t0, 1),
        )

    # WINDOW-INVARIANCE IS A GATE, AND IT REPORTS ITS n. One candidate cannot
    # disagree with itself, so on n=1 the gate is UNTESTED — not passed. Saying
    # "pass" there would be the same error as a detector returning a plausible
    # value it never measured.
    abs_finishes = [c.start_s + c.finish_frame / fps for c in anchored]
    invariance = {
        "n": len(abs_finishes),
        "spread_s": round(invariance_spread_s(abs_finishes), 3) if len(abs_finishes) > 1 else None,
        "verdict": "untested"
        if len(abs_finishes) < 2
        else ("pass" if is_window_invariant(abs_finishes) else "fail"),
        "absolute_s": [round(a, 3) for a in abs_finishes],
    }
    logger.info("[finish] invariance: %s (n=%d)", invariance["verdict"], invariance["n"])
    if invariance["verdict"] == "fail":
        return PlanResult(
            reason=PlanFailure.NOT_INVARIANT,
            candidates=results,
            vote_frames=vote_frames,
            invariance=invariance,
            seconds=round(now() - t0, 1),
        )

    # Closest finish to the window's own start; a tie goes to the earlier window.
    chosen = sorted(anchored, key=lambda c: (c.finish_frame, c.start_s))[0]
    finish = int(chosen.finish_frame)
    n_frames = _frames_of(chosen)
    frames = frames_for(chosen.start_s, n_frames)

    # The vote and the finish are done with the rest of the span. Propagation
    # holds image embeddings for every frame of the window on top of whatever
    # is still resident, and this machine has been between 6 and 18 GB into
    # swap all session.
    if release_span:
        release_span(chosen.start_s, n_frames)
    if rss_mb:
        logger.info("[plan] RSS before propagation: %d MB", rss_mb())

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

    if rss_mb:
        logger.info("[plan] RSS after propagation: %d MB", rss_mb())
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
        invariance=invariance,
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


def _opponent_side(seeds: dict, foreground_fn, wf: list) -> tuple[float, float]:
    """(right_mass, left_mass) of what is NOT the subject.

    Only ever used for a left/right decision, so it needs no continuity filter —
    a coarse mass comparison over several frames cannot be flipped by one bad
    seed the way a centroid can.
    """
    right = left = 0.0
    for i in sorted(seeds)[:: max(len(seeds) // 6, 1)]:
        fgm = np.asarray(foreground_fn(wf[i]), np.float32)
        if fgm.size < 4:
            continue
        subj = _match_shape(np.asarray(seeds[i], np.float32), fgm.shape)
        resid = (fgm > 0.5) & ~(dilate(subj, SUBJECT_DILATE_PX) > 0.5)
        half = resid.shape[1] // 2
        left += float(resid[:, :half].sum())
        right += float(resid[:, half:].sum())
    return right, left


def sub_mask(seed_mask_fn, f: int, shape=None) -> np.ndarray:
    """The coarse SUBJECT mask for one frame, or nothing to subtract.

    Coarse on purpose: it is dilated and subtracted, never rendered. What it
    must NOT be is absent, because then the residual is the whole foreground
    and the "opponent" is both fighters.
    """
    if seed_mask_fn is None:
        return np.zeros((2, 2), np.float32)
    return np.asarray(seed_mask_fn(f), np.float32)
