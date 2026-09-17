"""Pins for the SAM2 click derivation.

Three fixtures, one per situation the function got wrong in production:
  two people separated  -> positive on the subject, negative on the other
  two people clinched   -> same, and the negative must NOT be inside the
                           subject's own component
  one person alone      -> NO negative at all

This function failed three times by being re-described at each call site. These
pins exist so the next change has to keep all three true at once.
"""

import numpy as np
from genlab_core.action.clicks import ClickSet, derive_clicks

YELLOW = {"hue_deg": 52.0, "hue_tol": 14.0, "sat_min": 0.45, "val_min": 0.15}
MIN_PX = 400
H, W = 240, 160


def _canvas():
    return np.zeros((H, W, 3), np.uint8), np.zeros((H, W), np.float32)


def _person(rgb, fg, x0, x1, y0, y1, colour):
    rgb[y0:y1, x0:x1] = colour
    fg[y0:y1, x0:x1] = 1.0


SUBJECT = (230, 200, 40)  # the signature colour
OTHER = (70, 60, 58)  # a different person, no signature colour


def test_two_people_separated_negative_on_the_other():
    rgb, fg = _canvas()
    _person(rgb, fg, 10, 50, 40, 160, SUBJECT)
    _person(rgb, fg, 100, 145, 40, 160, OTHER)
    c = derive_clicks(rgb, fg, YELLOW, MIN_PX)
    assert isinstance(c, ClickSet) and c.has_negative
    assert c.positive[0] < 60, "positive must sit on the subject"
    assert c.negative[0] > 90, "negative must sit on the other person"


def test_two_people_clinched_negative_not_inside_the_subject():
    """Touching bodies merge into ONE foreground component. The negative must
    then be refused rather than placed on the subject -- placing it there is
    exactly what told SAM2 'the subject is not the subject'."""
    rgb, fg = _canvas()
    _person(rgb, fg, 30, 80, 40, 170, SUBJECT)
    _person(rgb, fg, 80, 130, 40, 170, OTHER)  # adjacent: one component
    c = derive_clicks(rgb, fg, YELLOW, MIN_PX)
    assert isinstance(c, ClickSet)
    if c.has_negative:
        seed_x0, seed_x1 = 30, 80
        assert not (seed_x0 <= c.negative[0] < seed_x1), (
            "negative landed inside the subject's own body"
        )


def test_one_person_alone_has_no_negative():
    rgb, fg = _canvas()
    _person(rgb, fg, 50, 110, 30, 200, SUBJECT)
    c = derive_clicks(rgb, fg, YELLOW, MIN_PX)
    assert isinstance(c, ClickSet)
    assert c.negative is None, "a lone subject must get ZERO negative clicks"


def test_subject_colour_absent_returns_none():
    rgb, fg = _canvas()
    _person(rgb, fg, 50, 110, 30, 200, OTHER)
    assert derive_clicks(rgb, fg, YELLOW, MIN_PX) is None


def test_thresholds_come_from_config_not_literals():
    """A niche whose subject is not yellow must still work."""
    rgb, fg = _canvas()
    blue = {"hue_deg": 225.0, "hue_tol": 14.0, "sat_min": 0.45, "val_min": 0.15}
    _person(rgb, fg, 20, 70, 40, 160, (40, 60, 220))
    _person(rgb, fg, 100, 145, 40, 160, OTHER)
    c = derive_clicks(rgb, fg, blue, MIN_PX)
    assert c is not None and c.positive[0] < 90
