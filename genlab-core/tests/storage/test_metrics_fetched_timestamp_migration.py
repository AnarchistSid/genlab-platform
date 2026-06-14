"""Pins migration q7l8m9n0o1p2 — metrics_fetched bool→timestamp.

Three angles, all checkable without a live Postgres:

  1. Migration file exists, has the correct revision chain, contains
     the canonical ALTER TABLE that converts boolean → timestamptz.

  2. Production writers continue to pass ISO timestamp strings (not
     bools, not datetimes) — pinning the call shape so a future
     refactor that "simplifies" to ``"metrics_fetched": True`` would
     fail the test before regressing the column data.

  3. Production readers use truthy-check semantics, not ``is True``
     (which would break the moment the column starts holding a
     datetime instead of a bool).

A separate Postgres-backed integration test
(``test_postgres_phase3_pub_analytics.TestPublishingAnalyticsCRUD``)
exercises the round-trip with a real DB but only runs when
``POSTGRES_PASSWORD`` is set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _REPO_ROOT / "genlab-core" / "migrations" / "versions"
_MIGRATION_FILE = _MIGRATIONS_DIR / "q7l8m9n0o1p2_metrics_fetched_timestamp.py"
_FETCH_INSIGHTS = (
    _REPO_ROOT / "genlab-core" / "src" / "genlab_core" / "pipeline" / "stages" / "fetch_insights.py"
)


class TestMigrationShape:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_FILE.is_file(), (
            f"Migration q7l8m9n0o1p2 not found at {_MIGRATION_FILE}. "
            "If you renamed/removed it, also update the downstream "
            "fix_insights writer that depends on the timestamp column "
            "type — without the migration the writes silently fail."
        )

    def test_revision_id_matches_filename(self) -> None:
        src = _MIGRATION_FILE.read_text()
        m = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', src, re.MULTILINE)
        assert m, 'could not find ``revision: str = "..."`` in migration'
        assert m.group(1) == "q7l8m9n0o1p2"

    def test_chains_to_pipeline_run_costs(self) -> None:
        """down_revision must be ``p6k7l8m9n0o1`` so this migration
        applies on top of the per-run-costs migration. If main ships
        a newer migration between p6k7l8m9n0o1 and this one, the
        chain needs updating."""
        src = _MIGRATION_FILE.read_text()
        m = re.search(r'^down_revision:[^=]+=\s*"([^"]+)"', src, re.MULTILINE)
        assert m, 'could not find ``down_revision: ... = "..."``'
        assert m.group(1) == "p6k7l8m9n0o1"

    def test_alters_metrics_fetched_to_timestamptz(self) -> None:
        """The upgrade body must contain the canonical conversion. If
        someone refactors to drop+add-column instead, that loses the
        column's stable position and the column-list-driven serializer
        in ``backlog_client._serialize_for_postgres`` might surprise."""
        src = _MIGRATION_FILE.read_text()
        # Normalise whitespace so ``ALTER\n   COLUMN`` matches too.
        upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
        upgrade_norm = re.sub(r"\s+", " ", upgrade_block)
        assert "ALTER TABLE publishing_analytics" in upgrade_norm
        assert "ALTER COLUMN metrics_fetched" in upgrade_norm
        assert "TYPE TIMESTAMP WITH TIME ZONE" in upgrade_norm
        assert "USING NULL" in upgrade_norm, (
            "The USING NULL clause is required because boolean → "
            "timestamp has no natural conversion. Dropping it would "
            "make the migration fail on prod where the column has "
            "live data."
        )

    def test_downgrade_returns_to_boolean(self) -> None:
        """downgrade must be runnable — boolean column + default. If
        we ever need to roll back the migration on prod we want a
        clean path."""
        src = _MIGRATION_FILE.read_text()
        downgrade_block = src.split("def downgrade")[1]
        downgrade_norm = re.sub(r"\s+", " ", downgrade_block)
        assert "TYPE BOOLEAN" in downgrade_norm
        assert "DEFAULT false" in downgrade_norm


class TestWriterStillSendsIsoString:
    """The whole point of the migration is to match what the writer
    already does — pin that contract so a future refactor doesn't
    re-introduce the type mismatch."""

    def test_fetch_insights_writes_isoformat_for_metrics_fetched(self) -> None:
        src = _FETCH_INSIGHTS.read_text()
        # The exact line the migration is designed around. If this
        # changes to e.g. ``"metrics_fetched": True`` then EITHER the
        # column should revert to boolean OR the writer needs a
        # different timestamp source.
        assert '"metrics_fetched": now.isoformat()' in src, (
            "fetch_insights.py no longer writes now.isoformat() into "
            "metrics_fetched. If the writer changed to a bool, also "
            "roll back migration q7l8m9n0o1p2 (downgrade exists) — "
            "otherwise the column type and the value type silently "
            "disagree again and the upsert drops to no-op."
        )


class TestReaderUsesTruthyCheck:
    """Readers MUST use truthy-check semantics. ``is True`` or
    explicit-bool comparisons would break the moment the column
    started holding timestamps (which it now does post-migration)."""

    def test_fetch_insights_uses_truthy_check(self) -> None:
        src = _FETCH_INSIGHTS.read_text()
        # The exact read pattern we want to preserve.
        assert "if metrics_fetched:" in src, (
            "fetch_insights.py no longer guards on truthy "
            "metrics_fetched. If someone changed this to "
            "``if metrics_fetched is True`` or ``== True``, the read "
            "path will silently re-fetch already-collected metrics "
            "because timestamps are not equal to True."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
