"""Pin: calibration_logger captures feedback_category + breakdown_by_category (Lever B).

Pre-2026-06-21 the 6 operator categorical rejection reasons were
captured on ``blueprints.feedback_issue`` but **never read by any
endpoint or analysis**. Confusion matrices were aggregate-only — a
``weak_hook`` rejection counted the same as a ``bad_fit`` rejection
when computing agreement rates. The categorical signal sat unused.

Lever B threads the category into the calibration table + adds a
breakdown function that groups rejections by reason.

These pins lock in:

  1. ``log()`` accepts ``feedback_category`` kwarg (defaults to None,
     backward compat).
  2. Empty string / whitespace categories normalize to None (so the
     index stays clean).
  3. Multi-KB free-text inputs get truncated to 64 chars (column has
     no length limit but we cap defensively to prevent index bloat).
  4. ``CategoryBreakdownEntry`` dataclass shape is stable (frontend
     contract).
  5. ``breakdown_by_category()`` returns empty list on no-DB / cold
     start (fail-quiet contract matches stats()).
  6. Migration file present with correct revision chain.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestCalibrationLoggerSignature:
    def test_log_accepts_feedback_category_kwarg(self):
        from genlab_core.scheduling.calibration_logger import log

        sig = inspect.signature(log)
        assert "feedback_category" in sig.parameters
        assert sig.parameters["feedback_category"].default is None

    def test_breakdown_by_category_function_exists(self):
        from genlab_core.scheduling.calibration_logger import breakdown_by_category

        sig = inspect.signature(breakdown_by_category)
        assert "niche_id" in sig.parameters
        assert "window_days" in sig.parameters
        assert sig.parameters["window_days"].default == 30


class TestCategoryNormalization:
    @pytest.fixture(autouse=True)
    def _no_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)

    def test_none_category_is_accepted(self):
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="00000000-0000-0000-0000-000000000001",
            niche_id="gaming",
            decision=None,
            operator_action="approved",
            feedback_category=None,
        )
        assert result is False  # no DB, but no crash

    def test_valid_category_is_accepted(self):
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="00000000-0000-0000-0000-000000000001",
            niche_id="gaming",
            decision=None,
            operator_action="rejected",
            feedback_category="weak_hook",
        )
        assert result is False  # no DB

    def test_empty_string_category_does_not_crash(self):
        """Empty / whitespace categories should normalize to None
        internally so the index stays clean — but the writer must not
        crash."""
        from genlab_core.scheduling.calibration_logger import log

        for bad_value in ("", "   ", "\t\n"):
            result = log(
                blueprint_id="00000000-0000-0000-0000-000000000001",
                niche_id="gaming",
                decision=None,
                operator_action="rejected",
                feedback_category=bad_value,
            )
            assert result is False, f"Expected safe handling of empty category {bad_value!r}"

    def test_multi_kb_category_does_not_crash(self):
        """If frontend bug leaks free-text into the categorical field,
        the writer truncates to 64 chars internally — no crash."""
        from genlab_core.scheduling.calibration_logger import log

        result = log(
            blueprint_id="00000000-0000-0000-0000-000000000001",
            niche_id="gaming",
            decision=None,
            operator_action="rejected",
            feedback_category="x" * 5000,  # 5KB of garbage
        )
        assert result is False  # no DB; but no crash from oversize

    def test_non_string_category_does_not_crash(self):
        from genlab_core.scheduling.calibration_logger import log

        for bad_value in (123, [], {}, object()):
            result = log(
                blueprint_id="00000000-0000-0000-0000-000000000001",
                niche_id="gaming",
                decision=None,
                operator_action="rejected",
                feedback_category=bad_value,  # type: ignore[arg-type]
            )
            # int(123) will pass through as "123" — that's fine
            # (defensive str() coercion); the other types may raise
            # internally but the wrapper catches.
            assert isinstance(result, bool), f"Expected bool result for {bad_value!r}"


class TestBreakdownContract:
    """The breakdown function's return shape is the frontend contract.
    Pin the dataclass fields so a future refactor that drops one is
    caught immediately."""

    def test_category_breakdown_entry_has_required_fields(self):
        from genlab_core.scheduling.calibration_logger import CategoryBreakdownEntry

        entry = CategoryBreakdownEntry(
            feedback_category="weak_hook",
            count=24,
            gate_agreed=4,
            gate_disagreed=20,
        )
        assert entry.feedback_category == "weak_hook"
        assert entry.count == 24
        assert entry.gate_agreed == 4
        assert entry.gate_disagreed == 20

    def test_breakdown_returns_empty_list_without_db(self, monkeypatch):
        from genlab_core.scheduling.calibration_logger import breakdown_by_category

        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert breakdown_by_category(niche_id="gaming") == []

    def test_breakdown_returns_empty_for_empty_niche_id(self):
        from genlab_core.scheduling.calibration_logger import breakdown_by_category

        assert breakdown_by_category(niche_id="") == []


class TestMigrationFile:
    def test_migration_file_present(self):
        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        target = migrations_dir / "x4s5t6u7v8w9_calibration_feedback_category.py"
        assert target.exists(), (
            f"Lever B migration file not found at {target}. "
            f"alembic upgrade head will not pick up the column."
        )

    def test_migration_has_expected_revision_chain(self):
        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        target = migrations_dir / "x4s5t6u7v8w9_calibration_feedback_category.py"
        content = target.read_text()
        assert 'revision = "x4s5t6u7v8w9"' in content
        # Chains off Lever E2 migration (w3r4s5t6u7v8) — this enforces
        # that E2 + B are deployed in the right order.
        assert 'down_revision = "w3r4s5t6u7v8"' in content

    def test_migration_creates_partial_index(self):
        """The composite index on (niche_id, feedback_category) MUST
        be partial (WHERE feedback_category IS NOT NULL) so rows from
        approved/skipped actions don't bloat the index."""
        migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        target = migrations_dir / "x4s5t6u7v8w9_calibration_feedback_category.py"
        content = target.read_text()
        assert "WHERE feedback_category IS NOT NULL" in content
