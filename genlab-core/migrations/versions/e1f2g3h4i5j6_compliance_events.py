"""compliance_events table — foundation for the full-autonomy moat

Revision ID: e1f2g3h4i5j6
Revises: d0e1f2g3h4i5
Create Date: 2026-06-25 16:00:00.000000+00:00

PR #566 (2026-06-25): the foundation for Phase A of the
full-autonomy-with-platform-safety roadmap (PRs #566-#570).

## Why this matters

Every autonomy expansion (auto-approval enforcement, auto-source-
discovery, per-platform product variation, etc.) increases the
risk of platform bans / shadowbans / strikes. Without an audit
log of every compliance decision, when something goes wrong we
can't reconstruct WHY a publish was blocked or WHY an account
got flagged. compliance_events is that audit log.

The table also drives the upcoming Mission Control dashboard
card that shows operators "your last 50 compliance decisions"
so they can spot drift before a strike lands.

## Schema rationale

  id UUID PK — durable identity for the row
  niche_id TEXT NOT NULL — RLS scope, matches existing
                          per-niche isolation pattern
  blueprint_id UUID NULL — nullable because some events
                           aren't blueprint-tied (e.g.
                           account-health alerts fire even
                           when no specific post is being
                           checked)
  platform TEXT NULL — nullable for cross-platform events
                       (e.g. caption_diversity_check runs
                       per-platform; account_health is per-
                       platform-account, but spam_pattern_
                       detected for an entire niche has
                       platform=NULL)
  event_type TEXT NOT NULL — closed enum, expanded as new
                              checks land:
    'pre_publish_check'   — policy_gate decision logged
    'ai_disclosure_added' — PR #567 will produce these
    'copyright_flag'      — PR #568 will produce these
    'spam_pattern_detected' — PR #569 will produce these
    'account_health_warning' — PR #570 will produce these
    'auto_publish_block'  — when enforcement starts blocking
    'manual_override'     — operator overrode a block
  decision TEXT NOT NULL — 'allow' | 'block' | 'warn'
    * 'allow': policy did not fire (or fired with allow)
    * 'warn': policy fired but observation-only (today's
              default — log it, don't block)
    * 'block': policy fired AND enforcement is on for this
               (niche, event_type) — publish was prevented
  reasons JSONB NOT NULL DEFAULT '[]'::jsonb — list of
    rule-name strings explaining the decision, e.g.
    ['caption_n_gram_overlap_above_70%',
     'hashtag_set_repeated_3_times']
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb — per-event-
    type detail. For 'copyright_flag': {fingerprint,
    matched_against_id}. For 'spam_pattern_detected':
    {n_gram_overlap_pct, last_n_captions_checked}.
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

### Indexes

  idx_compliance_niche_ts on (niche_id, created_at DESC) —
    primary dashboard query: "show me last 50 events for
    gaming"
  idx_compliance_event_type on (event_type) — cross-niche
    aggregations: "how many copyright flags fired across
    all niches this week"
  idx_compliance_blueprint on (blueprint_id) — partial
    index WHERE blueprint_id IS NOT NULL — drives "show
    me the compliance history for this specific blueprint"

## RLS

Applied via standard niche_id scope (matches the 16-table
convention from PR #535's SR-B audit). Operator-mode admin
escape preserved.

## Observation-only mode (PR #566 default)

This PR ships the SCHEMA + the logging helper + the policy_
gate skeleton. No callsite consumes policy_gate yet. The
default for every check is decision='warn' (logged, but
publish proceeds). Each subsequent PR (#567-#570) adds one
real check and decides per-niche/per-check whether to flip
to 'block'. The data layer logs everything from day one so
when enforcement activates, operators have weeks of
historical context to compare against.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e1f2g3h4i5j6"
down_revision = "d0e1f2g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            blueprint_id UUID,
            platform TEXT,
            event_type TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compliance_niche_ts
        ON compliance_events (niche_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compliance_event_type
        ON compliance_events (event_type)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compliance_blueprint
        ON compliance_events (blueprint_id)
        WHERE blueprint_id IS NOT NULL
        """
    )

    # RLS policy — same shape as the 16-table standard from PR #535's
    # SR-B audit. Admin-mode escape preserved.
    op.execute("ALTER TABLE compliance_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY compliance_events_rls ON compliance_events
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        )
        WITH CHECK (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS compliance_events_rls ON compliance_events")
    op.execute("ALTER TABLE compliance_events DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS idx_compliance_blueprint")
    op.execute("DROP INDEX IF EXISTS idx_compliance_event_type")
    op.execute("DROP INDEX IF EXISTS idx_compliance_niche_ts")
    op.execute("DROP TABLE IF EXISTS compliance_events")
