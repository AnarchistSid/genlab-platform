"""strategist_outcome_verification (Phase 1.A of the Genius Program)

Revision ID: p0d1a2b3c4d5
Revises: l3mon202608
Create Date: 2026-08-14 12:00:00.000000+00:00

Backs the outcome-verifier runner + auto-rollback loop shipped in
docs/GENIUS-PROGRAM-ROADMAP.md § Phase 1.A.

## What this closes

Today's flow: strategist proposes → operator/auto-accepts → apply
worker materialises → nothing checks whether the change actually
helped. Bad decisions persist forever, degrading the learning signal.

This table gives every applied proposal a 48h post-check row. If the
metric the proposal claimed to move actually MOVED (better),
`verdict=improved`. If it regressed, `verdict=regressed` and
`rollback_recommended=true` triggers reverse-SQL in the runner.

## Shape

  * ``proposal_id`` — ``{strategist_reports.id}:{proposals[idx]}``
    stable identifier. Non-UUID because it composes report UUID + int.
  * ``applied_at`` — set at register-time by the apply worker
  * ``metric_name`` — what the proposal claimed to move
    (e.g. ``arm_reward:anime:hook_type:anime:character_debate``)
  * ``baseline_value`` — metric snapshot at applied_at
  * ``t_plus_48h_value`` — metric snapshot at applied_at + 48h
  * ``verdict`` — pending/improved/unchanged/regressed
  * ``rollback_recommended`` — bool, informs the rollback SQL path
  * ``operator_notes`` — free-form override slot

Idempotency via ``proposal_id`` UNIQUE. Runner uses ON CONFLICT
DO NOTHING so a re-fire of apply worker won't create dupes.

Downgrade is destructive: DROP wipes all verification history.
"""

from alembic import op

revision = "p0d1a2b3c4d5"
down_revision = "l3mon202608"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategist_outcome_verification (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          proposal_id           TEXT NOT NULL,
          proposal_type         TEXT NOT NULL,
          proposal_target       TEXT NOT NULL,
          niche_id              TEXT NOT NULL,
          applied_at            TIMESTAMPTZ NOT NULL,
          metric_name           TEXT NOT NULL,
          baseline_value        DOUBLE PRECISION,
          t_plus_48h_value      DOUBLE PRECISION,
          verdict               TEXT NOT NULL DEFAULT 'pending',
          rollback_recommended  BOOLEAN NOT NULL DEFAULT FALSE,
          operator_notes        TEXT NOT NULL DEFAULT '',
          created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT strategist_outcome_verification_proposal_unique
            UNIQUE (proposal_id)
        )
        """
    )
    # Index for the runner's "find pending records older than 48h" query
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategist_outcome_pending
        ON strategist_outcome_verification (verdict, applied_at)
        WHERE verdict = 'pending'
        """
    )
    # Index for the dashboard's "recent verdicts per niche" query
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategist_outcome_niche_recent
        ON strategist_outcome_verification (niche_id, applied_at DESC)
        """
    )
    # Row-level security: per-niche isolation (matches other genlab tables)
    op.execute(
        "ALTER TABLE strategist_outcome_verification ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY strategist_outcome_verification_niche_isolation
        ON strategist_outcome_verification
        USING (
          niche_id = current_setting('app.niche_id', true)
          OR current_setting('app.niche_id', true) = 'all'
          OR current_setting('app.niche_id', true) IS NULL
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS strategist_outcome_verification CASCADE"
    )
