"""Pin Phase 5.A calibration tuner logic:

  * Rule #22: pinned operator_action string set matches DB truth
  * ConfusionMatrix arithmetic (n, agreement_pct, imbalance)
  * compute_confusion routes each action → correct cell
  * Rows with unknown operator_action skipped (not double-counted)
  * suggest_min_confidence: insufficient samples → delta=0
  * suggest_min_confidence: near-zero imbalance → delta=0
  * suggest_min_confidence: FP-heavy → positive delta (raise threshold)
  * suggest_min_confidence: FN-heavy → negative delta (lower threshold)
  * within_auto_apply True only when 0 < |delta| <= 0.05
  * suggested_min_confidence clamped [0, 1]
"""
from __future__ import annotations

import pytest

from genlab_core.scheduling.calibration_tuner import (
    AUTO_APPLY_MAX_DELTA,
    ConfusionMatrix,
    TuningSuggestion,
    _NEGATIVE_OPERATOR_ACTIONS,
    _POSITIVE_OPERATOR_ACTIONS,
    compute_confusion,
    suggest_min_confidence,
)


class TestRule22Pin:
    def test_positive_set_matches_db_truth(self):
        """Prod DB has 'approved' — NOT 'approve'. Any drift here
        is the exact class-of-bug that caused the 2026-07-17
        misenrollment incident."""
        assert _POSITIVE_OPERATOR_ACTIONS == {"approved"}

    def test_negative_set_matches_db_truth(self):
        """Prod DB has 'rejected' / 'revised' / 'skipped'."""
        assert _NEGATIVE_OPERATOR_ACTIONS == {"rejected", "revised", "skipped"}

    def test_pinned_sets_are_disjoint(self):
        overlap = _POSITIVE_OPERATOR_ACTIONS & _NEGATIVE_OPERATOR_ACTIONS
        assert overlap == set()


class TestConfusionMatrix:
    def test_n_sums_all_cells(self):
        m = ConfusionMatrix(tp=10, tn=20, fp=5, fn=3)
        assert m.n == 38

    def test_agreement_pct_math(self):
        m = ConfusionMatrix(tp=8, tn=2, fp=1, fn=1)  # 10/12 = 83.3%
        assert m.agreement_pct == pytest.approx(83.333, abs=0.01)

    def test_empty_matrix_safe(self):
        m = ConfusionMatrix(tp=0, tn=0, fp=0, fn=0)
        assert m.n == 0
        assert m.agreement_pct == 0.0
        assert m.imbalance == 0.0

    def test_imbalance_positive_when_fp_heavy(self):
        m = ConfusionMatrix(tp=5, tn=5, fp=8, fn=2)  # 20 total, (8-2)/20 = 0.3
        assert m.imbalance == pytest.approx(0.3)

    def test_imbalance_negative_when_fn_heavy(self):
        m = ConfusionMatrix(tp=5, tn=5, fp=2, fn=8)
        assert m.imbalance == pytest.approx(-0.3)


class TestComputeConfusion:
    def test_correct_routing(self):
        rows = [
            {"gate_approved": True, "operator_action": "approved"},   # TP
            {"gate_approved": True, "operator_action": "approved"},   # TP
            {"gate_approved": False, "operator_action": "rejected"},  # TN
            {"gate_approved": True, "operator_action": "rejected"},   # FP
            {"gate_approved": False, "operator_action": "approved"},  # FN
        ]
        m = compute_confusion(rows)
        assert m.tp == 2
        assert m.tn == 1
        assert m.fp == 1
        assert m.fn == 1

    def test_revised_counted_as_negative(self):
        rows = [
            {"gate_approved": True, "operator_action": "revised"},   # FP
            {"gate_approved": False, "operator_action": "revised"},  # TN
        ]
        m = compute_confusion(rows)
        assert m.fp == 1
        assert m.tn == 1

    def test_skipped_counted_as_negative(self):
        rows = [{"gate_approved": True, "operator_action": "skipped"}]
        assert compute_confusion(rows).fp == 1

    def test_unknown_action_skipped(self):
        """Value drift protection — a row with 'approve' (missing d)
        must NOT be counted. Prior code paths silently zeroed."""
        rows = [
            {"gate_approved": True, "operator_action": "approve"},  # NOT 'approved'
            {"gate_approved": True, "operator_action": "unknown"},
            {"gate_approved": True, "operator_action": None},
        ]
        m = compute_confusion(rows)
        assert m.n == 0

    def test_null_gate_skipped(self):
        rows = [{"gate_approved": None, "operator_action": "approved"}]
        assert compute_confusion(rows).n == 0

    def test_empty_rows_returns_zero_matrix(self):
        m = compute_confusion([])
        assert m.n == 0


