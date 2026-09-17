"""Adapt the port's LAYER contract to the oracle's FRAME contract.

The approved build calls effects that return a composited FRAME. The port
returns LAYERS the renderer composites. This shim is the only translation, and
it is deliberately thin: the compose ORDER and the ARGUMENTS come from the
approved build itself, which is exactly what a re-description gets wrong.

The one non-trivial adaptation is `flame_aura`. The port folds the build-layer
weighting (vertical_weight * limb_weight) into its returned layer; the build
applies that weighting itself, right after the call. Applying both would square
it, so the shim divides it back out.
"""

from pathlib import Path

import numpy as np
import yaml
from genlab_core.action.effects import _ops as P
from genlab_core.action.effects import impact as I

_KIT_PATH = Path(I.__file__).parents[1] / "kits" / "impact.yaml"
KIT = yaml.safe_load(_KIT_PATH.read_text())


def _on(f, lay):
    """Composite a port LAYER onto an oracle-contract FRAME."""
    return np.clip(P.screen(f / 255.0, lay), 0, 1) * 255.0


class L6:
    blur, dilate, erode, screen = (
        staticmethod(P.blur),
        staticmethod(P.dilate),
        staticmethod(P.erode),
        staticmethod(P.screen),
    )
    dist_outside, noise = staticmethod(P.dist_outside), staticmethod(P.noise)
    WHITE, BOLT, ORANGE, RED, LUM = P.WHITE, P.BOLT, P.ORANGE, P.RED, P.LUM

    @staticmethod
    def flame_aura(f, m, t_frame, beat, subj_h, **kw):
        lay = I.aura(f, m, KIT, beat, t_frame, subj_h)
        if "gain" in kw:  # the build passes gain=0.22 / 0.31 for the cold + head halos
            k2 = {
                **KIT,
                "effect_constants": {**KIT["effect_constants"], "aura_gain": {"value": kw["gain"]}},
            }
            lay = I.aura(f, m, k2, beat, t_frame, subj_h)
        wgt = (
            P.vertical_weight(m) * P.limb_weight(m, float(I.kit_value(KIT, "limb_weight", 0.7)))
        )[..., None]
        lay = lay / np.maximum(wgt, 1e-6)
        inner = (
            np.clip(P.blur(m, 26.0) - P.erode(m, 40), 0, 1)[..., None] * 0.34 * (0.9 + 0.4 * beat)
        )
        return _on(_on(f, np.clip(lay, 0, 1)), inner * (P.ORANGE / 255.0))

    @staticmethod
    def body_flash(f, m, amount=0.90):
        return _on(f, I.body_flash(m, KIT, amount))

    @staticmethod
    def heat_shimmer(f, m, t_frame, amount=7.0, band=160.0):
        return I.heat_shimmer(f, m, t_frame, amount, band)


class L8:
    @staticmethod
    def grade_bathed(f, m, **kw):
        # grade_bathed's OWN defaults, not the kit's -- the kit carries UFC-05's
        # solved values and this build is WWE v6.
        return I.grade_world(
            f,
            m,
            KIT,
            world_luma=kw.get("world_luma", 0.30),
            subject_luma=kw.get("subj_luma", 0.92),
            vignette=kw.get("vignette_amt", 0.62),
            feather=kw.get("feather", 34.0),
        )

    @staticmethod
    def aura_ambient(f, m, subj_h, radius=300.0, opacity=0.08):
        return _on(f, I.ambient_haze(m, KIT, subj_h, radius, opacity))

    @staticmethod
    def inner_spill(f, m, px=18.0, amount=0.30):
        return _on(f, I.inner_glow(m, KIT))

    @staticmethod
    def bloom_soft(f, thr=0.85, sigma=26, amount=0.34):
        return _on(f, I.bloom(f, KIT, sigma, amount))

    @staticmethod
    def motion_blur(f, prev, px=11.0, S=9):
        return I.directional_blur(f, prev, KIT, scale=S)

    @staticmethod
    def mega_bolt(f, m, rng, subj_h, prev=None, **kw):
        lay, core = I.bolts(m, KIT, rng, subj_h=subj_h, prev_core=prev)
        return _on(f, lay), core

    @staticmethod
    def Debris(cx, cy, n=150, seed=9):
        return I.Debris.at(cx, cy, KIT, seed)

    @staticmethod
    def draw_debris(f, parts, age, m, S=2):
        return _on(f, I.debris(parts, age, m, scale=S))
