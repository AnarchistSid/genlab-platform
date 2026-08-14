"""Pin Phase 2.F CUSUM change-point detector."""
from __future__ import annotations

import pytest

from genlab_core.monitoring.change_point import (
    ChangePoint,
    detect_change_point,
)


class TestBasicDetection:
    def test_insufficient_samples_returns_none(self):
        assert detect_change_point([0.1, 0.2, 0.3]) is None

    def test_flat_series_no_detection(self):
        """Constant series → σ=0 → detector returns None (can't
        compute z-score without dispersion)."""
        assert detect_change_point([0.3] * 30) is None

    def test_stable_noisy_series_no_detection(self):
        """Series with noise but no real shift shouldn't fire."""
        series = [0.3 + (i % 3) * 0.01 for i in range(30)]
        assert detect_change_point(series) is None

    def test_clear_upward_shift_detected(self):
        """First 7 days at 0.2, then jump to 0.6 with small noise.
        Signal is huge — CUSUM must catch this."""
        baseline = [0.2, 0.22, 0.19, 0.21, 0.2, 0.18, 0.22]
        shifted = [0.6, 0.62, 0.58, 0.61, 0.6, 0.59, 0.62,
                   0.63, 0.6, 0.58, 0.61, 0.6, 0.58, 0.62,
                   0.6, 0.61, 0.59, 0.6, 0.6, 0.61, 0.6, 0.6, 0.6]
        cp = detect_change_point(baseline + shifted)
        assert cp is not None
        assert cp.direction == "up"

    def test_clear_downward_shift_detected(self):
        """First 7 days at 0.6, then drop to 0.1."""
        baseline = [0.6, 0.62, 0.58, 0.61, 0.6, 0.59, 0.62]
        shifted = [0.1, 0.12, 0.08, 0.11, 0.1, 0.09, 0.12,
                   0.11, 0.1, 0.08, 0.12, 0.1, 0.1, 0.11,
                   0.09, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        cp = detect_change_point(baseline + shifted)
        assert cp is not None
        assert cp.direction == "down"


class TestConfidenceAndMagnitude:
    def test_confidence_in_unit_interval(self):
        baseline = [0.2, 0.22, 0.19, 0.21, 0.2, 0.18, 0.22]
        shifted = [0.6] * 23
        cp = detect_change_point(baseline + shifted)
        assert cp is not None
        assert 0.0 <= cp.confidence <= 1.0

    def test_larger_shift_higher_confidence(self):
        baseline = [0.2, 0.22, 0.19, 0.21, 0.2, 0.18, 0.22]
        small_shift = detect_change_point(baseline + [0.3] * 23)
        big_shift = detect_change_point(baseline + [1.0] * 23)
        assert small_shift is not None
        assert big_shift is not None
        assert big_shift.confidence >= small_shift.confidence

    def test_magnitude_positive(self):
        baseline = [0.2, 0.22, 0.19, 0.21, 0.2, 0.18, 0.22]
        cp = detect_change_point(baseline + [0.6] * 23)
        assert cp is not None
        assert cp.magnitude > 0


class TestFailOpen:
    def test_empty_series_returns_none(self):
        assert detect_change_point([]) is None

    def test_negative_values_dont_crash(self):
        """Reward can theoretically be negative (skip_rate penalty)."""
        series = [-0.1, -0.2, -0.1, -0.15, -0.2, -0.1, -0.15,
                  0.5, 0.6, 0.5, 0.55, 0.6, 0.5, 0.55,
                  0.6, 0.5, 0.55, 0.6, 0.5, 0.55, 0.6, 0.5, 0.5,
                  0.55, 0.6, 0.5, 0.55, 0.6, 0.5, 0.55]
        cp = detect_change_point(series)
        # Should not crash; may or may not detect depending on σ
        assert cp is None or isinstance(cp, ChangePoint)
