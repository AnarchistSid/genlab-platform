"""2026-07-24: pin that DB schema columns are all in PROMOTED_COLUMNS
(or in an explicit extra-only whitelist).

Motivating class-of-bug: today's discovery that
``blueprints.action_taken_source`` existed in the DB schema but was
NOT in ``PROMOTED_COLUMNS['blueprints']``. Auto-approver writes
silently routed the value into the ``extra`` JSONB column instead
of the dedicated column, leaving the column NULL for weeks. Every
downstream WHERE filter missed 23 auto-approvals.

Class-of-bug memo:
``[[class-of-bug-column-in-db-not-in-promoted-columns]]``

This test prevents regression by cross-checking:
  information_schema.columns  vs  PROMOTED_COLUMNS  vs  _EXTRA_ONLY

Behavior:
* Skips when DATABASE_URL isn't set — local dev + CI don't always
  have a Postgres instance available.
* Skips when connection fails for any reason (transient DB blips
  shouldn't fail CI).
* Hard-fails when ``GENLAB_REQUIRE_SCHEMA_PIN=1`` — the escape
  hatch for prod CI to demand the assertion runs, even if the DB
  is momentarily unavailable.

To fix a failure:
  1. Add the column to ``PROMOTED_COLUMNS[table]`` in
     ``genlab_core/storage/postgres.py`` — normal case.
  2. Add the column to ``_EXTRA_ONLY[table]`` below — rare case,
     only for columns that intentionally live outside the promoted
     write path (system columns, audit tables, etc.).
"""

from __future__ import annotations

import os
from typing import Any

import pytest


# Columns that Postgres or alembic add automatically — never expected
# in PROMOTED_COLUMNS (the writer never sees them; they're system-
# managed).
_SYSTEM_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "extra",
        "created_at",
        "updated_at",
    }
)


# Columns that INTENTIONALLY live outside PROMOTED_COLUMNS — writers
# don't set them, or they're managed by a separate mechanism. Add
# entries here with a written justification; keep the set small.
_EXTRA_ONLY: dict[str, frozenset[str]] = {
    # Future: add tables + their intentional non-promoted columns.
    # Empty today — every discovered non-promoted column should
    # move to PROMOTED_COLUMNS unless there's a genuine reason not to.
}


def _get_db_columns_per_table(conn: Any) -> dict[str, set[str]]:
    """Return {table_name: {column_name, ...}} from information_schema
    for every table in PROMOTED_COLUMNS. Restricted to those tables
    to avoid failing on niche_local tables (bandit_arms, etc. that
    have their own scope discipline)."""
    from genlab_core.storage.postgres import PROMOTED_COLUMNS

    result: dict[str, set[str]] = {}
    tables = list(PROMOTED_COLUMNS.keys())
    for table in tables:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchall()
        result[table] = {r[0] for r in rows}
    return result


def _resolve_dsn() -> str:
    """Read the DSN from env. Prefers ``GENLAB_SCHEMA_PIN_DSN`` over
    ``DATABASE_URL`` — conftest.py strips DATABASE_URL from tests'
    environment (see conftest.py:31; SharePoint fallback discipline),
    so we need a dedicated env var to bypass the strip when we WANT
    the pin to run against a real DB (prod CI, staging validation)."""
    return (
        os.environ.get("GENLAB_SCHEMA_PIN_DSN", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )


def _live_db_available() -> bool:
    """Cheap probe. Returns True iff we can psycopg.connect and run
    a trivial SELECT within the standard connect timeout."""
    dsn = _resolve_dsn()
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False


def _require_schema_pin() -> bool:
    """Hard-fail mode for prod CI. Set the env var to force the
    assertion to run even when the DB isn't reachable — surfaces
    connection issues rather than silently skipping."""
    return os.environ.get("GENLAB_REQUIRE_SCHEMA_PIN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def test_every_db_column_is_promoted_or_extra_only():
    """Every column in the DB schema for tables in PROMOTED_COLUMNS
    must be EITHER in PROMOTED_COLUMNS[table] OR in _EXTRA_ONLY[table]
    OR in _SYSTEM_COLUMNS.

    A column that exists in the DB but is missing from all three sets
    is the class-of-bug from 2026-07-24: writes silently land in
    extra JSONB, dedicated column stays NULL, every WHERE filter
    misses those rows."""
    if not _live_db_available():
        if _require_schema_pin():
            pytest.fail(
                "GENLAB_REQUIRE_SCHEMA_PIN=1 but DB is not reachable. "
                "The schema pin needs a live Postgres to run. Either "
                "unset the env var, set GENLAB_SCHEMA_PIN_DSN "
                "(DATABASE_URL is stripped by conftest.py:31), or fix "
                "the connection."
            )
        pytest.skip("GENLAB_SCHEMA_PIN_DSN not set or DB unreachable")

    import psycopg

    from genlab_core.storage.postgres import PROMOTED_COLUMNS

    dsn = _resolve_dsn()

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        db_columns = _get_db_columns_per_table(conn)

    unaccounted: dict[str, set[str]] = {}
    for table, cols in db_columns.items():
        if not cols:
            # Table exists in PROMOTED_COLUMNS but not in the DB —
            # separate class-of-bug (stale entry). Not what this test
            # covers; leave alone. Sibling test could pin this.
            continue
        promoted = PROMOTED_COLUMNS.get(table, set())
        extra_only = _EXTRA_ONLY.get(table, frozenset())
        missing = cols - promoted - extra_only - _SYSTEM_COLUMNS
        if missing:
            unaccounted[table] = missing

    if unaccounted:
        lines = ["Columns in DB schema but missing from PROMOTED_COLUMNS + _EXTRA_ONLY:"]
        for table, cols in sorted(unaccounted.items()):
            lines.append(f"  {table}: {sorted(cols)}")
        lines.append("")
        lines.append(
            "Class-of-bug memo: "
            "[[class-of-bug-column-in-db-not-in-promoted-columns]]. "
            "Writes to these columns silently route into extra JSONB "
            "instead of the dedicated column, leaving downstream "
            "WHERE filters blind. Fix by either adding the column to "
            "PROMOTED_COLUMNS[table] in genlab_core/storage/postgres.py "
            "OR adding to _EXTRA_ONLY[table] in this test file with "
            "written justification."
        )
        pytest.fail("\n".join(lines))
