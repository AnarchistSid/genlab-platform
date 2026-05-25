"""Shared fixtures for the PostgresBackend storage tests.

R-64/R-48: the per-phase test files each carried an identical ``pg_backend``
fixture whose teardown called a REMOVED async API (``backend._ensure_pool()``,
``backend._loop.run_until_complete(...)``, ``await pool.acquire()``). The live
``PostgresBackend`` is fully synchronous psycopg3 — those attributes don't exist,
so the teardown would ``AttributeError`` if the tests ever ran. They only stayed
"green" because the whole module skips without ``POSTGRES_PASSWORD`` and no CI job
set it. This consolidates the fixture into one place with a correct SYNCHRONOUS
teardown so the tests can actually run (and now do, in the ``test-storage`` CI job).

Each test module still declares its own ``_ALL_TABLES`` and ``_TEST_NICHE``
module-level constants; the shared fixture reads them off the requesting module
to scope cleanup.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def pg_backend(request):
    """A PostgresBackend on the local ``genlab`` DB, with sync row cleanup.

    Cleanup deletes this module's test rows (its ``_TEST_NICHE`` and any
    ``rls_test_%`` niches the RLS tests create) using the synchronous psycopg3
    pool, then closes the pool.
    """
    from genlab_core.storage.postgres import PostgresBackend

    backend = PostgresBackend(
        host="localhost",
        port=5432,
        database="genlab",
        user="genlab",
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        min_size=1,
        max_size=2,
    )
    yield backend

    tables = getattr(request.module, "_ALL_TABLES", ())
    niche = getattr(request.module, "_TEST_NICHE", None)
    try:
        pool = backend._get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # 'all' hits the RLS escape hatch so cleanup sees every row
                # regardless of which niche the test data was written under.
                cur.execute("SELECT set_config('app.niche_id', 'all', true)")
                for tbl in tables:
                    if niche:
                        cur.execute(f"DELETE FROM {tbl} WHERE niche_id = %s", (niche,))
                    cur.execute(f"DELETE FROM {tbl} WHERE niche_id LIKE 'rls_test_%'")
            conn.commit()
    finally:
        backend.close()
