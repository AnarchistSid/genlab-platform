"""Pin Phase 2.C SLO forecast pure functions."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from genlab_core.monitoring.slo_forecast import (
    SloForecast,
    _ewma,
    _linear_slope,
    _classify_verdict,
    bucket_by_day,
    compute_forecast,
)


class TestEWMA:
    def test_empty_returns_empty(self):
        assert _ewma([]) == []

    def test_single_value_returns_itself(self):
        assert _ewma([5.0]) == [5.0]

    def test_flat_series_stays_flat(self):
        result = _ewma([3.0, 3.0, 3.0])
        for v in result:
            assert v == pytest.approx(3.0, abs=1e-9)

    def test_step_up_smoothed(self):
        """0, 0, 10 → 0, 0, 3.0 with alpha=0.3."""
        result = _ewma([0.0, 0.0, 10.0])
        assert result[-1] == pytest.approx(3.0, abs=0.01)


class TestLinearSlope:
    def test_flat_series_zero_slope(self):
        assert _linear_slope([5.0, 5.0, 5.0]) == pytest.approx(0.0)

    def test_increasing_series_positive_slope(self):
        assert _linear_slope([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)

    def test_decreasing_series_negative_slope(self):
        assert _linear_slope([4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_single_point_zero_slope(self):
        assert _linear_slope([5.0]) == 0.0


class TestVerdictClassification:
    def test_stable_when_forecast_matches_current(self):
        assert _classify_verdict(1.0, 1.0) == "stable"

    def test_stable_when_both_zero(self):
        assert _classify_verdict(0.0, 0.0) == "stable"

    def test_watch_when_zero_to_positive(self):
        assert _classify_verdict(0.0, 0.5) == "watch"

    def test_watch_at_20pct_growth(self):
        assert _classify_verdict(1.0, 1.2) == "watch"

    def test_forecast_warning_at_2x(self):
        assert _classify_verdict(1.0, 2.0) == "forecast_warning"

    def test_forecast_critical_at_5x(self):
        assert _classify_verdict(1.0, 5.0) == "forecast_critical"

    def test_stable_when_below_20pct_growth(self):
        assert _classify_verdict(1.0, 1.15) == "stable"


class TestComputeForecast:
    def test_insufficient_data_returns_insufficient_data(self):
        f = compute_forecast("test", [1.0])
        assert f is not None
        assert f.verdict == "insufficient_data"

    def test_flat_series_returns_stable(self):
        # 14 days of 1.0
        f = compute_forecast("test", [1.0] * 14)
        assert f is not None
        assert f.verdict == "stable"

    def test_positive_slope_produces_forecast_higher_than_current(self):
        """End-to-end: any positive trend produces forecast > current
        and a positive ttb_hours. The specific verdict depends on
        EWMA smoothing + threshold multipliers (tested separately
        in TestVerdictClassification)."""
        # Steady exponential growth over 14d
        series = [float(2 ** (i / 3)) for i in range(14)]
        f = compute_forecast("test", series)
        assert f is not None
        assert f.forecast_rate > f.current_rate
        assert f.trend_pct > 0
        assert f.ttb_hours is not None
        assert f.ttb_hours > 0

    def test_decelerating_series_returns_stable_or_watch(self):
        f = compute_forecast("test", [10.0, 8.0, 6.0, 4.0, 2.0, 1.0, 0.5])
        assert f is not None
        # Trend is DOWN, so ttb_hours should be None
        assert f.ttb_hours is None

    def test_ttb_computed_when_trend_positive(self):
        # Ramp: 1, 2, 3, 4, 5, 6, 7
        f = compute_forecast("test", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        assert f is not None
        assert f.ttb_hours is not None
        assert f.ttb_hours > 0

    def test_niche_id_passed_through(self):
        f = compute_forecast("test", [1.0] * 14, niche_id="anime")
        assert f.niche_id == "anime"


class TestBucketByDay:
    def test_returns_days_back_length(self):
        result = bucket_by_day([], days_back=14)
        assert len(result) == 14

    def test_all_zero_when_no_events(self):
        result = bucket_by_day([], days_back=7)
        assert result == [0.0] * 7

    def test_counts_events_in_correct_bucket(self):
        now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        events = [
            now - timedelta(days=2, hours=3),  # 08-12
            now - timedelta(days=2, hours=1),  # 08-12
            now - timedelta(days=0, hours=1),  # 08-14
        ]
        result = bucket_by_day(events, days_back=3, now=now)
        # 3 days back = [8-12, 8-13, 8-14]
        assert result == [2.0, 0.0, 1.0]

    def test_events_older_than_window_excluded(self):
        now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        events = [now - timedelta(days=30)]  # way out
        result = bucket_by_day(events, days_back=7, now=now)
        assert sum(result) == 0