class TestSuggestMinConfidence:
    def _cm(self, tp: int, tn: int, fp: int, fn: int) -> ConfusionMatrix:
        return ConfusionMatrix(tp=tp, tn=tn, fp=fp, fn=fn)

    def test_insufficient_samples_returns_zero_delta(self):
        # Floor lowered 2026-08-15 to 5. Use n=4 to stay below.
        s = suggest_min_confidence("gaming", self._cm(1, 1, 1, 1), 0.85)
        assert s.suggested_delta == 0.0
        assert "insufficient samples" in s.rationale
        assert s.within_auto_apply is False

    def test_well_calibrated_returns_zero_delta(self):
        # 100 samples, imbalance 0 (fp=fn)
        s = suggest_min_confidence("gaming", self._cm(40, 40, 10, 10), 0.85)
        assert s.suggested_delta == 0.0
        assert "well-calibrated" in s.rationale

    def test_fp_heavy_suggests_positive_delta(self):
        # 100 samples, 20 FP, 2 FN → imbalance +0.18, delta +0.018
        s = suggest_min_confidence("gaming", self._cm(40, 38, 20, 2), 0.85)
        assert s.suggested_delta > 0
        assert "raise" in s.rationale
        assert "FP > FN" in s.rationale

    def test_fn_heavy_suggests_negative_delta(self):
        s = suggest_min_confidence("gaming", self._cm(40, 38, 2, 20), 0.85)
        assert s.suggested_delta < 0
        assert "lower" in s.rationale
        assert "FN > FP" in s.rationale

    def test_within_auto_apply_when_small_delta(self):
        # imbalance +0.10 → delta +0.010 (within [-0.05, 0.05])
        s = suggest_min_confidence("gaming", self._cm(35, 40, 15, 10), 0.85)
        assert 0 < s.suggested_delta <= AUTO_APPLY_MAX_DELTA
        assert s.within_auto_apply is True

    def test_small_sample_fp_heavy_suggests_but_beyond_auto_apply(self):
        """Anime-shaped case that motivated the 2026-08-15 floor drop.
        5 outcome rows, all gate-approved (source='outcome' semantic:
        gate says approve, outcome says whether it was actually good).
        2 outcomes good (TP=2), 3 outcomes bad (FP=3), no rejections
        from the gate so FN=TN=0. imbalance = (3-0)/5 = 0.60 →
        delta = +0.06 which EXCEEDS AUTO_APPLY_MAX_DELTA=0.05, so it
        becomes a manual suggestion the operator eyeballs rather than
        silent auto-apply. Small-sample noise is bounded by the cap."""
        s = suggest_min_confidence("anime", self._cm(2, 0, 3, 0), 0.85)
        assert s.suggested_delta > 0  # FP-heavy → raise threshold
        assert abs(s.suggested_delta) > AUTO_APPLY_MAX_DELTA
        assert s.within_auto_apply is False
        assert "FP > FN" in s.rationale

    def test_outside_auto_apply_when_large_delta(self):
        # Extreme skew: 60 FP vs 0 FN out of 100 → imbalance +0.60, delta +0.06
        s = suggest_min_confidence("gaming", self._cm(30, 10, 60, 0), 0.85)
        assert abs(s.suggested_delta) > AUTO_APPLY_MAX_DELTA
        assert s.within_auto_apply is False

    def test_suggested_min_confidence_clamped_upper(self):
        # Current 0.98 + big raise → clamp to 1.0
        s = suggest_min_confidence(
            "gaming", self._cm(30, 10, 60, 0), current_min_confidence=0.98,
        )
        assert s.suggested_min_confidence == 1.0

    def test_suggested_min_confidence_clamped_lower(self):
        # Current 0.05 + big lower → clamp to 0.0
        s = suggest_min_confidence(
            "gaming", self._cm(0, 60, 0, 40), current_min_confidence=0.02,
        )
        assert s.suggested_min_confidence >= 0.0

    def test_zero_delta_not_within_auto_apply(self):
        """When delta is exactly 0 (well-calibrated), auto-apply
        is False because there's nothing to apply."""
        s = suggest_min_confidence("gaming", self._cm(40, 40, 10, 10), 0.85)
        assert s.within_auto_apply is False

    def test_rationale_includes_full_confusion_matrix(self):
        """Rule #22 discipline: the rationale must show TP/TN/FP/FN,
        not just agreement %. Pin string prevents drift toward
        agreement-only summary."""
        s = suggest_min_confidence("gaming", self._cm(40, 38, 20, 2), 0.85)
        assert "TP=" in s.rationale
        assert "FP=" in s.rationale
        assert "FN=" in s.rationale


class TestConstants:
    def test_auto_apply_max_matches_roadmap(self):
        """Roadmap: auto-apply if delta in [-0.05, +0.05]."""
        assert AUTO_APPLY_MAX_DELTA == 0.05
