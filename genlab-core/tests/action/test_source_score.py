"""Pins for the ACTION source score and the derived colour seed."""

import numpy as np
import pytest
from genlab_core.action.colour_seed import derive
from genlab_core.action.source_score import MAG_CEILING, TORSO_FLOOR, SourceScore

H, W = 480, 320


def _mk(colour_regions, fg_box):
    rgb = np.zeros((H, W, 3), np.uint8)
    fg = np.zeros((H, W), np.float32)
    y0, y1, x0, x1 = fg_box
    fg[y0:y1, x0:x1] = 1.0
    rgb[y0:y1, x0:x1] = (196, 150, 128)  # skin
    for (a, b, c, d), col in colour_regions:
        rgb[a:b, c:d] = col
    return rgb, fg


# ---------------------------------------------------------------- score ----
def test_wwe_like_framing_is_not_action_eligible():
    """The clip that cost four packets must be rejected at ingest."""
    s = SourceScore(
        torso_box_frac=0.172,
        subject_area_frac=0.105,
        mag_needed=1.78,
        frames_measured=12,
        frames_at_cap=18,
    )
    assert s.action_eligible is False
    assert "not ACTION" in s.verdict


def test_ufc_like_framing_is_action_eligible():
    s = SourceScore(0.474, 0.260, 1.30, 12, 0)
    assert s.action_eligible is True
    assert s.verdict == "ACTION"
    assert s.action_source_score > 0.9


def test_score_orders_ufc_above_wwe():
    ufc = SourceScore(0.474, 0.260, 1.30, 12, 0)
    wwe = SourceScore(0.172, 0.105, 1.78, 12, 18)
    assert ufc.action_source_score > wwe.action_source_score


def test_a_clip_with_no_subject_scores_zero_and_is_rejected():
    s = SourceScore(0.0, 0.0, 3.0, 12, 12)
    assert s.action_source_score == 0.0 and s.action_eligible is False


def test_floors_are_the_documented_ones():
    assert TORSO_FLOOR == 0.30 and MAG_CEILING == 1.8


# ---------------------------------------------------------- colour seed ----
# Every fixture asserts the SELECTED FRACTION OF THE FRAME as well as the hue.
# That is the number that read 10-19% on real UFC footage while four passing
# fixtures checked only the hue -- see
# class-of-bug-fixtures-that-all-share-the-easy-case.
FRAME_FRACTION_MAX = 0.03


def _scene(garment, canvas=(120, 96, 88), light=1.0, garment_box=(300, 340, 130, 190)):
    """A body on a canvas, with a garment patch ~1.5% of the frame."""
    rgb = np.zeros((H, W, 3), np.uint8)
    fg = np.zeros((H, W), np.float32)
    rgb[:, :] = canvas
    fg[100:420, 100:220] = 1.0
    rgb[100:420, 100:220] = (196, 150, 128)  # skin
    a, b, c, d = garment_box
    rgb[a:b, c:d] = garment
    return (rgb.astype(np.float32) * light).clip(0, 255).astype(np.uint8), fg


def _selected_fraction(rgb, fg, spec):
    from genlab_core.action.colour_seed import match

    return float(match(rgb, spec, fg).mean())


@pytest.mark.parametrize(
    "name,garment,hue_lo,hue_hi",
    [
        ("bright yellow shirt", (235, 205, 45), 40, 70),
        ("bright red trunks", (200, 30, 40), -20, 20),
        ("blue glove", (40, 90, 230), 200, 260),
        ("dark maroon trunks", (92, 26, 34), -20, 20),  # the shape that broke it
        ("deep green singlet", (20, 92, 48), 120, 170),
    ],
)
def test_garment_seed_hue_and_selectivity(name, garment, hue_lo, hue_hi):
    rgb, fg = _scene(garment)
    c = derive(rgb, fg)
    assert c.has_colour_prior, name
    # only the RED band wraps; expressing it as hue_lo < 0 keeps the
    # non-wrapping bands (blue, green, yellow) on the raw 0-360 scale
    h = c.hue_deg
    if hue_lo < 0 and h > 180:
        h -= 360
    assert hue_lo <= h <= hue_hi, f"{name}: hue {c.hue_deg}"
    frac = _selected_fraction(rgb, fg, c.spec)
    assert frac <= FRAME_FRACTION_MAX, f"{name}: selects {100 * frac:.1f}% of the frame"


