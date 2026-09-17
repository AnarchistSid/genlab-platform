"""Choose the ACTION subject from SILHOUETTES, by a vote over frames.

Why this replaces the garment-geometry rule (ACTION-UFC-04 §2).

The previous rule asked whether a garment-hue component looked upright. Measured
on the UFC knockdown, varying ONLY the last frame across 0.7s -- 14 consecutive
frames at 60fps -- the answer flipped eight times:

    235, 225, 15, 15, 15, 225, 225, 15, 235, 235, 235, 225, 225, 15

    crimson garment aspect  0.53 - 1.73   (swings across the 1.2 bar)
    navy    garment aspect  0.84 - 1.10   (never clears it)

Whenever the DOWNED fighter's trunks happened to look elongated he cleared the
bar and was chosen as "the only upright body", while the standing fighter never
did. A garment's aspect is not a body's posture: trunks on a fallen fighter
elongate with leg position, and a standing fighter's trunks are roughly square.
Position was no rescue either -- the downed man was the lower body on only 7 of
14 frames, because the largest same-hue component is not stably the same
physical object.

Three changes, each closing one of those failures:

* **Silhouettes, not garments.** One SAM2 *image* call per frame, prompted with
  the two derived hue clicks, gives two real bodies. Shape questions are then
  asked of a body.
* **A vote over the last N frames.** A rule that changes with a 33ms shift is
  not a rule. Majority wins; ties go to the larger mean silhouette.
* **Take SAM2's LARGEST mask, not its highest-scoring one.** ``multimask_output``
  returns three granularities -- subpart, part, whole -- and a click on trunks
  scores the TRUNKS highest. Measured on the window's last frame, the
  whole-body masks that separate the fighters cleanly (navy aspect 1.39
  standing, crimson 0.82 down) were SAM2's LOWEST-scored at 0.162 and 0.205,
  while the top-scored masks were 0.9-1.6% slivers that the area floor rightly
  rejected. Selecting by score is how "one SAM2 call per frame" silently
  becomes "one garment per frame" again.

* **An area floor before any shape test.** A silhouette under MIN_BODY_FRAC of
  the crop is residue -- a glove, a logo, a sliver of a limb -- and cannot be
  chosen. Without it, scattered residue wins by being accidentally tall.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

VOTE_FRAMES = 10
MIN_BODY_FRAC = 0.04      # a silhouette smaller than this is not a body
UPRIGHT_ASPECT = 1.2      # measured on the SILHOUETTE, not on a garment patch
HUE_SAME_BODY = 45.0      # votes this close name the same fighter
STACKED_DY = 0.12         # centroid gap that means one body is ON the other
STACKED_X_OVERLAP = 0.50  # ...and they must overlap horizontally to be stacked


@dataclass(frozen=True)
class FrameVerdict:
    frame_index: int
    winner_hue: float | None
    reason: str
    areas: dict = field(default_factory=dict)
    aspects: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SubjectVote:
    hue_deg: float | None
    votes: int
    frames: int
    unanimous: bool
    verdicts: list[FrameVerdict]
    reason: str

    @property
    def margin(self) -> float:
        return self.votes / max(self.frames, 1)


def _stats(mask: np.ndarray) -> tuple[float, float, float, tuple[int, int]]:
    """(area fraction, bbox aspect h/w, centroid y fraction, x-extent)."""
    ys, xs = np.nonzero(mask > 0.5)
    if not len(ys):
        return 0.0, 0.0, 1.0, (0, 0)
    h = float(ys.max() - ys.min() + 1)
    w = float(xs.max() - xs.min() + 1)
    return (float((mask > 0.5).mean()), h / max(w, 1.0),
            float(ys.mean()) / mask.shape[0], (int(xs.min()), int(xs.max())))


def _x_overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Share of the NARROWER x-extent that the two boxes share."""
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    if hi <= lo:
        return 0.0
    narrow = min(a[1] - a[0], b[1] - b[0]) or 1
    return (hi - lo) / narrow


