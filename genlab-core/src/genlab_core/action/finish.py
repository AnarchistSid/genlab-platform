"""Where the strike lands — three signals, and a gate that says when to believe them.

RENDER-01 Part 16.

WHY THIS IS NOT ONE SIGNAL
--------------------------
The first version read the opponent out of the tracker's silhouette dict, which
returns exactly one entry, so its separability test was unsatisfiable and it
returned frame 0 forever. The second DERIVED the opponent as
``foreground AND NOT dilate(subject)`` and did find descents -- but measured on
three windows of the same UFC-05 span it placed the finish at 24.60 s, 26.20 s
and 26.80 s absolute. A real strike lands at ONE absolute time whatever window
you sample it from. A residual is not the opponent: it is whatever the seed
missed, and on those frames it held referee, crowd, cage and the subject itself.

So the anchor is now two independent signals that must AGREE, plus the descent
demoted to a cross-check:

* **Audio onset** -- the impact makes a sound. Window-invariant by construction:
  it is computed from the clip's own timeline, so shifting the window cannot
  move it.
* **Subject velocity** -- the finisher drives toward the opponent. Read off the
  coarse seed mask that the vote already pays for.
* **Opponent descent** -- the man goes down. Only believed when the residual is
  a single component that stays continuous frame to frame, and never on its own.

And window-invariance is a GATE, not an observation: see `invariance_spread_s`.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

#: RMS envelope hop. 50 ms is two frames at 30 fps -- fine enough to place an
#: impact, coarse enough that crowd texture averages out.
ONSET_WIN_S = 0.050
ONSET_SR = 16000

#: THE AUDIO BOUNDS, THE VIDEO PICKS. Crowd audio is window-invariant but late
#: -- measured +0.317 s after the archive's finish, because a crowd reacts after
#: the punch lands. Subject motion is exact but fragile: the colour seed
#: collapsed at one frame of the UFC-05 window (0.11% of frame against a 0.5%
#: norm) and the centroid teleported 200 px, which an unbounded argmax read as a
#: +94.85 px/frame lunge. So the audio gives a coarse bracket and the motion
#: picks inside it; neither is trusted to do the other's job.
#:
#: The strike PRECEDES the crowd and never follows it, so the bracket is
#: asymmetric -- generous before the onset, barely anything after.
BOUND_BEFORE_S = 0.50
BOUND_AFTER_S = 0.10

#: Below this share of frame the colour seed has not found the garment, and its
#: centroid is wherever the few surviving pixels happen to be.
SEED_AREA_FLOOR = 0.0025

#: THE STRIKE IS THE RISING EDGE, NOT THE PEAK. Velocity keeps climbing into the
#: follow-through, so the maximum lands after contact -- measured on UFC-05, the
#: peak sat 6 frames (0.200 s) after the archive's finish, with the archive's
#: own frame inside the bracket and available. The edge is where the body
#: commits. First crossing of this share of the bracket's maximum.
EDGE_FRACTION = 0.70

#: ONE EVENT PER CLIP IS AN ASSUMPTION. Measured on the UFC-05 span: five
#: comparable sustained crowd rises in 60 s (9.25 +0.0874, 13.20 +0.0679,
#: 25.65 +0.0572, 59.45 +0.0507, 16.50 +0.0495), and the wanted one ranked
#: THIRD. An argmax anchor picks 9.25 and never looks at the finish.
#:
#: Two is the cap, not a parameter to raise: a clip needing more than two
#: anchors is not a highlight, and each surviving anchor costs a full vote.
ANCHOR_COUNT = 2

#: Two anchors inside this of each other are the same crowd reaction sampled
#: twice, not two events.
ANCHOR_MIN_SEPARATION_S = 2.0

#: A maximum on the bracket's boundary is a maximum OF THE BRACKET, not of the
#: signal -- the real peak is outside. Measured on UFC-05 window 24.9: the pick
#: and the peak were both frame 7 of a (7, 25) bracket. Widen once on that side
#: and re-pick; still on an edge means the bracket is in the wrong place.
EDGE_WIDEN_FRAMES = 6

#: Fewer valid samples than this inside the bracket and the colour heuristic has
#: not answered; the quarter-res tracker is asked instead.
MIN_VALID_SAMPLES = 5

#: The descent is a cross-check. Outside this it is logged as disagreeing; it
#: never vetoes and never anchors.
DESCENT_TOLERANCE = 6

#: Across candidates, the finish converted to absolute time must not spread
#: further than this. 0.2 s is six frames: wider means the detector is finding
#: a maximum inside each window rather than an event in the footage.
INVARIANCE_SPREAD_S = 0.20

#: Continuity for the residual, in the mask's own pixels and as a fraction.
#: A body does not teleport 40 px or change size by a third between samples.
MAX_CENTROID_JUMP_PX = 40.0
MAX_AREA_JUMP_FRAC = 0.30


class FinishFailure:
    NO_AUDIO = "finish_no_audio"
    NO_VELOCITY = "finish_no_velocity"
    NOT_INVARIANT = "finish_not_window_invariant"


@dataclass
class FinishResult:
    frame: int | None = None
    reason: str = ""
    audio_frame: int | None = None
    velocity_frame: int | None = None
    descent_frame: int | None = None
    descent_agrees: bool | None = None
    bound: tuple | None = None
    samples_in_bound: int = 0
    used_tracker: bool = False

    @property
    def ok(self) -> bool:
        return self.frame is not None


# ── signal 1: the impact makes a sound ──────────────────────────────────────


#: How the onset is read off the envelope.
#:
#: MEASURED on the UFC-05 span, three windows, absolute times:
#:
#:   detector                      23.2      23.6      24.9     spread
#:   50 ms first difference       23.600    23.950    27.000    3.400 s
#:   0.50 s level shift           25.650    25.650    25.650    0.000 s
#:
#: The first-difference transient is what a clean impact looks like, and it is
#: what a struck object in a quiet room gives you. Broadcast MMA audio is
#: commentary over a crowd: the strike itself is buried and every 50 ms rise in
#: the envelope is crowd texture, all of them the same size. What the strike
#: actually does to the audio is shift the crowd's LEVEL and hold it there.
#:
#: So the default is the one that satisfies the gate. The cost is a known lag:
#: the level shift lands +0.317 s after the archive's finish, because a crowd
#: reacts after the punch lands. That offset is consistent across windows, so it
#: is correctable by a constant -- but the constant would be invented here
#: rather than measured, so it is left visible instead.
ONSET_MODE_TRANSIENT = "transient"
ONSET_MODE_LEVEL = "level_shift"

#: Half-width of the level-shift comparison, in envelope hops. 10 hops = 0.5 s.
#: Measured: 0.25 s and 0.50 s both give spread 0.000 s; 1.0 s tracks the strike
#: more closely (+0.067 s) but spreads to 0.500 s and fails the gate.
LEVEL_WIN_HOPS = 10


def _envelope(path: str, start_s: float, n_frames: int, fps: float, run) -> np.ndarray | None:
    """RMS at 50 ms over the window.

    `-ss` goes AFTER `-i`. Before it, ffmpeg seeks to the nearest keyframe and
    two different start times decode the same position -- the defect that made
    `loopscore.py` compare the wrong frames.
    """
    dur = n_frames / float(fps)
    raw = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            path,
            "-ss",
            f"{start_s:.3f}",
            "-t",
            f"{dur:.3f}",
            "-ac",
            "1",
            "-ar",
            str(ONSET_SR),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
    ).stdout
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    hop = int(ONSET_SR * ONSET_WIN_S)
    if len(a) < hop * 4:
        logger.warning("[finish] no usable audio in %.2fs+%.2fs (%d samples)", start_s, dur, len(a))
        return None
    env = np.sqrt((a[: len(a) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1))
    if float(env.max()) <= 1e-6:
        logger.warning("[finish] audio is silent across the window")
        return None
    return env


def audio_onset_frame(
    path: str,
    start_s: float,
    n_frames: int,
    fps: float,
    *,
    mode: str = ONSET_MODE_LEVEL,
    run=subprocess.run,
) -> int | None:
    """The strike, as a WINDOW-RELATIVE frame index. See `ONSET_MODE_*`."""
    env = _envelope(path, start_s, n_frames, fps, run)
    if env is None:
        return None

    if mode == ONSET_MODE_TRANSIENT:
        rise = np.diff(env)
        if not len(rise) or float(rise.max()) <= 0:
            return None
        k = int(np.argmax(rise)) + 1
        detail = f"+{float(rise[k - 1]):.4f} rms step"
    else:
        w = LEVEL_WIN_HOPS
        if len(env) < 2 * w + 1:
            logger.warning("[finish] window too short for a %d-hop level shift", w)
            return None
        mean = np.convolve(env, np.ones(w) / w, "valid")
        step = mean[w:] - mean[:-w]
        if not len(step) or float(step.max()) <= 0:
            return None
        k = int(np.argmax(step)) + w
        detail = f"+{float(step[k - w]):.4f} sustained over {w * ONSET_WIN_S:.2f}s"

    t = k * ONSET_WIN_S
    frame = int(round(t * fps))
    logger.info(
        "[finish] audio onset (%s) at %.3fs into the window (%s) -> frame %d",
        mode,
        t,
        detail,
        frame,
    )
    return frame if 0 <= frame < n_frames else None


# ── signal 2: the finisher drives toward the opponent ───────────────────────


def _centroid(mask: np.ndarray) -> tuple[float, float] | None:
    m = np.asarray(mask) > 0.5
    if not m.any():
        return None
    ys, xs = np.nonzero(m)
    return float(xs.mean()), float(ys.mean())


def audio_bound(audio_frame: int, fps: float) -> tuple[int, int]:
    """The bracket the video picks inside. Asymmetric: the strike precedes the
    crowd's reaction and never follows it."""
    return (
        int(round(audio_frame - BOUND_BEFORE_S * fps)),
        int(round(audio_frame + BOUND_AFTER_S * fps)),
    )


