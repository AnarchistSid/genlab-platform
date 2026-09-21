"""Pins for the palette advisory (ANIME-13 §3).

The headline pin is the one that stops this being re-promoted to a gate: the
show's own PV frame is FURTHER from its own cover than a different show's
cover is. Any future change that makes `check` block must first make that
assertion fail.
"""

from __future__ import annotations

import numpy as np
import pytest
from genlab_core.still import palette_match as PM
from PIL import Image


def _swatch(tmp_path, name, rgb, noise=0.0, side=128):
    a = np.zeros((side, side, 3), dtype=np.float32)
    a[..., 0], a[..., 1], a[..., 2] = rgb
    if noise:
        rng = np.random.default_rng(0)
        a = np.clip(a + rng.normal(0, noise, a.shape), 0, 255)
    p = tmp_path / f"{name}.png"
    Image.fromarray(a.astype("uint8")).save(p)
    return p


class TestMetric:
    def test_identical_images_are_zero_distance(self, tmp_path):
        a = _swatch(tmp_path, "a", (200, 60, 120))
        assert PM.distance(a, a) == pytest.approx(0.0, abs=1e-9)

    def test_opposite_hues_are_far_apart(self, tmp_path):
        warm = _swatch(tmp_path, "warm", (220, 90, 40))
        cool = _swatch(tmp_path, "cool", (40, 90, 220))
        assert PM.distance(warm, cool) > 0.9

    def test_brightness_alone_moves_it_little(self, tmp_path):
        """A night crop of a show is still that show's palette. A straight RGB
        histogram fails this; the saturation-weighted hue histogram is chosen
        for it."""
        bright = _swatch(tmp_path, "bright", (220, 110, 60))
        dark = _swatch(tmp_path, "dark", (110, 55, 30))
        assert PM.distance(bright, dark) < 0.25

    def test_it_is_symmetric(self, tmp_path):
        a = _swatch(tmp_path, "a", (200, 60, 120))
        b = _swatch(tmp_path, "b", (60, 200, 120))
        assert PM.distance(a, b) == pytest.approx(PM.distance(b, a), abs=1e-9)


class TestItIsAdvisoryNotAGate:
    def test_check_never_raises_on_a_miss(self, tmp_path):
        a = _swatch(tmp_path, "a", (220, 90, 40))
        b = _swatch(tmp_path, "b", (40, 90, 220))
        d = PM.check(a, b, threshold=0.10)
        assert d.passes is False, "fixture must exercise the miss path"

    def test_no_render_module_branches_on_the_distance(self):
        """The rule that survived the measurement is structural — reference or
        no generated still — and needs no threshold. If a caller starts
        branching on `passes`, the disqualified gate is back."""
        import pathlib

        root = pathlib.Path(PM.__file__).parent
        offenders = []
        for f in root.glob("*.py"):
            if f.name == "palette_match.py":
                continue
            src = f.read_text()
            if ".passes" in src or "advisory_threshold(" in src:
                offenders.append(f.name)
        assert offenders == [], offenders

    def test_the_disqualifying_controls_are_recorded_with_their_numbers(self):
        doc = PM.__doc__ or ""
        for token in ("0.8583", "0.8698", "87.2%", "ADVISORY"):
            assert token in doc, f"{token} missing — the evidence must travel with the code"


def test_calibration_refuses_to_invent_a_threshold():
    with pytest.raises(ValueError):
        PM.advisory_threshold([])
