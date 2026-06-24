"""SR-B: add missing WITH CHECK on tier_history + normalize 3 narrow USING clauses

Revision ID: b8c9d0e1f2g3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-24 12:30:00.000000+00:00

PR #535 (2026-06-24): the SYSTEM-RESEARCH.md §9 SR-B finding
("RLS policies have only USING, no WITH CHECK") was MOSTLY closed
by prior work — but a production audit on 2026-06-24 surfaced four
remaining loose ends:

  1. ``tier_history`` policy ships only USING, no WITH CHECK at all.
     This is the literal SR-B bug class for a single table — INSERTs
     can land regardless of ``app.niche_id`` because the RLS WITH
     CHECK clause doesn't exist. (Created in migration
     ``a7b8c9d0e1f2`` which forgot the WITH CHECK.)

  2-4. ``ab_tests``, ``audience_snapshots``, ``email_subscribers``
     have USING clauses NARROWER than their WITH CHECK clauses:
     they accept only ``'all'`` for admin-mode SELECT but accept
     ``''``, ``'all'``, OR NULL for admin-mode INSERT. Inconsistent
     but not insecure — an INSERT could land that no SELECT can
     read back (admin-mode SELECT would miss it). Normalize for
     defense-in-depth + uniform policy shape across all tables.

## Pre-existing convention

The standard policy shape (already on 16 of 20 tables) is:

  USING + WITH CHECK both =
    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) IN ('', 'all')
    OR current_setting('app.niche_id', true) IS NULL

This is the admin-mode shape — empty/'all'/NULL all bypass scoping.
We're aligning the 4 outliers to match.

## Safety

* Idempotent: DROP POLICY IF EXISTS + CREATE POLICY pattern matches
  every prior RLS migration on this project.
* Reversible: downgrade restores the pre-PR policy shape per table.
  Listed in policy-shape-per-table order so a partial downgrade
  doesn't leave the DB in an inconsistent state.
* Single transaction: alembic wraps each migration in BEGIN/COMMIT.
  Either all 4 policies update or none — no half-state risk.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2g3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


# The canonical policy shape used by the other 16 tables.
_CANONICAL_USING_AND_CHECK = """
    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) IN ('', 'all')
    OR current_setting('app.niche_id', true) IS NULL
"""


def upgrade() -> None:
    # 1. tier_history — add the missing WITH CHECK
    op.execute(f"""
    DROP POLICY IF EXISTS niche_isolation ON tier_history;
    CREATE POLICY niche_isolation ON tier_history
        USING ({_CANONICAL_USING_AND_CHECK})
        WITH CHECK ({_CANONICAL_USING_AND_CHECK});
    """)

    # 2. ab_tests — widen USING to match WITH CHECK
    op.execute(f"""
    DROP POLICY IF EXISTS niche_isolation ON ab_tests;
    CREATE POLICY niche_isolation ON ab_tests
        USING ({_CANONICAL_USING_AND_CHECK})
        WITH CHECK ({_CANONICAL_USING_AND_CHECK});
    """)

    # 3. audience_snapshots — widen USING to match WITH CHECK
    op.execute(f"""
    DROP POLICY IF EXISTS niche_isolation ON audience_snapshots;
    CREATE POLICY niche_isolation ON audience_snapshots
        USING ({_CANONICAL_USING_AND_CHECK})
        WITH CHECK ({_CANONICAL_USING_AND_CHECK});
    """)

    # 4. email_subscribers — widen USING to match WITH CHECK
    op.execute(f"""
    DROP POLICY IF EXISTS niche_isolation ON email_subscribers;
    CREATE POLICY niche_isolation ON email_subscribers
        USING ({_CANONICAL_USING_AND_CHECK})
        WITH CHECK ({_CANONICAL_USING_AND_CHECK});
    """)


def downgrade() -> None:
    # Restore the pre-PR-535 shapes per table. tier_history goes back
    # to USING-only (no WITH CHECK), and the 3 others go back to the
    # narrower USING that allowed only 'all' for admin mode.
    op.execute("""
    DROP POLICY IF EXISTS niche_isolation ON tier_history;
    CREATE POLICY niche_isolation ON tier_history
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );
    """)

    # ab_tests / audience_snapshots / email_subscribers all had this
    # narrower USING shape pre-PR-535. WITH CHECK was already aligned
    # with the canonical shape so we leave it.
    _narrow_using = """
        niche_id = current_setting('app.niche_id'::text, true)
        OR current_setting('app.niche_id'::text, true) = 'all'::text
    """
    _canonical_check = """
        niche_id = current_setting('app.niche_id'::text, true)
        OR current_setting('app.niche_id'::text, true) = ANY (ARRAY[''::text, 'all'::text])
        OR current_setting('app.niche_id'::text, true) IS NULL
    """
    for table in ("ab_tests", "audience_snapshots", "email_subscribers"):
        op.execute(f"""
        DROP POLICY IF EXISTS niche_isolation ON {table};
        CREATE POLICY niche_isolation ON {table}
            USING ({_narrow_using})
            WITH CHECK ({_canonical_check});
        """)
