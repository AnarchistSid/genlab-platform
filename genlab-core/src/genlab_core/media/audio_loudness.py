"""One source of truth for loudness normalisation and the true-peak gate.

Before this module there were four independent definitions of the same three
numbers, in modules that do not import each other:

* ``post_render_transform._DEFAULT_TRUE_PEAK_DBTP``  -1.5 dBTP, -14 LUFS, LRA 7
* ``validate_videos.SPEC["max_true_peak"]``          -1.0 dBTP, -14 LUFS, LRA 7
* ``motion_compositor``                              -1.5 dBTP inline, LRA 11
* ``ffmpeg_utils.build_loudnorm_filter`` defaults    -1.5 dBTP, LRA 11

The producer normalised to -1.5 and the gate demanded <= -1.0. Those agree only
because -1.5 happens to sit below -1.0; nothing enforced the direction, and the
LUFS pair agreed by coincidence. LRA did not agree at all.

Measured 2026-09-15 on a rejected movies render
(``ccbd0dd25c2c8dd7_reel_with_intro_ln.mp4``):

    loudnorm told TP=-1.5  ->  output measured -0.9 dBTP

a 0.6 dB overshoot of loudnorm's OWN target, which put the file 0.1 dB over the
gate and produced ``true_peak_over:-0.93dBTP``. The cause is the single-pass
fallback in ``post_render_transform``: when the analysis pass fails, loudnorm
runs in dynamic mode, where true-peak limiting is predictive rather than
measured. Its warning says the target "may miss by >1 LU" -- LU, not dBTP, so
nothing connected it to a true-peak gate failure.

Everything below derives from ONE number, the gate ceiling, minus margins that
are each named and measured. Do not add a fifth constant.
"""

from __future__ import annotations

# The gate. Matches how YouTube and Instagram normalise on playback, so a reel
# that clears this is not further attenuated by the platform. This is the only
# free parameter in the file; every other value derives from it.
GATE_MAX_TRUE_PEAK_DBTP = -1.0

# Integrated loudness target and loudness range. EBU R128 / streaming norm.
TARGET_LUFS = -14.0
TARGET_LRA = 7.0

# Measured 2026-09-15: how far loudnorm's output true peak exceeded the TP it
# was given. Re-measure if the ffmpeg version changes; this is an empirical
# property of loudnorm's single-pass limiter, not a spec value.
MEASURED_LOUDNORM_OVERSHOOT_DB = 0.6

# One measurement is not a distribution. This covers the spread we have not
# sampled, and the AAC re-encode that happens after normalisation.
SAFETY_MARGIN_DB = 0.4

# What loudnorm is TOLD to target, so that its overshoot still lands under the
# gate. -1.0 - 0.6 - 0.4 = -2.0 dBTP.
#
# Trade-off: this normalises transient peaks 0.5 dB lower than the previous
# -1.5. Integrated loudness is unchanged at -14 LUFS and every platform
# normalises on LUFS, not peak, so perceived loudness does not change.
NORMALISE_TARGET_TRUE_PEAK_DBTP = (
    GATE_MAX_TRUE_PEAK_DBTP - MEASURED_LOUDNORM_OVERSHOOT_DB - SAFETY_MARGIN_DB
)

# Hard ceiling for ``alimiter``, in linear amplitude. alimiter limits SAMPLE
# peaks, not true peaks, and a sample peak of X measures roughly 0.3-0.45 dB
# higher as a true peak (measured on the same file: -1.05 dB sample -> -0.9
# dBTP). Limiting at the normalisation target leaves that inter-sample headroom
# inside the safety margin above.
LIMITER_CEILING_LINEAR = 10 ** (NORMALISE_TARGET_TRUE_PEAK_DBTP / 20)


def loudnorm_filter(
    *,
    measured: dict[str, str] | None = None,
    target_lufs: float = TARGET_LUFS,
    lra: float = TARGET_LRA,
    target_tp: float = NORMALISE_TARGET_TRUE_PEAK_DBTP,
) -> str:
    """The loudnorm filter string. Two-pass when ``measured`` is supplied.

    Two-pass (linear mode) hits the true-peak target far more precisely than the
    single-pass dynamic fallback -- that difference is the whole defect this
    module exists to close -- so pass ``measured`` whenever the analysis pass
    succeeded.
    """
    spec = f"loudnorm=I={target_lufs}:LRA={lra}:TP={target_tp}"
    if measured:
        spec += (
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            ":linear=true:print_format=summary"
        )
    return spec


def limiter_filter() -> str:
    """The true-peak backstop that must follow EVERY loudnorm.

    loudnorm can raise peaks, and in single-pass mode its own TP target is
    approximate. Before 2026-09-15 this ran only in the ValidateVideos REPAIR
    path -- that is, only after the gate had already failed a file -- so the
    normal render path ended on loudnorm with nothing after it.
    """
    return f"alimiter=limit={LIMITER_CEILING_LINEAR:.4f}:level=disabled"
