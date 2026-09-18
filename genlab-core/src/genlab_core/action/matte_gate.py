"""The standing acceptance gate for a matte backend.

WHY THE PIXEL-OVERLAP GATE WAS RETIRED
--------------------------------------
RENDER-01 gated a ported matte backend on `IoU >= 0.98 on >= 90/96 frames`
against the mattes archived with ACTION-UFC-05. The port reached 55/96 and the
gate failed for four rounds while the mattes themselves got steadily better.

The gate was measuring the wrong artifact. Those archived mattes are the output
of `w2_sam2.py` + `w2_crop.py` — scripts that no longer run and are not what was
approved. **The approved thing was a REEL.** A pixel-overlap score against a
superseded intermediate answers "did you reproduce that script", when the
question is "does this produce an acceptable reel".

What the numbers actually said, once the native-frame path was in:

    area band mean   0.3084   against the archive's 0.3092   (0.3% apart)
    area band        .2254-.4312  against  .2265-.4272
    empty frames     0
    IoU mean         0.9585,  55/96 at >= 0.98

Two matte sets that agree on size and shape to a fraction of a percent, and
disagree on exact pixel membership. Rendered through grade + aura + bloom and
judged side by side on 2026-09-18, they were **indistinguishable**. So the port
is the oracle now, and the gate below is what a matte backend has to clear.

The residual divergence is named rather than chased: it begins at annotation
frames, i.e. SEED POSITION — the superseded script clicked from
`garment_groups` + `_click(g)`, the port clicks from `derive_clicks`. Different
click positions give different SAM2 masks and a different propagation. Recorded
so the next person does not re-hunt it.

WHY THESE THREE CRITERIA
------------------------
* **Area band, not overlap.** Size and shape of the subject is what every
  downstream effect reads — the aura band, the limb weighting, the debris mask
  all key off the silhouette's extent. Exact pixel membership at the boundary is
  below the feather.
* **Zero empty frames.** An empty matte is not a small error, it is a frame with
  no subject: the effects have nothing to attach to and the shot reads as a
  glitch. This one is absolute.
* **A judged A/B on the first clip of any new backend.** The criteria above are
  necessary, not sufficient. A backend could hold the area band while tracking
  the wrong fighter. Somebody looks, once, per backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Tolerance on the area band, as a fraction. +/-2% of the reference at the
#: mean and at both ends. The port sits at 0.3% on the mean; 2% leaves room for
#: a different model without admitting a different subject.
AREA_TOLERANCE = 0.02


@dataclass
class MatteGateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    def __str__(self) -> str:
        head = "PASS" if self.passed else "FAIL"
        return f"{head}: " + ("; ".join(self.reasons) if self.reasons else "all criteria met")


def _band(masks: dict[int, np.ndarray]) -> tuple[float, float, float]:
    a = np.array([float((np.asarray(m) > 0.5).mean()) for m in masks.values()])
    return float(a.min()), float(a.mean()), float(a.max())


def check(
    masks: dict[int, np.ndarray],
    reference_band: tuple[float, float, float],
    *,
    expected_frames: int,
    tolerance: float = AREA_TOLERANCE,
    judged_ab: bool | None = None,
) -> MatteGateResult:
    """Gate a matte backend's output against a reference's AREA BAND.

    `reference_band` is (min, mean, max) of the reference's per-frame subject
    area as a fraction of frame. `judged_ab` is the operator's verdict on the
    side-by-side; None means "not yet judged", which fails a NEW backend and is
    the point of the criterion.
    """
    reasons: list[str] = []
    if not masks:
        return MatteGateResult(False, ["no mattes produced"], {})

    lo, mean, hi = _band(masks)
    r_lo, r_mean, r_hi = reference_band
    measured = {
        "frames": len(masks),
        "area_min": round(lo, 4),
        "area_mean": round(mean, 4),
        "area_max": round(hi, 4),
        "reference_band": [round(v, 4) for v in reference_band],
    }

    if len(masks) != expected_frames:
        reasons.append(f"{len(masks)} mattes, expected {expected_frames}")

    empty = [f for f, m in masks.items() if int((np.asarray(m) > 0.5).sum()) == 0]
    measured["empty_frames"] = len(empty)
    if empty:
        # Absolute. An empty matte is a frame with no subject for the effects to
        # attach to, and it reads as a glitch rather than as a small error.
        reasons.append(f"{len(empty)} empty matte frame(s): {empty[:6]}")

    for label, got, want in (("min", lo, r_lo), ("mean", mean, r_mean), ("max", hi, r_hi)):
        if want and abs(got - want) / want > tolerance:
            reasons.append(
                f"area {label} {got:.4f} is {abs(got - want) / want:.1%} from the "
                f"reference's {want:.4f} (tolerance {tolerance:.0%})"
            )

    if judged_ab is None:
        reasons.append(
            "no judged A/B for this backend — the area band is necessary, not sufficient"
        )
    elif judged_ab is False:
        reasons.append("judged A/B rejected")

    return MatteGateResult(not reasons, reasons, measured)
