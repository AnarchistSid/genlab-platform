"""min_confidence must never ratchet above what the gate can produce.

Background (2026-08-21). `compute_confusion` compares the gate's 5-check
verdict against `operator_action`. `min_confidence` is not an input to it —
the threshold decides whether to ACT on a gate approval, and cannot change
whether the gate approves. So a persistent FP > FN imbalance produces the same
positive delta every run no matter how high the threshold goes: an open loop
wearing the costume of a closed one.

The only clamp was the mathematical [0.0, 1.0], which bounds the number and
nothing else. Prod reached anime=1.0, movies=1.0, sports=0.986 against a
best-observed gate confidence of ~0.91. A threshold above the achievable
maximum does not make the gate selective, it switches it off: 701 gate-approved
blueprints across those niches in 7 days, zero auto-approved.
"""
from __future__ import annotations

import pytest

from genlab_core.scheduling.calibration_tuner import (
    _HARD_CEILING,
    ConfusionMatrix,
    suggest_min_confidence,
)

# Heavy over-approval → large positive delta, the shape that ratchets.
OVER_APPROVING = ConfusionMatrix(tp=5, tn=5, fp=40, fn=0)
WELL_CALIBRATED = ConfusionMatrix(tp=20, tn=20, fp=5, fn=5)
OVER_REJECTING = ConfusionMatrix(tp=5, tn=5, fp=0, fn=40)


class TestHardCeiling:
    def test_never_exceeds_hard_ceiling(self):
        s = suggest_min_confidence("anime", OVER_APPROVING, 0.94)
        assert s.suggested_min_confidence <= _HARD_CEILING

    def test_ceiling_is_below_one(self):
        """A threshold of 1.0 is 'disabled' spelled as a number. Disabling
        must be an explicit operator act, not a tuner side effect."""
        assert _HARD_CEILING < 1.0

    @pytest.mark.parametrize("start", [0.90, 0.95, 0.986, 1.0])
    def test_ratchet_cannot_climb_from_any_start(self, start):
        """Repeated application must converge, not integrate upward."""
        current = start
        for _ in range(50):
            current = suggest_min_confidence(
                "anime", OVER_APPROVING, current
            ).suggested_min_confidence
        assert current <= _HARD_CEILING, (
            f"50 tuning rounds from {start} reached {current} — the ratchet "
            "still has no ceiling"
        )


class TestAchievableCeiling:
    @pytest.mark.parametrize(
        "current,achievable",
        [(0.986, 0.913), (1.000, 0.906), (0.950, 0.898)],
    )
    def test_runaway_values_are_pulled_down(self, current, achievable):
        """The three prod niches, with their real observed p90s."""
        s = suggest_min_confidence(
            "n", OVER_APPROVING, current, achievable_ceiling=achievable
        )
        assert s.suggested_min_confidence == pytest.approx(achievable)
        assert s.suggested_delta < 0, "a clamp downward must report a negative delta"

    def test_clamp_is_explained_in_the_rationale(self):
        """An operator reading the suggestion must see WHY it moved."""
        s = suggest_min_confidence(
            "n", OVER_APPROVING, 1.0, achievable_ceiling=0.90
        )
        assert "CLAMPED" in s.rationale
        assert "disables auto-approval" in s.rationale

    def test_achievable_ceiling_only_lowers_never_raises(self):
        """A generous ceiling must not license exceeding the hard cap."""
        s = suggest_min_confidence(
            "n", OVER_APPROVING, 0.94, achievable_ceiling=0.99
        )
        assert s.suggested_min_confidence <= _HARD_CEILING

    def test_absent_ceiling_falls_back_to_hard_cap(self):
        """No data is the conservative direction, not a licence to ratchet."""
        s = suggest_min_confidence("n", OVER_APPROVING, 0.94, achievable_ceiling=None)
        assert s.suggested_min_confidence <= _HARD_CEILING


class TestNormalBehaviourPreserved:
    """The clamp must not break the tuner's actual job."""

    def test_well_calibrated_still_holds_steady(self):
        s = suggest_min_confidence("n", WELL_CALIBRATED, 0.85)
        assert s.suggested_delta == 0.0
        assert s.suggested_min_confidence == pytest.approx(0.85)

    def test_over_rejecting_still_lowers(self):
        """Lowering was never the broken direction — pin that it survives."""
        s = suggest_min_confidence("n", OVER_REJECTING, 0.85, achievable_ceiling=0.91)
        assert s.suggested_delta < 0
        assert s.suggested_min_confidence < 0.85

    def test_below_ceiling_raises_are_untouched(self):
        s = suggest_min_confidence("n", OVER_APPROVING, 0.80, achievable_ceiling=0.91)
        assert s.suggested_delta > 0
        assert 0.80 < s.suggested_min_confidence <= 0.91

    def test_insufficient_samples_still_holds(self):
        s = suggest_min_confidence("n", ConfusionMatrix(tp=1, tn=1, fp=0, fn=0), 0.85)
        assert s.suggested_delta == 0.0
