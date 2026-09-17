"""The drawing flash: a redrawn frame over the finish, or nothing.

RENDER-01 Port 5. Oracle: `w2_draw2.py` from the ACTION-UFC-05 deliverable, plus
the flash anchoring `w2_build4.py` held inline.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
A drawing over the frame is a FLASH, not a cut -- same framing, same world,
composited over live footage for at most a handful of frames. For that to read
as the same moment rather than a different fight, the drawing has to match the
POSE underneath it. Plausibility is not the gate.

Asked for a ground finish, the model returns a standing exchange, because a
standing exchange is what "two MMA fighters" means to it. On the real UFC-05
finish all three tries came back as wide full-body cage compositions against a
tight, motion-blurred clinch, scoring IoU 0.21-0.45 against a 0.60 gate. The
clip shipped with NO drawing, which is the correct outcome: a missing flash
beats a flash showing a different fight than the footage under it.

The gate was validated before its verdict was believed -- self-IoU 1.000,
scale-invariance 0.986 on a 0.7x copy, and 0.291 subject-vs-OPPONENT as the
wrong-fighter floor. 0.21-0.45 sits at or barely above that floor, so the
drawings really did not match; the instrument was not at fault.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

IOU_GATE = 0.60
MAX_TRIES = 3
SEED = 1111

#: Flash envelope, as offsets from the finish frame. CONTENT-16's sequence,
#: centred so the WHITE frame lands ON the finish: freeze 1f, white 1f, then the
#: drawing ramping down, then live.
FLASH_OFFSETS: dict[int, float] = {1: 0.85, 2: 0.85, 3: 0.60, 4: 0.30}
FREEZE_OFFSET = -1
WHITE_OFFSET = 0


@dataclass
class DrawAttempt:
    index: int
    path: str
    task_id: str = ""
    iou: dict[str, float] = field(default_factory=dict)
    passed: bool = False


@dataclass
class DrawResult:
    finish_frame: int
    gate: float
    tries: list[DrawAttempt]
    drawing_path: str | None = None
    reason: str = ""

    @property
    def has_drawing(self) -> bool:
        return self.drawing_path is not None


def garment_words(hue: float) -> str:
    """Name the trunks' colour for the prompt. Unknown hues say 'dark'."""
    for lo, hi, name in (
        (345, 361, "crimson"),
        (0, 20, "crimson"),
        (20, 45, "orange"),
        (45, 70, "yellow"),
        (70, 160, "green"),
        (160, 200, "teal"),
        (200, 260, "navy"),
        (260, 300, "purple"),
        (300, 345, "magenta"),
    ):
        if lo <= hue < hi:
            return name
    return "dark"


