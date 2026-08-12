"""Pin the engagement-question A/B analysis script.

Focus: math + verdict logic + query-shape structure.
Full end-to-end DB integration test would need a live Postgres
fixture; keep this to unit-level.
"""

from __future__ import annotations

import pytest

from genlab_core.tools.analyze_engagement_question_ab import (
    BucketStats,
    _verdict,
    compute_bucket_stats,
    welch_t_test,
)


class TestComputeBucketStats:
    def test_empty_returns_zero(self):
        s = compute_bucket_stats([])
        assert s.n == 0
        assert s.mean == 0.0
        assert s.variance == 0.0

    def test_single_row_variance_zero(self):
        s = compute_bucket_stats([{"comments": 5}])
        assert s.n == 1
        assert s.mean == 5.0
        assert s.variance == 0.0

    def test_multi_row_mean_and_variance(self):
        s = compute_bucket_stats([
            {"comments": 2}, {"comments": 4}, {"comments": 6},
        ])
        assert s.n == 3
        assert s.mean == 4.0
        # sample variance (n-1 divisor)
        # sum of squares: (2-4)^2 + (4-4)^2 + (6-4)^2 = 4+0+4 = 8
        # variance = 8 / 2 = 4.0
        assert s.variance == 4.0

    def test_null_comments_treated_as_zero(self):
        s = compute_bucket_stats([
            {"comments": None}, {"comments": 4}, {"comments": None},
        ])
        assert s.n == 3
        assert s.mean == pytest.approx(4 / 3)


class TestWelchTTest:
    def test_identical_bucket_stats_zero_t(self):
        s = BucketStats(n=100, mean=5.0, variance=2.0)
        t, p = welch_t_test(s, s)
        assert t == 0.0
        assert p == pytest.approx(1.0, abs=0.01)

    def test_clear_difference_low_p(self):
        """Large mean difference + reasonable n -> p should be tiny."""
        a = BucketStats(n=100, mean=10.0, variance=4.0)
        b = BucketStats(n=100, mean=5.0, variance=4.0)
        t, p = welch_t_test(a, b)
        assert t > 10  # very high t stat
        assert p < 0.001

    def test_marginal_difference_moderate_p(self):
        a = BucketStats(n=50, mean=5.5, variance=4.0)
        b = BucketStats(n=50, mean=5.0, variance=4.0)
        _, p = welch_t_test(a, b)
        assert p > 0.05  # not significant at 95%

    def test_insufficient_samples_returns_p_one(self):
        a = BucketStats(n=1, mean=5.0, variance=0.0)
        b = BucketStats(n=100, mean=3.0, variance=2.0)
        t, p = welch_t_test(a, b)
        assert t == 0.0
        assert p == 1.0

    def test_zero_variance_returns_p_one(self):
        a = BucketStats(n=100, mean=5.0, variance=0.0)
        b = BucketStats(n=100, mean=3.0, variance=0.0)
        t, p = welch_t_test(a, b)
        assert p == 1.0


class TestVerdict:
    def _partial(self, wn, wo_n, w_mean, wo_mean, p):
        with_q = BucketStats(n=wn, mean=w_mean, variance=1.0)
        without_q = BucketStats(n=wo_n, mean=wo_mean, variance=1.0)
        lift = (w_mean - wo_mean) / wo_mean if wo_mean > 0 else 0
        return {"with_q": with_q, "without_q": without_q, "lift": lift, "p_value": p}

    def test_insufficient_data_flag(self):
        v = _verdict = None  # noqa: F841 (rename shadow)
        from genlab_core.tools.analyze_engagement_question_ab import _verdict
        p = self._partial(10, 20, 5.0, 4.0, 0.02)
        assert "insufficient_data" in _verdict(p)

    def test_not_significant_flag(self):
        from genlab_core.tools.analyze_engagement_question_ab import _verdict
        p = self._partial(100, 100, 5.5, 5.0, 0.15)
        assert "not_significant" in _verdict(p)

    def test_question_wins_recommendation(self):
        from genlab_core.tools.analyze_engagement_question_ab import _verdict
        # 30% lift, p=0.001
        p = self._partial(100, 100, 6.5, 5.0, 0.001)
        v = _verdict(p)
        assert "question_wins" in v
        assert "ROLLOUT_PCT=100" in v

    def test_question_hurts_recommendation(self):
        from genlab_core.tools.analyze_engagement_question_ab import _verdict
        # -30% lift, p=0.001
        p = self._partial(100, 100, 3.5, 5.0, 0.001)
        v = _verdict(p)
        assert "question_hurts" in v
        assert "ROLLOUT_PCT=0" in v

    def test_marginal_lift_says_keep_running(self):
        from genlab_core.tools.analyze_engagement_question_ab import _verdict
        # +10% lift, statistically significant but not big enough
        p = self._partial(100, 100, 5.5, 5.0, 0.02)
        v = _verdict(p)
        assert "marginal" in v
        assert "keep_running" in v


class TestScriptStructure:
    """Pin the script's public surface to catch accidental removal."""

    def test_module_exports_expected_names(self):
        import genlab_core.tools.analyze_engagement_question_ab as mod
        assert hasattr(mod, "main")
        assert hasattr(mod, "analyze_one")
        assert hasattr(mod, "query_ab_data")
        assert hasattr(mod, "compute_bucket_stats")
        assert hasattr(mod, "welch_t_test")

    def test_analyze_one_returns_ab_result(self):
        """The analyze_one function must accept a conn + kwargs and
        return an ABResult even if the DB errors (fail-open)."""
        from unittest.mock import MagicMock

        from genlab_core.tools.analyze_engagement_question_ab import analyze_one

        conn = MagicMock()
        # Simulate DB error inside query_ab_data
        conn.execute.side_effect = RuntimeError("db down")

        result = analyze_one(
            conn, niche="gaming", platform="youtube", window_days=14,
        )
        # Should not raise; returns ABResult with zeroed stats
        assert result.niche == "gaming"
        assert result.platform == "youtube"
        assert result.with_q.n == 0
        assert result.without_q.n == 0
        assert "insufficient_data" in result.verdict
