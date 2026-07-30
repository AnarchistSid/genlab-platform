"""rls_deny_by_default — RLS policies deny when app.niche_id is unset (A-0032b)

Revision ID: a0b0c0d0e0f0
Revises: n1i2j3k4l5m6
Create Date: 2026-07-30 18:30:00.000000+00:00

Closes Audit A A-0032b (isolation half of A-0032 split at Wave-0 rotation gate).

## Problem

The 24 niche-scoped RLS policies all shared this fail-open shape:

    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) = ANY (ARRAY['', 'all'])
    OR current_setting('app.niche_id', true) IS NULL     -- THE LEAK

The third disjunct means: when the session variable is unset (`NULL`), the
policy matches EVERY row. Combined with the app connecting as a superuser
role (`genlab`, `rolsuper=t rolbypassrls=t`), RLS was silently no-op
end-to-end. The rotation runbook fixed the role bypass (app now connects
as `genlab_app|f|f`), but Step 5b still returned all 5 niches because the
policy itself remained fail-open.

Empirical evidence at rotation Step 5b (2026-07-30):

    SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id;
    ai_creators | 357
    anime       | 376
    gaming      | 251
    movies      | 340
    sports      | 856

## Fix

Remove the third disjunct from every policy:

    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) = ANY (ARRAY['', 'all'])

Behavior after fix:
- Unset session variable → `current_setting(...)` returns NULL →
  `niche_id = NULL` is NULL (never true) → `NULL = ANY(...)` is NULL →
  ROW REJECTED. Deny by default. This is the SaaS invariant.
- `SET LOCAL app.niche_id = 'gaming'` → returns gaming rows.
- `SET LOCAL app.niche_id = 'all'` (dashboard admin escape hatch) → returns
  all rows. Preserved deliberately for admin/dashboard use.
- `SET LOCAL app.niche_id = ''` (empty string) → also returns all rows.
  Also preserved; it's the same admin escape hatch.

## Scope: 24 policies on 24 tables

All identified via:
    SELECT tablename, policyname FROM pg_policies WHERE schemaname='public';

## Verification gate (post-apply)

Run these as the `genlab_app` role WITHOUT any SET LOCAL:

    SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id;   -- expect: 0 rows
    SELECT COUNT(*) FROM stories;                                  -- expect: 0

Then WITH SET LOCAL:

    BEGIN;
    SET LOCAL app.niche_id = 'gaming';
    SELECT COUNT(DISTINCT niche_id) FROM blueprints;              -- expect: 1
    COMMIT;

Both halves must pass.

## Rollback

`alembic downgrade -1` restores the fail-open third disjunct. This is
DANGEROUS for tenant isolation but preserves cross-niche read access if
the app code isn't yet wired to SET LOCAL per request. If you find missing
SET LOCAL sites post-apply (queries returning 0 unexpectedly), fix the app
code, don't rollback.

## Deployment

CREATE POLICY does NOT hold a table lock; DROP + CREATE inside a single
transaction is atomic per policy. No downtime.

Refs: Audit A A-0032b, .audit/AUDIT_A_REGISTER.yaml, CLAUDE.md rule #33.
"""

from alembic import op


revision = "a0b0c0d0e0f0"
down_revision = "n1i2j3k4l5m6"
branch_labels = None
depends_on = None


# 22 tables carrying the standard `niche_isolation` policy shape.
STANDARD_POLICY_TABLES = [
    "ab_tests",
    "affiliate_revenue",
    "analytics",
    "assets",
    "audience_snapshots",
    "bandit_arms",
    "bandit_validation",
    "blueprints",
    "config_updates",
    "content_memory",
    "email_subscribers",
    "monetisationprogress",
    "pending_engagement",
    "pending_feedback",
    "post_decision_trace",
    "preference_data",
    "publishing_analytics",
    "sources",
    "stories",
    "templates",
    "tier_history",
]

# 3 tables with different policy NAMES but identical shape (verified via
# pg_policies at 2026-07-30). Handled separately so the DROP POLICY names
# match exactly.
NON_STANDARD_NAMED_POLICIES = [
    ("affiliate_clicks", "affiliate_clicks_niche_policy"),
    ("compliance_events", "compliance_events_rls"),
    ("niche_pauses", "niche_pauses_rls"),
]

# The safer USING clause: remove the third fail-open disjunct.
SAFE_USING = (
    "(niche_id = current_setting('app.niche_id', true)) "
    "OR (current_setting('app.niche_id', true) = ANY (ARRAY[''::text, 'all'::text]))"
)

# The prior fail-open USING clause (for down_revision).
FAILOPEN_USING = (
    "(niche_id = current_setting('app.niche_id', true)) "
    "OR (current_setting('app.niche_id', true) = ANY (ARRAY[''::text, 'all'::text])) "
    "OR (current_setting('app.niche_id', true) IS NULL)"
)


def _replace_policy(table: str, policy_name: str, using_clause: str) -> None:
    """Atomically DROP + CREATE a policy inside the current alembic transaction."""
    op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table}"')
    op.execute(
        f'CREATE POLICY "{policy_name}" ON "{table}" '
        f"AS PERMISSIVE FOR ALL "
        f"TO PUBLIC "
        f"USING ({using_clause})"
    )


def upgrade() -> None:
    """Replace fail-open RLS policies with deny-by-default."""
    for table in STANDARD_POLICY_TABLES:
        _replace_policy(table, "niche_isolation", SAFE_USING)
    for table, policy_name in NON_STANDARD_NAMED_POLICIES:
        _replace_policy(table, policy_name, SAFE_USING)


def downgrade() -> None:
    """Restore fail-open policies. Dangerous — see docstring."""
    for table in STANDARD_POLICY_TABLES:
        _replace_policy(table, "niche_isolation", FAILOPEN_USING)
    for table, policy_name in NON_STANDARD_NAMED_POLICIES:
        _replace_policy(table, policy_name, FAILOPEN_USING)
