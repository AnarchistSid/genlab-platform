"""SR-F: add WITH CHECK to the 2 policies SR-B's drift-tolerant skip missed.

Revision ID: u1p2q3r4s5t6
Revises: t0o1p2q3r4s5
Create Date: 2026-06-17

SR-B (#291 + #294 hotfix) added WITH CHECK to 17 of 19 niche-isolation
policies. The drift-tolerant DO block skipped two, both with RAISE
NOTICE in the upgrade output:

  1. ``affiliate_clicks`` has a policy named
     ``affiliate_clicks_niche_policy`` (NOT the canonical
     ``niche_isolation``). The table was created by an out-of-band
     .sql before the l2g3h4i5j6k7 Alembic migration ran; l2's
     ``IF to_regclass IS NULL THEN CREATE TABLE...`` idempotency guard
     correctly skipped the recreate, but the policy name drift remained.

  2. ``email_subscribers`` has a ``niche_isolation`` policy with no
     WITH CHECK. The table is in g7b8c9d0e1f2_create_email_subscribers
     but its USING-only policy predates the SR-B WITH CHECK pattern;
     SR-B's ``_TABLES`` tuple was assembled BEFORE the email path was
     prominent in the audit so it was just omitted.

Both gaps are the same risk class SR-B closed for the other 17 tables:
a tenant-A session can INSERT a row labelled ``niche_id='tenant_b'``
and the row materialises in tenant B's view. Identical fix.

Approach
========
Same defensive DO-block-introspect pattern as the SR-B hotfix. Each
ALTER takes a literal (table, policy_name) pair so the migration is
exhaustive — anyone adding a new RLS-protected table with a non-
canonical policy name MUST add a row here or accept that they're
shipping without write-side isolation.

Drift detection follow-up
=========================
A startup-time check that asserts every table in ``_VALID_TABLES``
(genlab_core/storage/postgres.py:136) has BOTH a niche_isolation
policy AND a WITH CHECK clause would prevent this drift class from
ever surprising us again. Out of scope here — file as a separate task.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "u1p2q3r4s5t6"
down_revision = "t0o1p2q3r4s5"
branch_labels = None
depends_on = None


_WITH_CHECK_EXPR = """
    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) IN ('', 'all')
    OR current_setting('app.niche_id', true) IS NULL
"""

# (table, policy_name) pairs SR-B's _TABLES + canonical naming missed.
# Listed explicitly (not auto-discovered from pg_policies) so a future
# operator can read this file and see exactly what runtime invariant
# we're enforcing — and so the migration is reproducible on a fresh
# DB that may not have the same drift state as prod.
_POLICY_PAIRS = (
    ("affiliate_clicks", "affiliate_clicks_niche_policy"),
    ("email_subscribers", "niche_isolation"),
)


def upgrade() -> None:
    """Add WITH CHECK to the 2 orphan policies.

    Uses the same defensive DO $srf$ ... $srf$ pattern as the SR-B
    hotfix — if the policy is missing entirely on a fresh DB (e.g. the
    table was created via a no-policy migration in some other future
    revision), the ALTER skips with a NOTICE rather than aborting.
    """
    for table, policy in _POLICY_PAIRS:
        op.execute(
            f"""
            DO $srf$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = current_schema()
                      AND tablename = '{table}'
                      AND policyname = '{policy}'
                ) THEN
                    ALTER POLICY {policy} ON {table}
                        WITH CHECK ({_WITH_CHECK_EXPR});
                ELSE
                    RAISE NOTICE 'SR-F skip: policy %.% not found (drift)',
                                 '{table}', '{policy}';
                END IF;
            END
            $srf$;
            """
        )


def downgrade() -> None:
    """Drop WITH CHECK by DROP + CREATE the policy without it.

    Same shape as the SR-B downgrade — PostgreSQL doesn't have
    ``ALTER POLICY ... DROP WITH CHECK``.
    """
    using_expr = _WITH_CHECK_EXPR  # same expression
    for table, policy in _POLICY_PAIRS:
        op.execute(
            f"""
            DO $srf$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = current_schema()
                      AND tablename = '{table}'
                      AND policyname = '{policy}'
                ) THEN
                    DROP POLICY {policy} ON {table};
                    CREATE POLICY {policy} ON {table}
                        USING ({using_expr});
                END IF;
            END
            $srf$;
            """
        )