def test_low_light_still_names_the_garment():
    """val_min is 0.15, not a percentile -- a dim scene must still work."""
    rgb, fg = _scene((200, 30, 40), light=0.45)
    c = derive(rgb, fg)
    assert c.has_colour_prior
    assert _selected_fraction(rgb, fg, c.spec) <= FRAME_FRACTION_MAX


def test_canvas_logo_of_the_same_hue_is_excluded_by_foreground_scoping():
    """A big patch of the garment's own hue OUTSIDE the body. Scoping the match
    to the foreground is the only thing that saves this."""
    rgb, fg = _scene((200, 30, 40))
    rgb[0:90, :] = (200, 30, 40)  # same-hue logo, clear of the body
    c = derive(rgb, fg)
    assert c.has_colour_prior
    frac = _selected_fraction(rgb, fg, c.spec)
    assert frac <= FRAME_FRACTION_MAX, f"selects {100 * frac:.1f}% -- logo leaked in"
    from genlab_core.action.colour_seed import match

    unscoped = float(match(rgb, c.spec, None).mean())
    assert unscoped > frac * 3, "the fixture must actually exercise the scoping"


def test_black_trunks_fall_back_to_the_saturated_glove():
    rgb, fg = _scene((18, 18, 20))  # black trunks
    rgb[180:215, 96:130] = (240, 240, 245)  # white glove: desaturated
    rgb[180:215, 190:224] = (40, 90, 230)  # blue glove: saturated
    c = derive(rgb, fg)
    assert c.has_colour_prior
    assert 200 <= c.hue_deg <= 260, c.hue_deg


def test_bare_torso_has_no_prior_rather_than_seeding_on_skin():
    rgb, fg = _scene((18, 18, 20))
    c = derive(rgb, fg)
    assert c.has_colour_prior is False and c.source == "none" and c.spec is None


def test_seed_spec_is_usable_by_derive_clicks():
    from genlab_core.action.clicks import colour_seed

    rgb, fg = _scene((235, 205, 45))
    c = derive(rgb, fg)
    assert colour_seed(rgb, c.spec, fg).sum() > 400


# ── full-bleed floor (ACTION-UFC-03 §5b) ────────────────────────────────────
# Third time this constant bit: the UFC run reported "1.30x needed" for a crop
# that would letterbox. A score must never return a magnification the geometry
# cannot reach.


def test_floor_is_the_minimum_full_bleed_crop_not_1_3():
    from genlab_core.action.source_score import FULL_BLEED_FLOOR_1080P

    # 1920x1080 -> 1080x1920: widest filling crop is 607.5x1080, i.e. 1920/1080.
    assert abs(FULL_BLEED_FLOOR_1080P - 1920 / 1080) < 1e-9
    assert FULL_BLEED_FLOOR_1080P > 1.3, "1.3x letterboxes a 1080p source"


def test_floor_moves_with_source_height():
    """Hardcoding 1.7778 would permit an unreachable mag on a 720p source."""
    from genlab_core.action.source_score import full_bleed_floor

    assert abs(full_bleed_floor(1080) - 1.7778) < 1e-3
    assert abs(full_bleed_floor(720) - 2.6667) < 1e-3
    assert full_bleed_floor(2160) < full_bleed_floor(1080), "4K needs less mag"


def test_a_perfectly_framed_clip_can_reach_1_0_on_the_mag_term():
    """Normalising over [1.3, 3.0] while clamping at 1.778 capped this at 0.719."""
    from genlab_core.action.source_score import FULL_BLEED_FLOOR_1080P, SourceScore

    s = SourceScore(0.60, 0.0, FULL_BLEED_FLOOR_1080P, 12, 0)
    assert s.action_source_score == 1.0


def test_wwe_still_fails_on_torso_after_the_floor_change():
    """The floor RAISES WWE's score (0.454 -> 0.524) because the old number was
    partly an artefact of an unreachable normalisation range. The verdict must
    not move: torso box is the real signal and it is still under the floor."""
    from genlab_core.action.source_score import SourceScore

    wwe = SourceScore(0.172, 0.0, 1.78, 12, 18)
    assert wwe.action_eligible is False
    assert wwe.verdict.startswith("TALK/STILL")


def test_720p_source_is_mag_ineligible_because_it_cannot_fill_the_frame():
    from genlab_core.action.source_score import SourceScore, full_bleed_floor

    s = SourceScore(0.50, 0.0, full_bleed_floor(720), 12, 0, full_bleed_floor(720))
    assert s.action_eligible is False, "2.67x to fill = poor ACTION input"
