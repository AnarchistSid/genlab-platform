"""Add WITH CHECK to all niche_isolation RLS policies (SR-B fix).

Revision ID: t0o1p2q3r4s5
Revises: s9n0o1p2q3r4
Create Date: 2026-06-17

SR-B from docs/SYSTEM-RESEARCH.md § 9.1 (Critical):

  > RLS policies have only USING, no WITH CHECK → write-side
  > cross-tenant isolation is structurally absent.

Today's policies look like:

    CREATE POLICY niche_isolation ON blueprints
      USING (
        niche_id = current_setting('app.niche_id', true)
        OR current_setting('app.niche_id', true) IN ('', 'all')
        OR current_setting('app.niche_id', true) IS NULL
      );

The USING clause is the *read* gate (and is what Postgres consults
when finding rows to UPDATE/DELETE). It correctly filters reads to the
current tenant's rows. But because there is no WITH CHECK, Postgres
silently allows INSERT/UPDATE to write a row whose ``niche_id`` is
*not* the current tenant's — i.e. tenant B can write a row labelled
``niche_id='tenant_a'`` and that row will then be visible only to
tenant A. Cross-tenant data injection.

Why this hasn't burned us yet: Phase 1 is single-tenant. The
``app.niche_id`` GUC is always either ``''`` (admin mode — the Python
code's default for every method per SR-A) or matches the caller's
intended niche. So nothing wedges. But:

  * The moment a second tenant's data enters the DB, this becomes a
    privilege-escalation vector.
  * The fix is purely additive (WITH CHECK with the same expression
    as USING) — it changes nothing for admin-mode (GUC='') callers,
    and blocks only the cross-tenant write that was always unsafe.

Approach
========
Use ``ALTER POLICY`` rather than ``DROP + CREATE`` so we don't briefly
expose a window where the policy doesn't exist. The expression mirrors
the existing USING clause exactly:

    ALTER POLICY niche_isolation ON <table>
      WITH CHECK (
        niche_id = current_setting('app.niche_id', true)
        OR current_setting('app.niche_id', true) IN ('', 'all')
        OR current_setting('app.niche_id', true) IS NULL
      );

Tables covered (14, every existing ``niche_isolation`` policy):
  * From a1b2c3d4e5f6: stories, assets
  * From 6034a2c87755: blueprints
  * From e5f6a7b8c9d0: templates, sources
  * From b2c3d4e5f6a7: publishing_analytics, analytics
  * From c3d4e5f6a7b8: content_memory, bandit_arms
  * From d4e5f6a7b8c9: pending_engagement, pending_feedback
  * From j0e1f2g3h4i5: config_updates
  * From l2g3h4i5j6k7: ab_tests, audience_snapshots, monetisationprogress, affiliate_clicks
  * From n4i5j6k7l8m9: affiliate_revenue, preference_data

Not covered by SR-B (separate finding, future SR-F):
  * dashboard_events and product_embeddings have no ``niche_isolation``
    policy at all (created without RLS in n4i5j6k7l8m9). That's a
    larger fix — add policy first, then add WITH CHECK — out of
    scope here.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "t0o1p2q3r4s5"
down_revision = "s9n0o1p2q3r4"
branch_labels = None
depends_on = None


# Mirror exactly the USING clauses already in production. Any drift
# between USING and WITH CHECK would be confusing for operators
# debugging "why did this INSERT silently disappear".
_WITH_CHECK_EXPR = """
    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) IN ('', 'all')
    OR current_setting('app.niche_id', true) IS NULL
"""

_TABLES = (
    "stories",
    "assets",
    "blueprints",
    "templates",
    "sources",
    "publishing_analytics",
    "analytics",
    "content_memory",
    "bandit_arms",
    "pending_engagement",
    "pending_feedback",
    "config_updates",
    "ab_tests",
    "audience_snapshots",
    "monetisationprogress",
    "affiliate_clicks",
    "affiliate_revenue",
    "preference_data",
)


def upgrade() -> None:
    """Add WITH CHECK to all existing niche_isolation policies.

    Idempotent via ALTER POLICY (no DROP/CREATE window). PostgreSQL
    silently no-ops if the policy doesn't exist — but every table in
    _TABLES was created with a niche_isolation policy by an earlier
    migration, so this should always succeed.
    """
    for table in _TABLES:
        op.execute(f"ALTER POLICY niche_isolation ON {table} WITH CHECK ({_WITH_CHECK_EXPR});")


def downgrade() -> None:
    """Drop the WITH CHECK clause from all policies.

    PostgreSQL doesn't have ``ALTER POLICY ... DROP WITH CHECK`` —
    we have to DROP + CREATE the policy without the WITH CHECK clause.
    Mirrors the original USING-only shape.
    """
    using_expr = _WITH_CHECK_EXPR  # same expression
    for table in _TABLES:
        op.execute(
            f"""
            DROP POLICY IF EXISTS niche_isolation ON {table};
            CREATE POLICY niche_isolation ON {table}
              USING ({using_expr});
            """
        )
