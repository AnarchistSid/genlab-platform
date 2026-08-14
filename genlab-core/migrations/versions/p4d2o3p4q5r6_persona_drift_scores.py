"""persona_drift_scores (Phase 4.D)

Revision ID: p4d2o3p4q5r6
Revises: p4c1n2o3p4q5
Create Date: 2026-08-14 21:00:00.000000+00:00

LLM-scored persona-fit signal for sampled recent publishes. One
row per (blueprint_id) — same blueprint can only be scored once
so a re-run doesn't inflate the drift-detection count.

  * ``drift_score`` in [0, 1]: 1 = perfect persona match,
    0 = severe drift.
  * ``reasons`` JSONB list of short strings the LLM emitted about
    matches / mismatches.
  * ``persona_hash`` records which persona snapshot was used —
    when the operator edits persona.yaml the hash changes so we
    know old scores were against a different persona (don't mix
    trend lines).

Downstream: runner writes a ``pipeline_alerts`` row when
drift_score < ALERT_THRESHOLD (default 0.6) so the operator sees
it in the alerts banner alongside other prod signals.
"""
from alembic import op

revision = "p4d2o3p4q5r6"
down_revision = "p4c1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS persona_drift_scores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            blueprint_id UUID NOT NULL UNIQUE,
            niche_id TEXT NOT NULL,
            drift_score DOUBLE PRECISION NOT NULL,
            hook_text TEXT,
            persona_hash TEXT,
            reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            llm_cost_usd DOUBLE PRECISION,
            evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_persona_drift_niche_evaluated
        ON persona_drift_scores (niche_id, evaluated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_persona_drift_niche_evaluated")
    op.execute("DROP TABLE IF EXISTS persona_drift_scores")
