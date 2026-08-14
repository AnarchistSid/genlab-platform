"""Pin the Phase 1.D `enrollment_readiness` verdict + rule-#22
protection on CalibrationStats.

The 2026-07-17 gaming revert (rule #22) proved that agreement% +
sample_count alone is dangerously insufficient. These tests pin the
extended gate:

  1. sample_count >= 30
  2. agreement_rate >= 0.90
  3. Both quadrants sampled: TP+FN > 0 AND TN+FP > 0
  4. FN rate <= 5%

Special verdicts:
  * 'ready' — all 4 checks pass
  * 'close' — 2-3 checks pass
  * 'not_ready' — 0-1 checks pass
  * 'unsampled_reject' — the exact rule #22 shape
"""
from __future__ import annotations

import pytest

from genlab_core.scheduling.calibration_logger import CalibrationStats


def _stats(*, samples=0, agree=0, tp=0, tn=0, fp=0, fn=0):
    return CalibrationStats(
        niche_id="test",
        sample_count=samples,
        agreement_count=agree,
        true_positives=tp,
        true_negatives=tn,
        false_positives=fp,
        false_negatives=fn,
    )


class TestReadyVerdict:
    def test_all_checks_pass_returns_ready(self):
        # 30 samples, 95% agree, both quadrants sampled, FN rate 0
        s = _stats(samples=30, agree=29, tp=20, tn=9, fp=1, fn=0)
        assert s.enrollment_readiness == "ready"

    def test_perfect_signal_ready(self):
        s = _stats(samples=100, agree=100, tp=70, tn=30, fp=0, fn=0)
        assert s.enrollment_readiness == "ready"


class TestUnsampledRejectSentinel:
    def test_gaming_20260717_shape_flagged(self):
        """The exact 2026-07-17 gaming revert shape: high agreement
        because operator never rejected + gate never rejected =
        TN+FP = 0. Rule #22: this must NOT show 'ready'."""
        # 40 samples, 95% agree, but ALL TP + zero TN/FP
        s = _stats(samples=40, agree=38, tp=38, tn=0, fp=0, fn=2)
        assert s.enrollment_readiness == "unsampled_reject"

    def test_low_agreement_with_empty_reject_side_is_not_ready(self):
        """If agreement% is below the 90% threshold, we return the
        generic close/not_ready verdict — 'unsampled_reject' is
        reserved for the specific misleading-high-agreement shape."""
        s = _stats(samples=40, agree=25, tp=25, tn=0, fp=0, fn=15)
        assert s.enrollment_readiness != "unsampled_reject"


class TestCloseVerdict:
    def test_two_checks_pass_returns_close(self):
        # 30 samples ✓, 88% agree ✗, both quadrants ✓, FN 0% ✓
        s = _stats(samples=30, agree=26, tp=16, tn=10, fp=2, fn=2)
        assert s.enrollment_readiness == "close"

    def test_needs_more_samples_returns_close(self):
        # 20 samples ✗, 95% agree ✓, both quadrants ✓, FN 0% ✓
        s = _stats(samples=20, agree=19, tp=13, tn=6, fp=1, fn=0)
        assert s.enrollment_readiness == "close"


class TestNotReadyVerdict:
    def test_multiple_failures_returns_not_ready(self):
        # 5 samples ✗, low agree ✗, only approve-side ✗, FN 40% ✗
        s = _stats(samples=5, agree=2, tp=2, tn=0, fp=1, fn=2)
        assert s.enrollment_readiness == "not_ready"


class TestFNRateGate:
    def test_high_fn_rate_blocks_ready(self):
        """FN > 5% means gate is over-restrictive: rejecting things
        operator wants approved. Rule #22 protects against this too."""
        # 100 samples, 91% agree, both quadrants, FN 9%
        s = _stats(samples=100, agree=91, tp=50, tn=41, fp=0, fn=9)
        # Now: 3/4 checks pass (samples ✓, agree ✓, quadrants ✓, FN ✗)
        assert s.enrollment_readiness == "close"

    def test_fn_rate_at_5pct_still_ready(self):
        """5% is the threshold — inclusive."""
        s = _stats(samples=100, agree=95, tp=50, tn=45, fp=0, fn=5)
        assert s.enrollment_readiness == "ready"


class TestReadinessReason:
    def test_ready_reason_is_positive(self):
        s = _stats(samples=30, agree=29, tp=20, tn=9, fp=1, fn=0)
        assert "safe to enroll" in s.readiness_reason.lower()

    def test_unsampled_reject_reason_mentions_rule_22(self):
        s = _stats(samples=40, agree=38, tp=38, tn=0, fp=0, fn=2)
        assert "rule #22" in s.readiness_reason.lower()

    def test_close_reason_lists_specific_failures(self):
        # Under samples
        s = _stats(samples=15, agree=13, tp=10, tn=3, fp=1, fn=1)
        assert "15 more samples" in s.readiness_reason


class TestBackwardCompat:
    def test_legacy_ready_for_enforcement_unchanged(self):
        """The legacy heuristic (samples + agreement%) stays exactly
        as-was so callers depending on it don't break."""
        # 30 samples, 90% agree — legacy says ready even in the
        # unsampled_reject case
        s = _stats(samples=30, agree=27, tp=27, tn=0, fp=0, fn=3)
        assert s.ready_for_enforcement is True
        # New gate correctly rejects
        assert s.enrollment_readiness != "ready"

    def test_zero_samples_both_verdicts_negative(self):
        s = _stats()
        assert s.ready_for_enforcement is False
        assert s.enrollment_readiness != "ready"
