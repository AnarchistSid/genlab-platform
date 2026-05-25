"""R-63: guard against PROMOTED_COLUMNS / schema drift.

``PROMOTED_COLUMNS`` in postgres.py decides which record keys map to real SQL
columns vs the ``extra`` JSONB overflow. When it drifts from the actual table
schema, the backend corrupts data silently:

  * a REAL column missing from the map → writes land in ``extra`` but reads
    prefer the (NULL) real column, so the value vanishes (the video_id bug the
    test-storage job first caught);
  * a PHANTOM entry (in the map but not a real column) → any write including
    that key fails the INSERT/UPDATE outright.

This test queries ``information_schema`` against the Alembic-migrated database
(so it is exact about quoting, reserved words, and op.add_column migrations a
regex can't see) and asserts, for every migrated table, that the promoted set
equals the real non-structural columns. Tables that aren't created by Alembic
(the ad-hoc content_pool/affiliate_clicks/etc. — tracked for adoption under
R-48) are absent from the migrated schema and skipped here.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)

# No rows are created, so the shared conftest fixture's cleanup is a no-op.
_ALL_TABLES: tuple[str, ...] = ()
_TEST_NICHE = None

# Columns every table carries that are intentionally NOT in PROMOTED_COLUMNS.
_STRUCTURAL = {"id", "extra", "created_at", "updated_at"}


def _real_columns(backend, table: str) -> set[str]:
    pool = backend._get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            return {r[0] for r in cur.fetchall()}


def test_promoted_columns_match_schema(pg_backend):
    from genlab_core.storage.postgres import PROMOTED_COLUMNS

    problems: list[str] = []
    checked = 0
    for table, promoted in sorted(PROMOTED_COLUMNS.items()):
        real = _real_columns(pg_backend, table)
        if not real:
            # Not in the Alembic-migrated schema (ad-hoc table). Skip — adoption
            # of those tables is tracked separately (R-48).
            continue
        checked += 1
        real_nonstructural = real - _STRUCTURAL
        unpromoted = real_nonstructural - set(promoted)  # → silent data loss
        phantom = set(promoted) - real  # → INSERT/UPDATE error
        if unpromoted or phantom:
            problems.append(
                f"  {table}: unpromoted_real_columns={sorted(unpromoted)} "
                f"phantom_promoted={sorted(phantom)}"
            )

    assert checked > 0, "no migrated tables found — did `alembic upgrade head` run?"
    assert not problems, "PROMOTED_COLUMNS has drifted from the live schema:\n" + "\n".join(
        problems
    )
