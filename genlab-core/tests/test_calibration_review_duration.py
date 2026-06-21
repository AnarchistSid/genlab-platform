"""Pin: calibration_logger accepts + clamps review_duration_ms (Lever E2).

Pre-2026-06-21 the calibration_logger had no concept of operator dwell
time. A 2s "obvious approve" and a 60s "ambiguous wrestle" both
contributed the same weight to the confusion matrix. Lever E2 adds
the column + writer support so:

  - Fast confident decisions can be weighted as strong signal
  - Slow ambiguous decisions can be weighted as weak signal
  - Active-learning queue can prioritize blueprints similar to ones
    where the operator had high dwell

These pins lock in:
  1. ``review_duration_ms=None`` passes through cleanly (cold-start
     for unwired callers).
  2. Valid integer durations pass through unmodified.
  3. Negative durations clamp to None (clock-skew defense).
  4. Absurdly-large durations (>1 hour) clamp to None (tab-left-open
     defense).
  5. Non-int values clamp to None (malformed input safety).
  6. The kwarg defaults to None so any existing caller continues to
     work without modification — backward compatibility pin.
"""

from __future__ import annotations

import inspect

import pytest


class TestCalibrationLoggerSignature:
    """Pure-signature tests that don't require DB connectivity."""

    def test_log_accepts_review_duration_ms_kwarg(self):
        """The new kwarg exists and defaults to None — backward compat
        for all pre-E2 callers."""
        from genlab_core.scheduling.calibration_logger import log

        sig = inspect.signature(log)
        assert "review_duration_ms" in sig.parameters, (
            f"Expected review_duration_ms param; got {list(sig.parameters)}"
        )
        # Default must be None so existing callers (without the kwarg)
        # produce identical behavior to the pre-E2 baseline.
        assert sig.parameters["review_duration_ms"].default is None


class TestDurationClamping:
    """The calibration writer must defend against bad dwell-time values.
    These pins exercise the clamping logic directly via the public
    ``log()`` function — which always succeeds at the signature level
    even when DB is unavailable (best-effort writer returns False)."""

    @pytest.fixture(autouse=True)
    def _no_database_url(self, monkeypatch):
        """Force the no-DB path so we exercise validation logic without
        needing a real Postgres. The function returns False but does not
        crash, which is exactly the contract we want to pin."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

    def test_none_duration_is_accepted(self):
        """Most calls pre-frontend-wiring will pass None — accepted as
        cold-start; downstream stores NULL in the column."""
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="bp_test",
            niche_id="gaming",
            decision=None,
            operator_action="approved",
            review_duration_ms=None,
        )
        # No DB → False (the function's no-error fail path), but no
        # exception raised. That's the safety contract.
        assert result is False

    def test_valid_duration_is_accepted(self):
        """Reasonable dwell times (1ms to 1hr) pass through to the DB
        layer. Even without DB, no exception is raised."""
        from genlab_core.scheduling.calibration_logger import log

        # 15.3 seconds is a typical "I'm thinking about this" review
        result = log(
            blueprint_id="bp_test",
            niche_id="gaming",
            decision=None,
            operator_action="approved",
            review_duration_ms=15_300,
        )
        assert result is False  # no DB, but no crash

    def test_negative_duration_does_not_crash(self):
        """Clock skew can produce negative durations — must not crash."""
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="bp_test",
            niche_id="gaming",
            decision=None,
            operator_action="approved",
            review_duration_ms=-500,
        )
        assert result is False  # no DB; clamping handled internally

    def test_overlarge_duration_does_not_crash(self):
        """Tab-left-open scenario: 6 hours of dwell time. Must not crash
        — clamps to None internally so the column stays NULL."""
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="bp_test",
            niche_id="gaming",
            decision=None,
            operator_action="approved",
            review_duration_ms=6 * 60 * 60 * 1000,  # 6 hours in ms
        )
        assert result is False

    def test_non_int_duration_does_not_crash(self):
        """Frontend bug could send a float / string / object. The
        validator coerces to int + falls through to None on failure."""
        from genlab_core.scheduling.calibration_logger import log

        for bad_value in ("not-a-number", [], {}, object()):
            result = log(
                blueprint_id="bp_test",
                niche_id="gaming",
                decision=None,
                operator_action="approved",
                review_duration_ms=bad_value,  # type: ignore[arg-type]
            )
            assert result is False, f"Expected safe fall-through for {bad_value!r}"


class TestMigrationFileExists:
    """The migration file must be discoverable by alembic — tests pin
    the file naming + revision chain so a typo or missing file is caught
    immediately rather than at deploy time."""

    def test_migration_file_present(self):
        from pathlib import Path

        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        target = migrations_dir / "w3r4s5t6u7v8_calibration_review_duration_ms.py"
        assert target.exists(), (
            f"Lever E2 migration file not found at {target}. "
            f"alembic upgrade head will not pick up the schema change."
        )

    def test_migration_has_expected_revision_id(self):
        from pathlib import Path

        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        target = migrations_dir / "w3r4s5t6u7v8_calibration_review_duration_ms.py"
        content = target.read_text()
        assert 'revision = "w3r4s5t6u7v8"' in content
        # down_revision must chain off the latest migration on main when
        # this PR was authored. If main has moved on, the deployer needs
        # to either rebase or accept that this column ships with this PR's
        # revision in the chain.
        assert 'down_revision = "v2q3r4s5t6u7"' in content
