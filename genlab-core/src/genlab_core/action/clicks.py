"""Derived SAM2 click points for per-instance subject mattes.

This function failed three times while it was a description that got re-written
per call site. Each rewrite dropped a constraint the previous one had:

    v1 (segment)  negative = foreground AND NOT subject_colour        -- correct
    v2 (reel)     negative = a dark pixel in a luminance band         -- landed on
                  the subject's own trousers; SAM2 then learned "the trousers are
                  not the subject", object 1 collapsed to the shirt and object 2
                  tracked the subject. Instance overlap 89.6%.
    v3 (reel)     negative = foreground AND NOT subject_colour        -- the v1
                  rule, but at a tighter crop the second person is OUT OF FRAME,
                  so that set is again the subject's own skin. Overlap 15.3%,
                  with the matte blown out to the whole frame on some shots.

The rule is not "not the subject's colour". It is **a different person** -- a
separate connected component of the foreground -- and when the subject is alone
in frame the correct number of negative clicks is ZERO, not one placed on him.

It lives here, once, and every caller imports it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ClickSet:
    """Points for one frame. `negative` is None when the subject is alone."""

    positive: tuple[int, int]
    negative: tuple[int, int] | None

    @property
    def has_negative(self) -> bool:
        return self.negative is not None


def colour_seed(rgb: np.ndarray, spec: dict, foreground: np.ndarray | None = None) -> np.ndarray:
    """Binary mask of the subject's signature colour, scoped to the foreground.

    HSV since 2026-09-17: a brightness-and-ratio spec cannot name a dark colour.
    `spec` comes from `colour_seed.derive()` or niche config -- never a literal
    here, since gaming, movies and anime subjects are not in yellow shirts.
    """
    from .colour_seed import match

    return match(rgb, spec, foreground)


def _components(mask: np.ndarray, stride: int) -> list[np.ndarray]:
    """Connected components of a boolean mask, labelled at 1/stride resolution."""
    small = mask[::stride, ::stride]
    seen = np.zeros(small.shape, bool)
    out: list[np.ndarray] = []
    for y0, x0 in zip(*np.nonzero(small), strict=False):
        if seen[y0, x0]:
            continue
        queue = deque([(y0, x0)])
        seen[y0, x0] = True
        pts: list[tuple[int, int]] = []
        while queue:
            y, x = queue.popleft()
            pts.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < small.shape[0]
                    and 0 <= nx < small.shape[1]
                    and small[ny, nx]
                    and not seen[ny, nx]
                ):
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        out.append(np.array(pts) * stride)
    return out


def derive_clicks(
    rgb: np.ndarray,
    foreground: np.ndarray,
    subject_colour: dict,
    min_person_px: int,
    stride: int = 4,
) -> ClickSet | None:
    """Positive on the subject, negative on a DIFFERENT person, or None.

    `foreground` is a saliency matte (birefnet) of the same frame, computed on
    annotation frames only -- not on every frame of the clip.
    Returns None when the subject's colour is not present at all.
    """
    # The seed is matched INSIDE the foreground. Unscoped, it selected 10-19%
    # of the frame on real footage -- cage padding, canvas logos, crowd.
    seed = colour_seed(rgb, subject_colour, foreground)
    ys, xs = np.nonzero(seed > 0.5)
    if len(xs) < min_person_px:
        return None
    # centroid of the LARGEST matching component, not of all matches: a stray
    # patch of the same hue elsewhere in the foreground must not drag the click.
    seed_comps = _components(seed > 0.5, stride)
    if seed_comps:
        big = max(seed_comps, key=len)
        positive = (int(np.median(big[:, 1])), int(np.median(big[:, 0])))
    else:
        positive = (int(np.median(xs)), int(np.median(ys)))

    comps = _components(foreground > 0.5, stride)
    if not comps:
        return ClickSet(positive, None)

    def holds_subject(pts: np.ndarray) -> bool:
        sub = pts[:: max(1, len(pts) // 400)]
        return bool(
            seed[
                np.clip(sub[:, 0], 0, seed.shape[0] - 1),
                np.clip(sub[:, 1], 0, seed.shape[1] - 1),
            ].max()
            > 0.5
        )

    own_idx = next((i for i, p in enumerate(comps) if holds_subject(p)), None)
    others = [
        p for i, p in enumerate(comps) if i != own_idx and len(p) * stride * stride >= min_person_px
    ]
    if not others:
        # Alone in frame. A negative here would land on the subject himself,
        # which is what broke v2 and v3.
        return ClickSet(positive, None)

    biggest = max(others, key=len)
    negative = (int(np.median(biggest[:, 1])), int(np.median(biggest[:, 0])))
    return ClickSet(positive, negative)
