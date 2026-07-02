"""Tests for scripts/recompute_late_rewards.py exit-code hardening.

2026-07-02 — see [[late-reward-sql-bug-2026-07-02]] for context on why
this runner MUST exit nonzero on high error rate. Prior state was
``return 0`` unconditionally, so systemd saw SUCCESS even when every
blueprint erred on the SQL parse and the runner produced zero
telemetry. 7 weeks of silent no-op resulted.

These tests pin the exit-code contract so it can't silently regress.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_runner():
    """Load scripts/recompute_late_rewards.py as an importable module.

    ``scripts/`` has no ``__init__.py`` (per repo convention — see
    [[systemd-scripts-invocation-gotcha]]), so we import it via
    ``importlib.util`` from its file path rather than a package import.
    """
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "recompute_late_rewards.py"
    spec = importlib.util.spec_from_file_location("recompute_late_rewards", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["recompute_late_rewards"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    """The recompute_late_rewards module loaded fresh per test."""
    return _load_runner()


class TestExitCodeContract:
    """Pin the exit-code contract for the runner:

    * 0 — completed successfully (any counts, including scanned=0)
    * 2 — high error rate (>50% of scanned when scanned >= 3)

    Exit 0 for scanned=0 is INTENTIONAL — an empty 6-8d window is
    legitimate (no publishes to re-evaluate) and shouldn't fire an
    alert.
    """

    def _run(self, runner, counters):
        """Execute main() with process_late_reward_batch mocked to return
        the given counters dict.

        NB: ``_integration_enabled`` + ``process_late_reward_batch`` are
        LAZY-IMPORTED inside main() from
        ``genlab_core.learning.late_reward`` — so we patch the SOURCE
        module, not the runner module (see CLAUDE.md "Test patterns
        for lazy-imported dependencies").
        """
        with (
            patch(
                "genlab_core.learning.late_reward._integration_enabled",
                new=lambda: False,
            ),
            patch(
                "genlab_core.learning.late_reward.process_late_reward_batch",
                return_value=counters,
            ),
        ):
            return runner.main([])

    def test_scanned_zero_returns_zero(self, runner):
        """Empty window: 0 scanned, 0 errors, exit 0."""
        assert self._run(runner, {"scanned": 0, "measured": 0, "errors": 0}) == 0

    def test_healthy_run_returns_zero(self, runner):
        """10 scanned, 9 measured, 1 error (10% error rate) — healthy."""
        assert self._run(runner, {"scanned": 10, "measured": 9, "errors": 1}) == 0

    def test_boundary_exactly_50pct_returns_zero(self, runner):
        """5 scanned, 2 errors (40%) — under 50% threshold, healthy."""
        assert self._run(runner, {"scanned": 5, "measured": 3, "errors": 2}) == 0

    def test_high_error_rate_returns_two(self, runner):
        """10 scanned, 6 errors (60%) — over 50%, high-error-rate exit."""
        assert self._run(runner, {"scanned": 10, "measured": 4, "errors": 6}) == 2

    def test_hundred_percent_error_returns_two(self, runner):
        """10 scanned, 10 errors — the 7-week silent-noop condition.

        Was the exact production state of Intervention 1 before the
        SQL fix: every blueprint errored on parse, runner exited 0,
        systemd saw SUCCESS, no alert. Now: exits 2 → OnFailure fires
        → operator sees CRITICAL alert within 24h.
        """
        assert self._run(runner, {"scanned": 10, "measured": 0, "errors": 10}) == 2

    def test_below_min_scanned_never_fires_healthcheck(self, runner):
        """2 scanned, 2 errors (100%) but under min-scanned=3 threshold.

        Avoids flapping on tiny samples — a day with 1-2 publishes in
        the 6-8d window that both error isn't necessarily a systemic
        bug. The runner still exits 0; operator sees the WARN logs
        for the individual errors.
        """
        assert self._run(runner, {"scanned": 2, "measured": 0, "errors": 2}) == 0


class TestConstantsAreTunable:
    """The three thresholds live at module scope so ops can patch
    them via a monkey-patched test or a hotfix without touching main()."""

    def test_min_scanned_threshold_exists(self, runner):
        assert hasattr(runner, "_MIN_SCANNED_FOR_HEALTHCHECK")
        assert runner._MIN_SCANNED_FOR_HEALTHCHECK == 3

    def test_max_error_rate_threshold_exists(self, runner):
        assert hasattr(runner, "_MAX_ERROR_RATE")
        assert runner._MAX_ERROR_RATE == 0.5

    def test_exit_code_constant_exists(self, runner):
        assert hasattr(runner, "_EXIT_HIGH_ERROR_RATE")
        assert runner._EXIT_HIGH_ERROR_RATE == 2
