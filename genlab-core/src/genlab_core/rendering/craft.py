"""Execute a Storyboard. Decide nothing.

RENDER-01 Port 8. Oracle: the ACTION-UFC-05 v4 build script end to end.

THE DIVISION OF LABOUR
----------------------
The Storyboard is the plan: classifier verdict, window, subject, per-shot
magnification, beat grid, event list, grade, drawing. Everything that required a
judgement was made when the plan was built and checked by the plan-time gates.
This module turns that plan into frames and takes no decisions of its own -- if
it finds itself choosing, the plan was incomplete.

That split is what makes a render reproducible: replaying a Storyboard replays
the reel, and a difference in output is a difference in plan.

CRAFT NEVER BLOCKS A PUBLISH
----------------------------
FFmpeg produces a publishable reel standalone at every stage, so craft is
additive. Any failure here returns a reason and the caller renders legacy. The
worst outcome is a legacy reel plus a recorded reason -- never "no reel".

THE COMPOSE ORDER IS FIXED, AND IT IS NOT ARBITRARY
---------------------------------------------------
Taken from the approved build, in this order:

    motion blur (fast frames only, on the UNGRADED source)
    grade                     world dimmed, subject lit
    ambient haze              scene-wide wash
    [ white flash -> emit; the flash frame carries nothing else ]
    heat shimmer              warps pixels, so before anything that adds light
    aura                      + the head halo, + inner spill
    bolts / afterglow
    debris
    bloom                     highlights, after everything that makes them
    drawing flash             an overlay, once, last
    push                      the slow zoom, applied to the finished frame

Three of those placements are load-bearing. Motion blur reads the ungraded
source because grading first smears an already-dimmed world and loses the
highlight streaks. Heat shimmer displaces pixels rather than adding light, so
anything composited before it gets dragged. Bloom must come after every effect
that creates a highlight, or it blooms the footage and not the effects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from genlab_core.action.effects import _ops as ops
from genlab_core.action.effects import impact as fx
from genlab_core.storyboard.models import Scope, Storyboard, Treatment

logger = logging.getLogger(__name__)

#: Effects that may not appear in the tail. `motion_blur` is deliberately
#: absent: a directional smear along the frame's own motion vector is the
#: footage filmed differently, not a graphic sitting on top of it.
TAIL_OVERLAYS = frozenset(
    {"drawing_flash", "white_flash", "mega_bolt", "bolt_afterglow", "debris", "heat_shimmer"}
)


class CraftUnavailable(RuntimeError):
    """Craft cannot render this. The caller falls back to legacy, never fails."""


@dataclass
class FrameSources:
    """Everything the executor reads, injected rather than fetched.

    The renderer owning no I/O is what lets the whole-pipeline gate run against
    an archived deliverable without a network, a GPU, or a model download.
    """

    clean: dict[int, np.ndarray]  # graded-source frames by index
    matte: dict[int, np.ndarray]  # subject matte per output frame
    head_region: dict[int, np.ndarray] = field(default_factory=dict)
    opponent: dict[int, np.ndarray] = field(default_factory=dict)
    drawing_rgba: np.ndarray | None = None


@dataclass
class CraftResult:
    frames: list[np.ndarray]
    events: list[tuple[int, str]]
    notes: list[str] = field(default_factory=list)


def _require(cond: bool, why: str) -> None:
    if not cond:
        raise CraftUnavailable(why)


def source_frame_for(sb: Storyboard, frame: int, available: Sequence[int]) -> int:
    """Which source frame this output frame draws on.

    A plan may reuse a source frame -- the freeze before the finish is exactly
    that -- so this is a lookup, not an identity.
    """
    for shot in sb.shots:
        if shot.start_frame <= frame <= shot.end_frame and shot.source_frame is not None:
            return shot.source_frame
    return frame if frame in available else (sb.window.finish_frame if sb.window else 0) or 0


def push(img: np.ndarray, frame: int, total: int, beat_phase: float) -> np.ndarray:
    """The slow zoom. Applied LAST, to the finished frame, so it moves the
    composite rather than the footage under it."""
    from PIL import Image

    h, w = img.shape[:2]
    z = 1.0 + 0.06 * (frame / max(total - 1, 1)) + 0.012 * beat_phase
    cw, ch = int(w / z), int(h / z)
    dx, dy = int(3 * np.sin(frame * 0.21)), int(3 * np.cos(frame * 0.17))
    x0, y0 = (w - cw) // 2 + dx, (h - ch) // 2 + dy
    return np.asarray(
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
        .crop((x0, y0, x0 + cw, y0 + ch))
        .resize((w, h), Image.LANCZOS),
        np.float32,
    )


def execute(
    sb: Storyboard,
    src: FrameSources,
    kit: dict,
    *,
    beat_phase: Callable[[int], float] | None = None,
    rng: np.random.Generator | None = None,
) -> CraftResult:
    """Storyboard + sources -> frames. Raises CraftUnavailable, never returns
    a partial reel."""
    _require(sb.total_frames > 0, "storyboard has no frames")
    _require(bool(src.clean), "no source frames supplied")
    _require(sb.treatment_is(Treatment.ACTION), f"craft has no path for {sb.classifier.treatment}")

    available = sorted(src.clean)
    rng = rng or np.random.default_rng(23)
    phase_of = beat_phase or (lambda f: 0.0)
    by_frame: dict[int, list[str]] = {}
    for e in sb.events:
        by_frame.setdefault(e.frame, []).append(e.kind)

    grade = sb.grade or {}
    world_luma = float(grade.get("world_luma", 0.30))
    subject_luma = float(grade.get("subj_luma", grade.get("subject_luma", 0.92)))
    # Per clip, not a kit constant -- see grade_world. Absent from the plan means
    # "use the kit default", which is a different statement from "use 0.62".
    vignette = grade.get("vignette")
    feather = grade.get("feather")

    finish = sb.window.finish_frame if sb.window else None
    debris_seed = None
    out: list[np.ndarray] = []
    events: list[tuple[int, str]] = []
    prev_core: np.ndarray | None = None
    prev_source: np.ndarray | None = None

    for f in range(sb.total_frames):
        kinds = by_frame.get(f, [])
        s = source_frame_for(sb, f, available)
        _require(s in src.clean, f"frame {f} wants source {s}, which was not supplied")
        base = src.clean[s]
        m = src.matte.get(f)
        _require(m is not None, f"no matte for frame {f}")
        mb = (m > 0.5).astype(np.float32)
        head = src.head_region.get(f)
        keep = 1.0 - np.clip(head, 0, 1) if head is not None else np.ones(mb.shape, np.float32)
        ys, _ = np.nonzero(mb > 0.5)
        subj_h = float(ys.max() - ys.min() + 1) if len(ys) else float(mb.shape[0])
        bp = phase_of(f)

        img = base
        # 1. motion blur, on the UNGRADED source: grading first smears an
        #    already-dimmed world and loses the highlight streaks.
        if "motion_blur" in kinds and prev_source is not None:
            img = fx.directional_blur(base, prev_source, kit)
            events.append((f, "motion_blur"))

        # 2. grade, 3. ambient haze
        img = fx.grade_world(
            img,
            m,
            kit,
            world_luma=world_luma,
            subject_luma=subject_luma,
            vignette=vignette,
            feather=feather,
        )
        img = np.clip(ops.screen(img / 255.0, fx.ambient_haze(mb, kit, subj_h)), 0, 1) * 255.0

        # 4. the white flash carries nothing else -- it IS the frame
        if "white_flash" in kinds:
            flash_m = src.matte.get(finish, mb) if finish is not None else mb
            img = (
                np.clip(
                    ops.screen(img / 255.0, fx.body_flash((flash_m > 0.5).astype(np.float32), kit)),
                    0,
                    1,
                )
                * 255.0
            )
            events.append((f, "white_flash"))
            out.append(push(img, f, sb.total_frames, bp))
            prev_source = base
            continue

        # 5. heat shimmer warps pixels, so it precedes anything that adds light
        if "heat_shimmer" in kinds:
            img = fx.heat_shimmer(img, mb, f)
            events.append((f, "heat_shimmer"))

        # 6. aura, weighted and kept off the face, then the inner spill
        if "aura" in kinds or bp > 0:
            lay = fx.aura(img, mb, kit, bp, f, subj_h) * keep[..., None]
            img = np.clip(ops.screen(img / 255.0, lay), 0, 1) * 255.0
            if head is not None:
                halo = fx.aura(img, mb, kit, bp, f, subj_h)
                hreg = (np.clip(head, 0, 1) * (1.0 - np.clip(ops.blur(mb, 8.0), 0, 1)))[..., None]
                img = img * (1 - hreg * float(fx.kit_value(kit, "head_halo", 0.40))) + (
                    np.clip(ops.screen(img / 255.0, halo), 0, 1) * 255.0
                ) * hreg * float(fx.kit_value(kit, "head_halo", 0.40))
            spill = fx.inner_glow(mb, kit)
            img = (
                img * (1 - keep[..., None])
                + (np.clip(ops.screen(img / 255.0, spill), 0, 1) * 255.0) * keep[..., None]
            )
            if "aura" in kinds:
                events.append((f, "aura"))

        # 7. bolts, and the afterglow that must follow one
        if "mega_bolt" in kinds:
            lay, prev_core = fx.bolts(mb, kit, rng, subj_h=subj_h, prev_core=None)
            img = (
                img * (1 - keep[..., None])
                + (np.clip(ops.screen(img / 255.0, lay), 0, 1) * 255.0) * keep[..., None]
            )
            events.append((f, "mega_bolt"))
        elif prev_core is not None:
            lay, _ = fx.bolts(mb, kit, rng, subj_h=subj_h, prev_core=prev_core)
            img = (
                img * (1 - keep[..., None])
                + (np.clip(ops.screen(img / 255.0, lay), 0, 1) * 255.0) * keep[..., None]
            )
            events.append((f, "bolt_afterglow"))
            prev_core = None

        # 8. debris, thrown from the contact point
        if "debris" in kinds and finish is not None:
            if debris_seed is None:
                cy, cx = ops.centroid(src.matte.get(finish, mb)) or (
                    mb.shape[1] / 2,
                    mb.shape[0] / 2,
                )
                debris_seed = fx.Debris.at(cx, cy, kit)
            lay = fx.debris(debris_seed, f - finish, mb)
            img = (
                img * (1 - keep[..., None])
                + (np.clip(ops.screen(img / 255.0, lay), 0, 1) * 255.0) * keep[..., None]
            )
            events.append((f, "debris"))

        # 9. bloom AFTER everything that makes a highlight
        img = np.clip(ops.screen(img / 255.0, fx.bloom(img, kit)), 0, 1) * 255.0

        # 10. the drawing: an overlay, once, last
        if "drawing_flash" in kinds and src.drawing_rgba is not None:
            op = next(
                (e.detail for e in sb.events if e.frame == f and e.kind == "drawing_flash"), ""
            )
            alpha = float(op) if op.replace(".", "", 1).isdigit() else 0.85
            rgba = src.drawing_rgba
            a = (rgba[..., 3:4] / 255.0) * alpha
            img = img * (1 - a) + rgba[..., :3] * a
            events.append((f, "drawing_flash"))

        out.append(push(img, f, sb.total_frames, bp))
        prev_source = base

    return CraftResult(frames=out, events=events, notes=list(sb.notes))


def ends_live(events: Sequence[tuple[int, str]], total_frames: int, tail: int = 12) -> list:
    """Overlays in the final `tail` frames. Must be empty."""
    start = total_frames - tail
    return [(f, k) for f, k in events if f >= start and k in TAIL_OVERLAYS]


def plate_appears_once(events: Sequence[tuple[int, str]], kind: str = "drawing_flash") -> bool:
    """A plate is a flash, not a state: one contiguous run, not two."""
    frames = sorted(f for f, k in events if k == kind)
    if not frames:
        return True
    return frames == list(range(frames[0], frames[0] + len(frames)))


def render(sb: Storyboard, src: FrameSources, kit: dict, **kw) -> CraftResult | None:
    """execute(), but returning None instead of raising.

    The caller renders legacy on None. Craft never blocks a publish, so the
    boundary where that guarantee is enforced has to be a real function rather
    than a convention every call site is trusted to remember.
    """
    try:
        res = execute(sb, src, kit, **kw)
    except CraftUnavailable as exc:
        logger.warning("craft unavailable, falling back to legacy: %s", exc)
        return None
    except Exception:
        logger.warning("craft raised, falling back to legacy", exc_info=True)
        return None
    late = ends_live(res.events, sb.total_frames)
    if late:
        logger.warning("craft output does not end live (%s); falling back to legacy", late[:3])
        return None
    if sb.scope is Scope.REEL and not plate_appears_once(res.events):
        logger.warning("craft plate appears more than once; falling back to legacy")
        return None
    return res
