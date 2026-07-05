"""Pins migration a7v8w9x0y1z2 — intelligent transformation schema.

Three angles, all checkable without a live Postgres:

  1. Migration file exists, has the correct revision chain
     (down_revision → z6u7v8w9x0y1), contains the canonical ALTERs
     for pending_feedback.arm_ids_by_dimension + bandit_arms triple
     (arm_type, dimension, dimension_value).

  2. Downgrade is symmetric — the columns get dropped in reverse
     order and the indexes drop before the columns they reference.
     If prod ever rolls back this migration we want a clean path.

  3. Idempotency — every ALTER uses IF NOT EXISTS / IF EXISTS so
     partial-migration recovery on prod doesn't require manual
     column checks.

A separate Postgres-backed integration test would exercise the
schema round-trip with a real DB. This module runs cheap in CI
against the migration source alone.

Related:
- [[intelligent-transformation-architecture-2026-07-05]] — the design
  the schema serves
- [[sprint-intelligent-transformation-commit-2026-07-05]] — the sprint
  commit doc naming which downstream PRs depend on this schema
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _REPO_ROOT / "genlab-core" / "migrations" / "versions"
_MIGRATION_FILE = _MIGRATIONS_DIR / "a7v8w9x0y1z2_intelligent_transformation_schema.py"


class TestMigrationShape:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_FILE.is_file(), (
            f"Migration a7v8w9x0y1z2 not found at {_MIGRATION_FILE}. "
            "This is the schema foundation for the intelligent "
            "transformation sprint. PRs 3-16 all depend on the "
            "columns this migration adds — do not remove without "
            "coordinated rollback of the downstream PRs."
        )

    def test_revision_id_matches_filename(self) -> None:
        src = _MIGRATION_FILE.read_text()
        m = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', src, re.MULTILINE)
        assert m, 'could not find ``revision: str = "..."`` in migration'
        assert m.group(1) == "a7v8w9x0y1z2"

    def test_chains_to_hook_classifier_score(self) -> None:
        """down_revision must be ``z6u7v8w9x0y1`` so this migration
        applies on top of the hook_classifier_score migration. If main
        ships a newer migration between z6u7v8w9x0y1 and this one, the
        chain needs updating."""
        src = _MIGRATION_FILE.read_text()
        m = re.search(r'^down_revision:[^=]+=\s*"([^"]+)"', src, re.MULTILINE)
        assert m, 'could not find ``down_revision: ... = "..."``'
        assert m.group(1) == "z6u7v8w9x0y1"


class TestUpgradeBody:
    """The upgrade body must contain all four ALTER statements plus
    the two indexes. If someone refactors to a different column layout,
    the downstream code that reads arm_ids_by_dimension / arm_type /
    dimension needs coordinated updates."""

    def _upgrade_body(self) -> str:
        src = _MIGRATION_FILE.read_text()
        upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
        return re.sub(r"\s+", " ", upgrade_block)

    def test_adds_arm_ids_by_dimension_to_pending_feedback(self) -> None:
        body = self._upgrade_body()
        assert "ALTER TABLE pending_feedback" in body
        assert "arm_ids_by_dimension" in body
        assert "JSONB" in body
        assert "DEFAULT '{}'::jsonb" in body, (
            "Empty-dict default is critical — legacy publishes and "
            "reels with transformation off must have '{}' rather than "
            "NULL so the reward router's iteration doesn't need "
            "null-checking every row."
        )

    def test_adds_arm_type_to_bandit_arms(self) -> None:
        body = self._upgrade_body()
        assert "ALTER TABLE bandit_arms" in body
        assert "arm_type TEXT" in body

    def test_adds_dimension_and_value_to_bandit_arms(self) -> None:
        body = self._upgrade_body()
        assert "dimension TEXT" in body
        assert "dimension_value TEXT" in body

    def test_creates_analytical_indexes(self) -> None:
        body = self._upgrade_body()
        assert "idx_ba_arm_type_niche" in body
        assert "idx_ba_dimension_niche" in body
        assert "arm_type, niche_id" in body, (
            "Composite (arm_type, niche_id) index supports the common "
            "'get all transformation arms for niche' query pattern "
            "used by Mission Control cards + Strategist."
        )
        assert "WHERE dimension IS NOT NULL" in body, (
            "Partial index on dimension — only transformation arms "
            "populate it. Reduces index size for millions of legacy "
            "content arms."
        )

    def test_all_alters_use_if_not_exists(self) -> None:
        """Idempotency: partial-migration recovery on prod must not
        require manual column checks."""
        body = self._upgrade_body()
        alter_count = body.count("ADD COLUMN")
        if_not_exists_count = body.count("IF NOT EXISTS")
        # 4 columns added + 2 indexes = 6 IF NOT EXISTS clauses expected
        assert if_not_exists_count >= alter_count, (
            f"Expected all {alter_count} ADD COLUMN clauses to use "
            f"IF NOT EXISTS; only {if_not_exists_count} present. "
            "Partial migration recovery on prod will fail without it."
        )


class TestDowngradeBody:
    """downgrade must be runnable — drops in reverse dependency order.
    Indexes drop before the columns they reference so the drop path
    doesn't error on Postgres."""

    def _downgrade_body(self) -> str:
        src = _MIGRATION_FILE.read_text()
        downgrade_block = src.split("def downgrade")[1]
        return re.sub(r"\s+", " ", downgrade_block)

    def test_drops_indexes_before_columns(self) -> None:
        body = self._downgrade_body()
        # Find positions of first DROP INDEX vs first DROP COLUMN
        idx_pos = body.find("DROP INDEX")
        col_pos = body.find("DROP COLUMN")
        assert idx_pos != -1, "downgrade should DROP INDEX"
        assert col_pos != -1, "downgrade should DROP COLUMN"
        assert idx_pos < col_pos, (
            "Indexes must drop before the columns they reference — "
            "otherwise the column drop errors when the index still "
            "depends on it."
        )

    def test_drops_all_four_columns(self) -> None:
        body = self._downgrade_body()
        assert "arm_ids_by_dimension" in body
        assert "arm_type" in body
        assert "dimension_value" in body
        # 'dimension' word alone is ambiguous (appears in dimension_value).
        # Check for the DROP COLUMN dimension line specifically.
        assert re.search(r"DROP COLUMN IF EXISTS dimension\b", body), (
            "downgrade must DROP COLUMN dimension (without _value suffix)"
        )

    def test_all_drops_use_if_exists(self) -> None:
        """Symmetric idempotency: partial rollback recovery."""
        body = self._downgrade_body()
        drop_count = body.count("DROP")
        if_exists_count = body.count("IF EXISTS")
        assert if_exists_count >= drop_count, (
            f"Expected all {drop_count} DROP clauses to use IF EXISTS; "
            f"only {if_exists_count} present. Partial rollback recovery "
            "will fail without it."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