def _pick_one(sils: dict[float, np.ndarray]) -> tuple[float | None, str, dict, dict]:
    """Finisher on ONE frame: upright, else higher in frame, else larger."""
    areas, aspects, tops, xext = {}, {}, {}, {}
    for hue, m in sils.items():
        a, asp, cy, xr = _stats(m)
        areas[hue], aspects[hue], tops[hue], xext[hue] = a, asp, cy, xr

    # Area floor FIRST. Residue never competes on shape.
    live = {h: a for h, a in areas.items() if a >= MIN_BODY_FRAC}
    if not live:
        return None, (f"no silhouette over the {MIN_BODY_FRAC:.0%} floor "
                      f"(areas {({h: round(a, 4) for h, a in areas.items()})})"), areas, aspects
    if len(live) == 1:
        h = next(iter(live))
        return h, f"only body over the floor (area {live[h]:.1%})", areas, aspects

    # GROUND FINISH: when the two bodies are stacked -- one substantially above
    # the other, bboxes overlapping horizontally -- the finisher is the one ON
    # TOP, whatever his posture. Measured on this window: at t=678.7 the navy
    # finisher has dropped into ground-and-pound and his silhouette is no longer
    # upright (a crouch is wide), while the sprawled loser presents the taller
    # box, so an upright-first rule votes 10/10 for the man being hit. Posture
    # is a STANDING-KO signal; vertical order survives both.
    if len(live) == 2:
        a, b = sorted(live, key=lambda h: tops[h])
        if (tops[b] - tops[a] >= STACKED_DY
                and _x_overlap(xext[a], xext[b]) >= STACKED_X_OVERLAP):
            return a, (f"stacked: on top (centroid y {tops[a]:.2f} vs "
                       f"{tops[b]:.2f})"), areas, aspects

    upright = [h for h in live if aspects[h] >= UPRIGHT_ASPECT]
    if len(upright) == 1:
        h = upright[0]
        return h, f"only upright silhouette (aspect {aspects[h]:.2f})", areas, aspects

    pool = upright or list(live)
    hi = min(pool, key=lambda h: tops[h])          # smaller y = higher in frame
    others = [h for h in pool if h != hi]
    if others and abs(tops[hi] - tops[max(others, key=lambda h: tops[h])]) > 0.05:
        return hi, f"higher in frame (centroid y {tops[hi]:.2f})", areas, aspects
    h = max(pool, key=lambda x: areas[x])
    return h, f"larger silhouette (area {areas[h]:.1%})", areas, aspects


def choose_subject_by_vote(
    frames: list,
    silhouette_fn: Callable[[object], dict[float, np.ndarray]],
    *, vote_frames: int = VOTE_FRAMES,
) -> SubjectVote:
    """Vote the finisher over the LAST ``vote_frames`` frames of the window.

    ``silhouette_fn(frame) -> {hue: mask}`` runs one SAM2 image call per frame
    and returns a silhouette per derived hue click.

    TWO REQUIREMENTS ON silhouette_fn, both measured, both easy to get wrong:

    1. **Give SAM2 a NEGATIVE click on the other fighter.** With positives only,
       two clicks inside an entangled pair return the SAME merged blob --
       measured at t=675.5: areas 7.67% vs 7.62%, aspects 1.57 vs 1.57, so the
       vote turned on a 0.05pp difference, which is noise. Adding the negative
       moved the window's vote from 6/10 for the LOSER to 10/10 unanimous for
       the finisher.
    2. **Take the LARGEST plausible mask, not the highest-scoring one** (see the
       module docstring).

    Known boundary: this rule assumes the finisher is distinguishable from the
    man he beat. Roughly 0.6s past the finish the pair re-entangles in
    ground-and-pound and the vote flips back to the loser. Pick windows that
    start AT the finish.
    """
    tail = frames[-vote_frames:]
    verdicts: list[FrameVerdict] = []
    area_sums: dict[float, list[float]] = {}
    for i, f in enumerate(tail):
        sils = silhouette_fn(f)
        hue, reason, areas, aspects = _pick_one(sils)
        for h, a in areas.items():
            area_sums.setdefault(h, []).append(a)
        verdicts.append(FrameVerdict(len(frames) - len(tail) + i, hue, reason,
                                     areas, aspects))
        logger.info("[subject-vote] frame %d -> %s (%s)",
                    verdicts[-1].frame_index,
                    "none" if hue is None else f"{hue:.1f}", reason)

    # Merge votes for the SAME body. The hue histogram bins at 10 degrees, so
    # one fighter can be named 225 on nine frames and 235 on the tenth; counting
    # those separately understates the majority and could hand a 5-5 "tie" to a
    # body that never led. Anything within HUE_SAME_BODY is one candidate.
    raw = [v.winner_hue for v in verdicts if v.winner_hue is not None]
    merged: list[float] = []
    for h in raw:
        near = [c for c in merged if abs((h - c + 180) % 360 - 180) <= HUE_SAME_BODY]
        merged.append(near[0] if near else h)
    tally = Counter(merged)
    if not tally:
        return SubjectVote(None, 0, len(tail), False, verdicts,
                           "no frame produced a body over the area floor")
    top = tally.most_common()
    best, votes = top[0]
    if len(top) > 1 and top[1][1] == votes:
        tied = [h for h, c in top if c == votes]
        best = max(tied, key=lambda h: float(np.mean(area_sums.get(h, [0.0]))))
        reason = (f"tie at {votes}/{len(tail)} between {tied} — larger mean "
                  f"silhouette wins ({best:.1f})")
    else:
        reason = f"majority {votes}/{len(tail)}"
    vote = SubjectVote(float(best), votes, len(tail), votes == len(tail), verdicts, reason)
    logger.info("[subject-vote] SUBJECT hue=%.1f — %s", vote.hue_deg, vote.reason)
    return vote