def uniform_fit_iou(drawn: np.ndarray, live: np.ndarray) -> float:
    """IoU after fitting the drawn figure's bbox onto the live one, UNIFORMLY.

    Uniform, not stretched. Fitting a 4:5 drawing into a 9:16 frame by stretching
    scored 51.9% on an earlier version of this gate, and that was the gate's
    fault rather than the render's -- the drawing was fine, the comparison
    squashed it. `min(lw/dw, lh/dh)` preserves the aspect and centres the result
    on the live bbox.
    """
    dy, dx = np.nonzero(drawn > 0.5)
    ly, lx = np.nonzero(live > 0.5)
    if not len(dy) or not len(ly):
        return 0.0
    dw, dh = dx.max() - dx.min() + 1, dy.max() - dy.min() + 1
    lw, lh = lx.max() - lx.min() + 1, ly.max() - ly.min() + 1
    s = min(lw / dw, lh / dh)
    crop = Image.fromarray(
        (drawn[dy.min() : dy.max() + 1, dx.min() : dx.max() + 1] * 255).astype(np.uint8)
    )
    crop = crop.resize((max(1, int(dw * s)), max(1, int(dh * s))), Image.BILINEAR)
    canvas = np.zeros(live.shape, bool)
    a = np.asarray(crop, np.float32) / 255.0 > 0.5
    y0 = max(0, ly.min() + (lh - a.shape[0]) // 2)
    x0 = max(0, lx.min() + (lw - a.shape[1]) // 2)
    h = min(a.shape[0], canvas.shape[0] - y0)
    w = min(a.shape[1], canvas.shape[1] - x0)
    canvas[y0 : y0 + h, x0 : x0 + w] = a[:h, :w]
    lv = live > 0.5
    return float((canvas & lv).sum()) / max(float((canvas | lv).sum()), 1)


def build_prompt(subject_hue: float, opponent_aspect: float, opponent_hue: float = 15.0) -> str:
    """Ground vs standing comes from the silhouettes' own upright test.

    Both phrasings are written so they cannot contradict "falling" -- an earlier
    prompt said "knocked down" over a fighter already on the canvas, and the
    model resolved the contradiction by drawing a third thing.
    """
    state = (
        "collapsing to the canvas"
        if opponent_aspect < 1.2
        else "being knocked backwards off his feet"
    )
    sw, ow = garment_words(subject_hue), garment_words(opponent_hue)
    return (
        f"manga ink illustration, two shirtless MMA fighters inside a cage, the "
        f"{sw}-trunked fighter landing the finishing strike on the {ow}-trunked "
        f"fighter, who is {state}, bold black outlines, flat cel "
        f"colours, hard cel shadow, dramatic, white background, no referee, "
        f"no photographic texture"
    )


def score_attempt(
    figures: dict[str, np.ndarray], live: dict[str, np.ndarray]
) -> tuple[dict[str, float], bool]:
    """Per-FIGHTER IoU, and whether every fighter clears the gate.

    Per fighter and not pooled: a drawing can match one body and invent the
    other, and pooling would average that into a pass.
    """
    ious = {
        k: (uniform_fit_iou(figures[k].astype(np.float32), live[k]) if k in figures else 0.0)
        for k in ("subject", "opponent")
    }
    return ious, all(v >= IOU_GATE for v in ious.values())


def decide(
    finish_frame: int,
    live: dict[str, np.ndarray],
    render_attempt,
    figures_of,
    max_tries: int = MAX_TRIES,
) -> DrawResult:
    """Up to `max_tries` attempts, each more constrained; else NO drawing.

    `render_attempt(i, prompt, extra_refs, strength) -> (task_id, path)` and
    `figures_of(path) -> {"subject": mask, "opponent": mask}` are injected so the
    img2img call and the matting model stay out of this module -- and so the
    gate can be tested against the recorded UFC-05 outputs without spending.
    """
    if "subject" not in live or "opponent" not in live:
        return DrawResult(
            finish_frame, IOU_GATE, [], None, "both fighters not separable on the finish frame"
        )

    tries: list[DrawAttempt] = []
    for i in range(1, max_tries + 1):
        task_id, path = render_attempt(i)
        figs = figures_of(path)
        ious, ok = score_attempt(figs, live)
        tries.append(DrawAttempt(i, path, task_id, ious, ok))
        logger.info(
            "draw try %d: IoU subject=%.3f opponent=%.3f -> %s",
            i,
            ious["subject"],
            ious["opponent"],
            "PASS" if ok else "FAIL",
        )
        if ok:
            return DrawResult(finish_frame, IOU_GATE, tries, path, "")
    return DrawResult(
        finish_frame,
        IOU_GATE,
        tries,
        None,
        "no attempt matched the live poses; a missing flash beats a wrong one",
    )


def flash_frames(finish_frame: int, has_drawing: bool) -> dict[int, float]:
    """The frames the drawing is composited on, and at what opacity.

    Anchored to the FINISH, not to peak motion. v3 anchored to peak motion and
    fired the flash on frames 93/94/95 -- the last three frames of the segment --
    because when the window was re-picked to start at the finish, the anchor
    stayed where it was and the flash landed on the follow-through over the live
    ending. Empty when there is no drawing: the envelope must not exist without
    something to put in it.
    """
    if not has_drawing:
        return {}
    return {finish_frame + off: op for off, op in FLASH_OFFSETS.items()}


def flash_centre(frames: dict[int, float]) -> float | None:
    """Opacity-weighted centre of the flash, for the `flash_at_finish` gate."""
    if not frames:
        return None
    total = sum(frames.values())
    return sum(f * op for f, op in frames.items()) / total if total else None