def velocity_samples(masks_by_frame: dict) -> list:
    """[(frame, centroid_x, area_frac)] for masks that are actually a garment.

    Two rejections, both measured on UFC-05 frame 44: the seed covered 0.11% of
    frame against a 0.5% norm, and its centroid sat 200 px from where the
    fighter was. An area floor catches the collapse; taking the largest
    component catches the case where the seed is scattered but big enough.
    """
    out = []
    for f in sorted(masks_by_frame):
        m = np.asarray(masks_by_frame[f], np.float32)
        area = float((m > 0.5).mean())
        if area < SEED_AREA_FLOOR:
            logger.info("[finish] frame %d: seed is %.3f%% of frame — collapsed", f, area * 100)
            continue
        c = _centroid(largest_component(m))
        if c is None:
            continue
        out.append((f, c[0], area))
    return out


def subject_velocity_peak(
    samples: list, *, opponent_on_right: bool, bound: tuple | None = None
) -> int | None:
    """Frame of peak subject velocity TOWARD the opponent, inside the bracket.

    Speed alone peaks when the finisher backs off as much as when he commits;
    the sign is what makes it a strike. And the peak is taken INSIDE the audio
    bracket -- unbounded, it found a seed-collapse artefact 34 frames away from
    where the crowd said the strike was.
    """
    kept = continuous_samples(samples)
    if bound is not None:
        lo, hi = bound
        kept = [s for s in kept if lo <= s[0] <= hi]
    if len(kept) < 3:
        logger.warning(
            "[finish] %d usable subject sample(s)%s — no velocity",
            len(kept),
            f" in frames {bound[0]}..{bound[1]}" if bound else "",
        )
        return None
    idx = [f for f, _, _ in kept]
    x = np.asarray([v for _, v, _ in kept], float)
    v = np.diff(x) / np.maximum(np.diff(idx), 1)
    toward = v if opponent_on_right else -v
    k = min(3, len(toward))
    sm = np.convolve(np.pad(toward, (k // 2, k // 2), mode="edge"), np.ones(k) / k, "valid")

    peak = float(sm.max())
    if peak <= 0:
        logger.warning("[finish] no motion toward the opponent inside the bracket")
        return None
    # FIRST CROSSING of the edge threshold, not the argmax.
    j = int(np.argmax(sm >= EDGE_FRACTION * peak))
    logger.info(
        "[finish] velocity rises through %.0f%% of %.2f px/frame toward the %s at frame %d "
        "(peak at frame %d, %d samples in bound)",
        EDGE_FRACTION * 100,
        peak,
        "right" if opponent_on_right else "left",
        idx[j],
        idx[int(np.argmax(sm))],
        len(kept),
    )
    return idx[j]


# ── signal 3 (cross-check only): the man goes down ──────────────────────────


def largest_component(mask: np.ndarray, stride: int = 4) -> np.ndarray:
    """The biggest 4-connected blob. Labelled on a strided copy — the question
    is which blob, not its exact edge."""
    m = np.asarray(mask) > 0.5
    small = m[::stride, ::stride]
    if not small.any():
        return np.zeros_like(m, np.float32)
    seen = np.zeros_like(small, bool)
    h, w = small.shape
    best, best_size = None, 0
    for sy in range(h):
        for sx in range(w):
            if not small[sy, sx] or seen[sy, sx]:
                continue
            stack, cells = [(sy, sx)], []
            seen[sy, sx] = True
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < h and 0 <= nx < w and small[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(cells) > best_size:
                best, best_size = cells, len(cells)
    keep = np.zeros_like(small)
    for y, x in best or []:
        keep[y, x] = True
    full = np.repeat(np.repeat(keep, stride, axis=0), stride, axis=1)[: m.shape[0], : m.shape[1]]
    out = np.zeros_like(m)
    out[: full.shape[0], : full.shape[1]] = full
    return (m & out).astype(np.float32)


def _continuous(a, b) -> bool:
    _, y0, a0 = a
    _, y1, a1 = b
    return abs(y1 - y0) <= MAX_CENTROID_JUMP_PX and abs(a1 - a0) <= MAX_AREA_JUMP_FRAC * max(
        a0, 1e-6
    )


def continuous_samples(samples: list) -> list:
    """The LONGEST mutually-continuous run. `samples` is [(frame, y, area_frac)].

    Measured on the UFC-05 span, the unfiltered residual moved 54, 91 and 97 px
    between ADJACENT samples -- the mask changing subject, not a body falling.

    A forward scan comparing each sample to the last ACCEPTED one is not enough,
    and the real trace shows why: 347, 293, 281, 259, 271, 362, 265 keeps
    {347, 362} and discards the coherent middle, purely because 347 came first.
    Whichever sample happens to be first becomes the reference and the whole
    trajectory is judged against an outlier. The longest run has no such
    preference -- the answer stops depending on where the scan starts, which is
    the same property the invariance gate tests for, one level down.
    """
    best: list = []
    for i in range(len(samples)):
        run = [samples[i]]
        for nxt in samples[i + 1 :]:
            if _continuous(run[-1], nxt):
                run.append(nxt)
        if len(run) > len(best):
            best = run
    keep = {f for f, _, _ in best}
    dropped = [f for f, _, _ in samples if f not in keep]
    if dropped:
        logger.info("[finish] residual discontinuous at frames %s -- dropped", dropped)
    return best


def descent_frame(samples: list) -> int | None:
    """Steepest sustained descent among CONTINUOUS samples."""
    from genlab_core.action.window import finish_frame as steepest

    kept = continuous_samples(samples)
    if len(kept) < 3:
        logger.warning("[finish] %d continuous residual sample(s) — no descent", len(kept))
        return None
    return int(steepest([y for _, y, _ in kept], [f for f, _, _ in kept]))


# ── the anchor, and the gate ────────────────────────────────────────────────


def resolve(
    audio_f: int | None,
    velocity_f: int | None,
    descent_f: int | None = None,
    *,
    bound: tuple | None = None,
    samples_in_bound: int = 0,
    used_tracker: bool = False,
) -> FinishResult:
    """THE AUDIO BOUNDS, THE VIDEO PICKS.

    The previous shape asked the two signals to AGREE within four frames and
    called it unresolved otherwise. That threw away the thing each is good at:
    the crowd is window-invariant but lands ~0.3 s late, and subject motion is
    frame-exact but collapses when the colour seed does. Requiring them to
    coincide asks the late signal to be early.

    So the audio supplies the bracket and the motion picks the frame inside it.
    The descent stays a cross-check that may disagree out loud.
    """
    r = FinishResult(
        audio_frame=audio_f,
        velocity_frame=velocity_f,
        descent_frame=descent_f,
        bound=bound,
        samples_in_bound=samples_in_bound,
        used_tracker=used_tracker,
    )
    if audio_f is None:
        r.reason = FinishFailure.NO_AUDIO
        return r
    if velocity_f is None:
        r.reason = FinishFailure.NO_VELOCITY
        return r
    r.frame = int(velocity_f)
    if descent_f is not None:
        r.descent_agrees = abs(descent_f - r.frame) <= DESCENT_TOLERANCE
        if not r.descent_agrees:
            logger.warning(
                "[finish] descent says %d, the pick is %d — cross-check DISAGREES",
                descent_f,
                r.frame,
            )
    logger.info(
        "[finish] anchored at frame %d — audio bracket %s, %d sample(s)%s",
        r.frame,
        bound,
        samples_in_bound,
        " via the tracker" if used_tracker else "",
    )
    return r


def invariance_spread_s(finishes_abs: list[float]) -> float:
    """Spread, in seconds, of the same event seen from different windows."""
    return (max(finishes_abs) - min(finishes_abs)) if len(finishes_abs) >= 2 else 0.0


def is_window_invariant(finishes_abs: list[float], spread_s: float = INVARIANCE_SPREAD_S) -> bool:
    """Same footage, different start, same absolute answer — or it is finding a
    maximum, not an event. Fewer than two windows cannot disagree, so they pass;
    the log says the gate did not run rather than that it was cleared."""
    if len(finishes_abs) < 2:
        logger.info("[finish] invariance gate: %d window(s), nothing to compare", len(finishes_abs))
        return True
    spread = invariance_spread_s(finishes_abs)
    ok = spread <= spread_s
    (logger.info if ok else logger.warning)(
        "[finish] invariance gate: spread %.3fs across %d windows (max %.2fs) — %s",
        spread,
        len(finishes_abs),
        spread_s,
        "PASS" if ok else "FAIL",
    )
    return ok


def _in_bracket(samples: list, bound: tuple) -> list:
    lo, hi = bound
    return [s for s in continuous_samples(samples) if lo <= s[0] <= hi]


def pick_with_edge_guard(
    samples: list, *, opponent_on_right: bool, bound: tuple, widen: int = EDGE_WIDEN_FRAMES
) -> tuple:
    """(frame, bound_used) — the velocity pick, refusing boundary maxima.

    A pick sitting on the first or last sample of the bracket means the signal
    was still rising (or falling) where the bracket stopped looking. Widen once
    on that side. If it is still on an edge, the bracket is in the wrong place
    and there is no finish to report.
    """
    for attempt in (0, 1):
        f = subject_velocity_peak(samples, opponent_on_right=opponent_on_right, bound=bound)
        if f is None:
            return None, bound
        kept = _in_bracket(samples, bound)
        if not kept:
            return None, bound
        on_lo, on_hi = f == kept[0][0], f == kept[-1][0]
        if not (on_lo or on_hi):
            return f, bound
        if attempt:
            logger.warning(
                "[finish] pick %d is still on a bracket edge after widening %s — unresolved",
                f,
                bound,
            )
            return None, bound
        lo, hi = bound
        bound = (lo - widen if on_lo else lo, hi + widen if on_hi else hi)
        logger.info(
            "[finish] pick %d sat on a bracket edge — widening to %s and re-picking", f, bound
        )
    return None, bound


def level_shift_anchors(
    path: str,
    start_s: float = 0.0,
    duration_s: float | None = None,
    fps: float = 30.0,
    *,
    k: int = ANCHOR_COUNT,
    min_sep_s: float = ANCHOR_MIN_SEPARATION_S,
    run=subprocess.run,
) -> list[tuple[float, float]]:
    """[(time_s_in_clip, step), ...] — the top-k sustained level rises.

    The same statistic `audio_onset_frame` uses in level mode, read over a range
    rather than a window, and returned as a RANKED LIST instead of an argmax.

    THE LIMIT, PLAINLY: this ranks crowd reactions by size, and the reaction to
    the shot you want is not necessarily the biggest. On the UFC-05 span the
    archive's finish ranked third of five. Two anchors cover it when the clip is
    a highlight of one or two moments; for a long multi-event clip the fetcher's
    own highlight timestamp should seed the search instead, and a clip needing
    more than two anchors is not a highlight.
    """
    n_frames = int((duration_s or 0) * fps) or None
    env = _envelope(path, start_s, n_frames or int(3600 * fps), fps, run)
    if env is None:
        return []
    w = LEVEL_WIN_HOPS
    if len(env) < 2 * w + 1:
        logger.warning("[finish] range too short for a %d-hop level shift", w)
        return []
    mean = np.convolve(env, np.ones(w) / w, "valid")
    step = mean[w:] - mean[:-w]
    out: list[tuple[float, float]] = []
    for idx in np.argsort(step)[::-1]:
        t = start_s + (int(idx) + w) * ONSET_WIN_S
        if float(step[idx]) <= 0:
            break
        if any(abs(t - prev) < min_sep_s for prev, _ in out):
            continue
        out.append((round(t, 3), float(step[idx])))
        if len(out) >= k:
            break
    logger.info(
        "[finish] top-%d level-shift anchors: %s",
        k,
        ", ".join(f"{t:.2f}s (+{v:.4f})" for t, v in out) or "none",
    )
    return out
