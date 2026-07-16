"""Pin the publishing_analytics UNIQUE(blueprint_id, platform) constraint
shipped in migration `b9x0y1z2a3b4` (2026-07-17).

Deep-cuts audit found 183 duplicate `(blueprint_id, platform)` pairs
accumulated by the publisher writing a new PA row on every retry
attempt without an idempotency key. The audit's UNIQUE partial
index closes the class-of-bug so retries fail-fast with
IntegrityError instead of silently corrupting the reward fetcher's
aggregate.

This test greps the migration file for the CREATE UNIQUE INDEX
statement + verifies the constraint definition matches the
audit's specification. If someone drops the index in a later
migration without adding a replacement, the failure surfaces
here.

Companion runtime pin: dashboard/tests/ or a real-DB integration
test would assert an actual duplicate INSERT raises IntegrityError.
Skipped here — this file-level pin is the greppable regression
guard that runs in every CI cycle.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_MIGRATIONS_DIR = _REPO_ROOT / "genlab-core" / "migrations" / "versions"

_INDEX_NAME = "uq_publishing_analytics_bp_platform"
_MIGRATION_FILENAME = "b9x0y1z2a3b4_publishing_analytics_cleanup_and_unique.py"


def test_pa_unique_index_migration_exists() -> None:
    """The migration file itself must be on disk (someone could delete
    it as "already applied to prod" — but every fresh DB (CI, dev, new
    prod) still needs to run it)."""
    migration_path = _MIGRATIONS_DIR / _MIGRATION_FILENAME
    assert migration_path.is_file(), (
        f"{_MIGRATION_FILENAME} was removed. Every fresh DB (CI, new "
        "prod, dev) needs this migration to apply the UNIQUE constraint. "
        "Restore it from git history."
    )


def test_pa_unique_index_ddl_present() -> None:
    """The migration must contain the CREATE UNIQUE INDEX statement
    for `uq_publishing_analytics_bp_platform`. Regression scenario:
    someone "simplifies" the migration by dropping the index creation."""
    migration_path = _MIGRATIONS_DIR / _MIGRATION_FILENAME
    src = migration_path.read_text(encoding="utf-8")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in src, (
        f"{_MIGRATION_FILENAME} no longer contains the "
        "`CREATE UNIQUE INDEX IF NOT EXISTS` DDL. Without it, publisher "
        "retries can re-introduce (blueprint_id, platform) duplicates."
    )
    assert _INDEX_NAME in src, (
        f"{_MIGRATION_FILENAME} no longer references index name "
        f"{_INDEX_NAME!r}. The audit's pin, monitoring, and dashboard "
        "check all rely on this exact name — do not rename without "
        "coordinating."
    )


def test_pa_unique_index_is_partial_on_non_null() -> None:
    """The UNIQUE index MUST be a partial index (WHERE both cols
    NOT NULL). Full-column unique would break legacy rows with
    NULL blueprint_id (SKIPPED records from before the bp linkage)."""
    migration_path = _MIGRATIONS_DIR / _MIGRATION_FILENAME
    src = migration_path.read_text(encoding="utf-8")

    assert (
        "WHERE blueprint_id IS NOT NULL AND platform IS NOT NULL" in src
    ), (
        f"{_MIGRATION_FILENAME} lost the partial-index `WHERE ... IS "
        "NOT NULL` clause. Removing it would make the constraint apply "
        "to legacy NULL-bp rows and break the migration on fresh DBs "
        "with historical SKIPPED PA rows."
    )


def test_no_later_migration_drops_the_index() -> None:
    """Scan all migrations that chain AFTER b9x0y1z2a3b4. None should
    contain a `DROP INDEX ... uq_publishing_analytics_bp_platform`
    without a paired re-CREATE.

    Regression scenario: someone reverts the constraint via a
    downstream migration to unblock a hot-fix, then forgets to
    restore it.
    """
    for path in _MIGRATIONS_DIR.glob("*.py"):
        if path.name == _MIGRATION_FILENAME:
            continue
        src = path.read_text(encoding="utf-8")
        if "DROP INDEX" in src and _INDEX_NAME in src:
            # Only fail if the same file DOESN'T ALSO recreate it.
            if "CREATE UNIQUE INDEX" not in src or _INDEX_NAME not in src.split("DROP INDEX", 1)[1]:
                raise AssertionError(
                    f"{path.name} drops {_INDEX_NAME} without re-creating "
                    "it. Publisher retries will silently re-introduce "
                    "duplicate PA rows. If the drop is intentional, "
                    "update this pin AND CLAUDE.md."
                )
