"""§2 — choose the ACTION subject ONCE per window, and hold that identity.

The defect this closes (measured on the UFC knockdown, 2026-09-17): the aura
walked off the red-trunked fighter. Sampling the dominant garment hue under the
matte on all 96 frames showed three distinct excursions --

    frames  5-17   hue 1-6 deg     on-seed 0.88-0.96   correct
    frames 24-47   hue 339-342     on-seed 0.23-0.30   wrong man
    frames 69-75   hue 269-296     on-seed 0.27-0.43   wrong man (blue)

-- so 49 of 96 frames carried the effect on the wrong body.

The seed was already held constant, which is why holding it was not enough.
``colour_seed.derive()`` answers "which saturated garment is here", and
``derive_clicks`` then takes the LARGEST component matching that hue. Both are
frame-local questions. On a frame where the subject is occluded or turned, the
largest matching component belongs to somebody else, and the annotation hands
SAM2 a click on the wrong fighter -- which then propagates.

Identity is a decision about the WINDOW, so it is made once, here, and every
annotation is bound to it. The template names the method; the clip supplies the
answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from genlab_core.action.colour_seed import _hsv, _hue_dist

logger = logging.getLogger(__name__)

# A standing fighter is taller than wide. Ratio, not pixels, so it survives the
# crop. 1.2 rather than 1.0 because a crouching-but-upright stance still clears
# it while a fallen body (lying across frame) does not.
UPRIGHT_ASPECT = 1.2
MIN_COMPONENT_PX = 400
# Skin is excluded from garment candidacy exactly as in colour_seed.
_SKIN_HUE_LO, _SKIN_HUE_HI, _SKIN_SAT_MAX = 5.0, 35.0, 0.6


@dataclass(frozen=True)
class Subject:
    """The one body the effects are allowed to read, for the whole window."""

    hue_deg: float
    centroid: tuple[int, int]
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1
    aspect: float
    chosen_on_frame: int
    reason: str

    def spec(self, hue_tol: float = 12.0, sat_min: float = 0.45, val_min: float = 0.15) -> dict:
        """The colour-seed spec bound to THIS subject, for every annotation."""
        return {"hue_deg": self.hue_deg, "hue_tol": hue_tol, "sat_min": sat_min, "val_min": val_min}


def _components(mask: np.ndarray, stride: int = 4) -> list[np.ndarray]:
    """Connected components of a boolean mask, coarse-strided for speed."""
    small = mask[::stride, ::stride]
    lab = np.zeros(small.shape, np.int32)
    cur = 0
    out: list[np.ndarray] = []
    h, w = small.shape
    for sy in range(h):
        for sx in range(w):
            if not small[sy, sx] or lab[sy, sx]:
                continue
            cur += 1
            stack = [(sy, sx)]
            lab[sy, sx] = cur
            pix = []
            while stack:
                y, x = stack.pop()
                pix.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and small[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
            if len(pix) * stride * stride >= MIN_COMPONENT_PX:
                full = np.zeros(mask.shape, bool)
                for y, x in pix:
                    full[y * stride : (y + 1) * stride, x * stride : (x + 1) * stride] = True
                out.append(full & mask)
    return out


def _garment_hue(
    rgb: np.ndarray, comp: np.ndarray, sat_min: float = 0.45, val_min: float = 0.15
) -> float | None:
    """Circular-mean hue of the saturated, non-skin pixels of one component."""
    h, s, v = _hsv(rgb)
    sel = comp & (s >= sat_min) & (v >= val_min)
    sel &= ~((h >= _SKIN_HUE_LO) & (h <= _SKIN_HUE_HI) & (s < _SKIN_SAT_MAX))
    if int(sel.sum()) < 150:
        return None
    ang = np.deg2rad(h[sel])
    return float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360)


def garment_groups(
    rgb: np.ndarray,
    fg: np.ndarray,
    *,
    n_bins: int = 36,
    min_share: float = 0.06,
    min_sep_deg: float = 60.0,
    sat_min: float = 0.45,
    val_min: float = 0.15,
) -> list[dict]:
    """Split the foreground's garment pixels into distinct bodies BY HUE.

    Connected components cannot separate two fighters in a clinch: measured on
    the UFC window's last frame, birefnet returns ONE component of 228,860 px
    containing both men. Hue can separate them -- the same frame's garment
    histogram has a red mode (330-10 deg, spanning 40 deg) and a blue mode
    (220-230 deg).

    This is what makes a reliable NEGATIVE click possible. ``derive_clicks``
    looks for a separate CONNECTED component to click negative on; when the
    bodies are merged there isn't one, so SAM2 got no instruction to exclude the
    opponent and bled onto him on 58 of 96 frames.

    Modes closer than ``min_sep_deg`` are merged, because one garment can span a
    wide arc: this fighter's crimson reads 330-10, and treating that as two
    bodies would be the same error in the opposite direction.
    """
    h, s_, v = _hsv(rgb)
    sel = (fg > 0.5) & (s_ >= sat_min) & (v >= val_min)
    sel &= ~((h >= _SKIN_HUE_LO) & (h <= _SKIN_HUE_HI) & (s_ < _SKIN_SAT_MAX))
    if int(sel.sum()) < 150:
        return []
    hh = h[sel]
    hist, edges = np.histogram(hh, bins=n_bins, range=(0, 360))
    order = np.argsort(hist)[::-1]
    centres: list[float] = []
    for i in order:
        if hist[i] < hh.size * min_share:
            break
        c = float(edges[i] + 180.0 / n_bins)
        if all(_hue_dist(np.array([c]), o)[0] >= min_sep_deg for o in centres):
            centres.append(c)
    if not centres:
        return []
    groups = []
    for c in centres:
        # Assign every garment pixel to its NEAREST centre so the arcs partition
        # the frame rather than overlapping.
        d_own = _hue_dist(h, c)
        nearest = np.ones(h.shape, bool)
        for o in centres:
            if o != c:
                nearest &= d_own <= _hue_dist(h, o)
        g = sel & nearest
        if int(g.sum()) < MIN_COMPONENT_PX:
            continue
        # Shape is measured on the LARGEST CONNECTED COMPONENT, never on the
        # scattered hue group. A downed fighter's crimson still appears all over
        # the frame -- trunks on the canvas, a glove, a logo -- and the bbox of
        # that scatter is tall, so the whole group reports "upright" while the
        # body is flat. Measured on the UFC window's last frame:
        #
        #   hue 15  (crimson)  whole-group 1.47   largest component 0.66  DOWN
        #   hue 225 (navy)     whole-group 1.51   largest component 1.00  up
        #
        # Using the group aspect picked the fighter who had just been knocked
        # out, and the whole reel rendered the aura onto the loser.
        comps = _components(g)
        if not comps:
            continue
        body = max(comps, key=lambda c: int(c.sum()))
        bbox, aspect, cen = _stats(body)
        groups.append(
            dict(
                hue=round(float(c), 1),
                pixels=g,
                body=body,
                px=int(g.sum()),
                body_px=int(body.sum()),
                bbox=bbox,
                aspect=float(aspect),
                centroid=cen,
            )
        )
    return sorted(groups, key=lambda g: -g["px"])


def _stats(comp: np.ndarray) -> tuple[tuple[int, int, int, int], float, tuple[int, int]]:
    ys, xs = np.nonzero(comp)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    w, hgt = max(x1 - x0 + 1, 1), max(y1 - y0 + 1, 1)
    return (x0, y0, x1, y1), hgt / w, (int(xs.mean()), int(ys.mean()))


def choose_subject(
    last_rgb: np.ndarray,
    last_fg: np.ndarray,
    window_rgbs: list[np.ndarray] | None = None,
    window_fgs: list[np.ndarray] | None = None,
    *,
    last_frame_index: int = -1,
) -> Subject | None:
    """Pick the fighter delivering the finish, ONCE, from the window's last frame.

    Candidates are garment-hue GROUPS, not connected components -- in a clinch
    the two men are one component and cannot be told apart spatially.

    Primary rule: the finisher is the one still UPRIGHT when it is over
    (``aspect > UPRIGHT_ASPECT``). That is the point of a knockdown: at the last
    frame the two bodies stop being symmetric, which is why the decision is made
    there and not on frame 0.

    Tie-break when both are upright (no knockdown in this window): the one with
    net motion TOWARD the other across the window -- the aggressor.

    Returns None when no garment hue is present: a bare-torso or monochrome
    window gets no subject rather than a guess.
    """
    groups = garment_groups(last_rgb, last_fg)
    if not groups:
        logger.warning("[subject] no garment group on the last frame — no subject")
        return None

    upright = [g for g in groups if g["aspect"] > UPRIGHT_ASPECT]
    if len(upright) == 1:
        pick = upright[0]
        reason = (
            f"only upright body on last frame (aspect {pick['aspect']:.2f} > "
            f"{UPRIGHT_ASPECT}); {len(groups)} garment group(s) "
            f"at {[g['hue'] for g in groups]}"
        )
    elif len(upright) > 1:
        pick, net = _by_motion_toward(upright, window_rgbs, window_fgs)
        if pick is None:
            pick = max(upright, key=lambda g: g["body_px"])
            reason = (
                f"{len(upright)} upright, motion tie-break unavailable — "
                f"largest garment ({pick['px']} px)"
            )
        else:
            reason = (
                f"{len(upright)} upright; net motion toward the other "
                f"({net:+.0f} px) — the aggressor"
            )
    else:
        pick = max(groups, key=lambda g: g["body_px"])
        reason = (
            f"no upright body (max aspect {max(g['aspect'] for g in groups):.2f}) "
            f"— largest garment group"
        )

    logger.info(
        "[subject] hue=%.1f centroid=%s aspect=%.2f — %s",
        pick["hue"],
        pick["centroid"],
        pick["aspect"],
        reason,
    )
    return Subject(
        hue_deg=pick["hue"],
        centroid=pick["centroid"],
        bbox=pick["bbox"],
        aspect=round(pick["aspect"], 3),
        chosen_on_frame=last_frame_index,
        reason=reason,
    )


def subject_clicks(rgb: np.ndarray, fg: np.ndarray, subject: Subject, *, hue_tol: float = 45.0):
    """Clicks for ONE annotation frame, bound to an already-chosen subject.

    Returns ``(positive, negative_or_None, diagnostics)``.

    The positive is the centroid of the garment group nearest the subject's
    HELD hue -- the hue is never re-derived here, which is the whole point. The
    negative is the centroid of the largest OTHER group, i.e. the opponent, and
    it comes from a hue group rather than a separate connected component so it
    still exists when the two bodies are merged.

    ``hue_tol`` is wide (45 deg) because it selects a BODY, not a colour: this
    fighter's crimson spans 330-10 deg, and the 12 deg seed tolerance matched
    only part of it.
    """
    groups = garment_groups(rgb, fg)
    scoped = True

    def _nearest(gs):
        if not gs:
            return None, None
        d = [float(_hue_dist(np.array([g["hue"]]), subject.hue_deg)[0]) for g in gs]
        k = int(np.argmin(d))
        return (k, d[k]) if d[k] <= hue_tol else (None, d[k])

    i, gap = _nearest(groups)
    if i is None:
        # birefnet's foreground is whoever IS salient, not whoever WE chose. On
        # this window it covers only the crimson fighter on frames 24 and 48,
        # so the navy subject -- present in frame the whole time, 49k-173k
        # garment px -- had no group inside the scope and its annotations were
        # skipped. SAM2 then lost him: 21 empty frames, area mean 1.14%.
        #
        # Fall back to the WHOLE FRAME for the subject's own hue only. The
        # scoping guard still does its job on the first pass (a same-hue canvas
        # logo cannot win while the body is in scope); this only widens the
        # search when the body is not in scope at all.
        whole = np.ones_like(fg)
        groups = garment_groups(rgb, whole)
        i, gap = _nearest(groups)
        scoped = False
        if i is not None:
            logger.info(
                "[subject] hue %.0f outside the foreground on this frame — "
                "searched the whole frame",
                subject.hue_deg,
            )
    if i is None:
        return (
            None,
            None,
            {
                "reason": f"subject hue {subject.hue_deg} absent even unscoped "
                f"(nearest group {gap:.0f} deg away)"
            },
        )

    # Centroid of the LARGEST CONNECTED COMPONENT of the hue group, never the
    # centroid of all matching pixels. A hue group is routinely split -- trunks
    # plus a glove, or the same red on both sides of frame -- and the mean of a
    # disjoint set lands between the parts, on neither body. Measured cost of
    # getting this wrong on this very clip: the matte collapsed to 0.5% of frame
    # on frames 0-8 and what survived sat on the OPPONENT.
    #
    # clicks.py already carried this constraint. It was lost by re-deriving the
    # rule here instead of reusing it -- promote code, not descriptions.
    def _largest_centroid(mask):
        comps = _components(mask)
        if not comps:
            return None
        big = max(comps, key=lambda c: int(c.sum()))
        ys, xs = np.nonzero(big)
        return (int(xs.mean()), int(ys.mean())), int(big.sum())

    pos_hit = _largest_centroid(groups[i]["body"])
    if pos_hit is None:
        return (
            None,
            None,
            {"reason": f"subject hue present but no component >= {MIN_COMPONENT_PX}px"},
        )
    pos, pos_px = pos_hit

    others = [g for j, g in enumerate(groups) if j != i]
    neg = None
    if others:
        neg_hit = _largest_centroid(max(others, key=lambda g: g["body_px"])["body"])
        neg = neg_hit[0] if neg_hit else None
    return (
        pos,
        neg,
        {
            "subject_component_px": pos_px,
            "foreground_scoped": scoped,
            "subject_hue": groups[i]["hue"],
            "hue_delta": round(gap, 1),
            "subject_px": groups[i]["px"],
            "opponent_hue": (max(others, key=lambda g: g["body_px"])["hue"] if others else None),
        },
    )


def _by_motion_toward(upright: list[dict], window_rgbs, window_fgs):
    """Which upright body closed the distance across the window?

    Returns ``(group, net_px)``, or ``(None, None)`` when the window is too
    short or a body cannot be located on the first frame.
    """
    if not window_rgbs or not window_fgs or len(window_fgs) < 2 or len(upright) < 2:
        return None, None
    first = garment_groups(window_rgbs[0], window_fgs[0])
    if len(first) < 2:
        return None, None
    xs_now = [g["centroid"][0] for g in upright]
    toward = []
    for i, g in enumerate(upright):
        d = [float(_hue_dist(np.array([f["hue"]]), g["hue"])[0]) for f in first]
        then = first[int(np.argmin(d))]["centroid"][0]
        other_now = (
            xs_now[1 - i]
            if len(xs_now) == 2
            else float(np.mean([v for j, v in enumerate(xs_now) if j != i]))
        )
        # Positive = moved in the direction of the opponent.
        toward.append((g["centroid"][0] - then) * np.sign(other_now - g["centroid"][0]))
    i = int(np.argmax(toward))
    return upright[i], float(toward[i])
