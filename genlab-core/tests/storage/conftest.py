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

    Skip-at-fixture-setup guard (2026-07-15):
        Storage-test modules use ``pytestmark = pytest.mark.skipif(
        not os.environ.get("POSTGRES_PASSWORD"), ...)`` — but skipif
        evaluates at COLLECTION time. If any earlier test file's import
        triggers ``load_dotenv`` and repopulates POSTGRES_PASSWORD (see
        the leak documented in ``tests/conftest.py``), the skipif returns
        False and these tests are NOT marked skipped. They then reach
        the fixture setup phase, which was previously failing with
        connection errors (no local Postgres on dev boxes) — showing
        up as ERROR in the sweep rather than the intended SKIP.

        This guard catches the connection error and re-raises as a
        pytest.skip so the runtime-level "no reachable DB" case looks
        identical to the collection-level "no env var" case.
    """
    import psycopg
    from genlab_core.storage.postgres import PostgresBackend

    # The RLS isolation tests are only meaningful when connecting as a
    # NON-superuser, non-owner role — Postgres superusers (and table owners
    # without FORCE) bypass row-level security entirely. CI creates a dedicated
    # ``genlab_app`` role and points these vars at it; locally they default to
    # the plain ``genlab`` superuser (CRUD still validates, RLS may not isolate).
    backend = PostgresBackend(
        host=os.environ.get("GENLAB_TEST_PG_HOST", "localhost"),
        port=int(os.environ.get("GENLAB_TEST_PG_PORT", "5432")),
        database=os.environ.get("GENLAB_TEST_PG_DB", "genlab"),
        user=os.environ.get("GENLAB_TEST_PG_USER", "genlab"),
        password=os.environ.get("GENLAB_TEST_PG_PASSWORD")
        or os.environ.get("POSTGRES_PASSWORD", ""),
        min_size=1,
        max_size=2,
    )

    # Runtime reachability probe. If the DB isn't there, skip cleanly
    # instead of ERRORing the test — matches the intent of the module-
    # level skipif (which only catches the missing-env-var case).
    try:
        pool = backend._get_pool()
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except (psycopg.OperationalError, psycopg.errors.ConnectionTimeout) as exc:
        backend.close()
        pytest.skip(f"Local Postgres not reachable: {exc}")

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
