"""SR-B: WITH CHECK clause on every niche_isolation RLS policy.

Verifies migration ``t0o1p2q3r4s5_rls_with_check_clauses`` actually
blocks the cross-tenant write that was structurally possible before
the WITH CHECK clause was added.

Requires the ``test-storage`` CI job's setup:
  * Postgres up
  * ``alembic upgrade head`` applied
  * ``genlab_app`` non-superuser role created (RLS bypassed for
    superusers; this test would silently pass without that role)
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)

# Conftest deletes rls_test_% niches in its teardown.
_TEST_NICHE = f"rls_test_{uuid.uuid4().hex[:8]}"
_OTHER_NICHE = f"rls_test_{uuid.uuid4().hex[:8]}"

# Tables to sweep — sample one per migration so we're sure the policy
# is uniformly applied. Full per-table coverage would be redundant
# (the migration uses one ALTER POLICY loop).
_ALL_TABLES = ("stories", "blueprints", "publishing_analytics", "templates")


def _open_app_conn():
    """Raw psycopg connection as the non-superuser app role.

    Bypasses ``PostgresBackend.create()`` which doesn't set the niche
    GUC (SR-C, separate). For SR-B we want to drive the GUC manually
    and verify the WITH CHECK clause fires.
    """
    import psycopg

    dsn = (
        f"postgresql://"
        f"{os.environ.get('GENLAB_TEST_PG_USER', 'genlab')}:"
        f"{os.environ.get('GENLAB_TEST_PG_PASSWORD') or os.environ.get('POSTGRES_PASSWORD', '')}@"
        f"{os.environ.get('GENLAB_TEST_PG_HOST', 'localhost')}:"
        f"{os.environ.get('GENLAB_TEST_PG_PORT', '5432')}/"
        f"{os.environ.get('GENLAB_TEST_PG_DB', 'genlab')}"
    )
    return psycopg.connect(dsn)


class TestSRBWithCheckBlocksCrossTenantInsert:
    """Confirm the WITH CHECK clause blocks INSERT with a foreign niche_id.

    Pre-migration: an app-role connection with ``app.niche_id='A'``
    could INSERT a row with ``niche_id='B'`` and that row would belong
    to tenant B (cross-tenant data injection). Post-migration:
    Postgres raises ``CheckViolation``.
    """

    def test_stories_blocks_foreign_niche_insert(self):
        import psycopg

        record_id = str(uuid.uuid4())
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", (_TEST_NICHE,))
                # Per-tenant context set; try to write a foreign-tenant row.
                with pytest.raises(psycopg.errors.CheckViolation):
                    cur.execute(
                        "INSERT INTO stories (id, niche_id, story_id, status) "
                        "VALUES (%s::uuid, %s, %s, 'NEW')",
                        (record_id, _OTHER_NICHE, f"srb_{uuid.uuid4().hex[:8]}"),
                    )
            conn.rollback()

    def test_blueprints_blocks_foreign_niche_insert(self):
        import psycopg

        record_id = str(uuid.uuid4())
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", (_TEST_NICHE,))
                with pytest.raises(psycopg.errors.CheckViolation):
                    cur.execute(
                        "INSERT INTO blueprints (id, niche_id, blueprint_id, status) "
                        "VALUES (%s::uuid, %s, %s, 'DRAFTED')",
                        (record_id, _OTHER_NICHE, f"srb_{uuid.uuid4().hex[:8]}"),
                    )
            conn.rollback()

    def test_own_niche_insert_still_allowed(self):
        """The fix must NOT break the legitimate path —
        tenant A writing their OWN tenant-A row continues to succeed.
        """
        record_id = str(uuid.uuid4())
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", (_TEST_NICHE,))
                cur.execute(
                    "INSERT INTO stories (id, niche_id, story_id, status) "
                    "VALUES (%s::uuid, %s, %s, 'NEW') RETURNING id",
                    (record_id, _TEST_NICHE, f"srb_own_{uuid.uuid4().hex[:8]}"),
                )
                row = cur.fetchone()
                assert row is not None
            conn.commit()

    def test_admin_mode_can_still_write_any_niche(self):
        """Admin mode (GUC='' / 'all' / NULL) preserves the existing
        ability to write any tenant's row — used by maintenance scripts,
        bulk operators, and the conftest teardown. The WITH CHECK clause
        mirrors USING exactly, so admin-mode keeps working.
        """
        record_id = str(uuid.uuid4())
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", ("",))
                cur.execute(
                    "INSERT INTO stories (id, niche_id, story_id, status) "
                    "VALUES (%s::uuid, %s, %s, 'NEW') RETURNING id",
                    (record_id, _OTHER_NICHE, f"srb_admin_{uuid.uuid4().hex[:8]}"),
                )
                row = cur.fetchone()
                assert row is not None
            conn.commit()

    def test_update_blocked_when_changing_to_foreign_niche(self):
        """Per PostgreSQL: WITH CHECK applies to the NEW row state on
        UPDATE. A tenant-A user shouldn't be able to UPDATE a row to
        change its niche_id to tenant B (an exfiltration vector — the
        row would then disappear from tenant A's USING view and
        materialise in tenant B's).

        Set up: admin inserts a row owned by _TEST_NICHE. Then a
        tenant-_TEST_NICHE session tries to UPDATE niche_id → _OTHER_NICHE.
        """
        import psycopg

        record_id = str(uuid.uuid4())
        story_id = f"srb_upd_{uuid.uuid4().hex[:8]}"

        # Setup: admin writes a row owned by _TEST_NICHE
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", ("",))
                cur.execute(
                    "INSERT INTO stories (id, niche_id, story_id, status) "
                    "VALUES (%s::uuid, %s, %s, 'NEW')",
                    (record_id, _TEST_NICHE, story_id),
                )
            conn.commit()

        # Attack: tenant-A user tries to flip niche_id to tenant-B
        with _open_app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", (_TEST_NICHE,))
                with pytest.raises(psycopg.errors.CheckViolation):
                    cur.execute(
                        "UPDATE stories SET niche_id = %s WHERE id = %s::uuid",
                        (_OTHER_NICHE, record_id),
                    )
            conn.rollback()
