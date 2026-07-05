"""Tests for genlab_core.runner_healthcheck.exit_code_from_health.

Ships with PR extracting the pattern from recompute_late_rewards.py
(PR #656). The specific runner's test file
(test_recompute_late_rewards_exit_code.py) pins the end-to-end
behaviour; this file pins the shared helper in isolation.
"""

from __future__ import annotations

import pytest
from genlab_core.runner_healthcheck import (
    DEFAULT_HIGH_ERROR_EXIT_CODE,
    DEFAULT_MAX_ERROR_RATE,
    DEFAULT_MIN_TOTAL_FOR_CHECK,
    exit_code_from_health,
)


class TestDefaults:
    """The three defaults are the specific values PR #656 chose. Any
    change here needs deliberate consideration + doc update in the
    helper's docstring — pin them so a drift-by-accident is caught.
    """

    def test_min_total_default_is_3(self):
        assert DEFAULT_MIN_TOTAL_FOR_CHECK == 3

    def test_max_error_rate_default_is_50pct(self):
        assert DEFAULT_MAX_ERROR_RATE == 0.5

    def test_high_error_exit_default_is_2(self):
        assert DEFAULT_HIGH_ERROR_EXIT_CODE == 2


class TestExitCodeContract:
    """Pin the return-value semantics for the boundary cases the
    docstring calls out."""

    def test_empty_window_returns_zero(self):
        """total=0 → healthy (no work to do is not a bug)."""
        assert exit_code_from_health(total=0, errors=0) == 0

    def test_no_errors_returns_zero(self):
        """Every one of 10 items succeeded — healthy."""
        assert exit_code_from_health(total=10, errors=0) == 0

    def test_low_error_rate_returns_zero(self):
        """10% error rate — well below 50% threshold."""
        assert exit_code_from_health(total=10, errors=1) == 0

    def test_boundary_50pct_returns_zero(self):
        """Exactly at the threshold is NOT unhealthy (strict > check)."""
        assert exit_code_from_health(total=10, errors=5) == 0

    def test_just_over_50pct_returns_high_error(self):
        """51% error rate — trips the healthcheck."""
        assert exit_code_from_health(total=100, errors=51) == 2

    def test_high_error_rate_returns_high_error(self):
        """80% error rate — squarely unhealthy."""
        assert exit_code_from_health(total=10, errors=8) == 2

    def test_hundred_percent_error_returns_high_error(self):
        """100% error rate — the 7-week silent-noop condition
        from [[late-reward-sql-bug-2026-07-02]]."""
        assert exit_code_from_health(total=10, errors=10) == 2

    def test_below_min_total_never_fires(self):
        """2 items with 100% error — under min-total-3 threshold,
        returns 0 to avoid flapping on tiny samples."""
        assert exit_code_from_health(total=2, errors=2) == 0

    def test_min_total_boundary_exactly_at_threshold(self):
        """total=3 IS eligible for the check (>= not >)."""
        # 100% error rate at min-total boundary → should fire
        assert exit_code_from_health(total=3, errors=3) == 2


class TestCustomThresholds:
    """Callers can tighten or loosen thresholds per-runner."""

    def test_tightened_error_rate(self):
        """A runner that wants 10% max — 15% would trip."""
        assert exit_code_from_health(total=100, errors=15, max_error_rate=0.10) == 2

    def test_tightened_error_rate_healthy(self):
        """Same runner, 5% — healthy."""
        assert exit_code_from_health(total=100, errors=5, max_error_rate=0.10) == 0

    def test_custom_min_total(self):
        """A runner willing to alert on tiny samples — 2/2 fails
        when min_total_for_check=2."""
        assert exit_code_from_health(total=2, errors=2, min_total_for_check=2) == 2

    def test_custom_exit_code(self):
        """A runner that wants a distinct exit signal — 7 not 2."""
        assert exit_code_from_health(total=10, errors=10, high_error_exit_code=7) == 7


class TestDefensiveInputs:
    """The helper should be defensive against edge cases so callers
    don't have to guard the input themselves."""

    def test_zero_total_ignored_even_with_positive_errors(self):
        """total=0 with errors=X — the errors count is meaningless
        without a denominator; return 0 (healthy)."""
        assert exit_code_from_health(total=0, errors=5) == 0

    def test_int_arithmetic_stays_int(self):
        """Return type is always int — for use as ``sys.exit(...)``."""
        result = exit_code_from_health(total=10, errors=6)
        assert isinstance(result, int)
        assert result == 2

    @pytest.mark.parametrize(
        "total,errors,expected",
        [
            (0, 0, 0),
            (0, 1, 0),
            (3, 0, 0),
            (3, 1, 0),
            (3, 2, 2),
            (100, 50, 0),
            (100, 51, 2),
        ],
    )
    def test_parametric_boundary_sweep(self, total, errors, expected):
        assert exit_code_from_health(total=total, errors=errors) == expected
