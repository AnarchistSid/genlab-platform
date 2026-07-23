"""auto_experiments table for #9 autonomy roadmap scaffold

Revision ID: k8g9h0i1j2k3
Revises: j7f8g9h0i1j2
Create Date: 2026-07-23 23:00:00.000000+00:00

Creates the ``auto_experiments`` table that stores structured
experiment specs extracted from strategist_reports.causal_hypotheses
testable_prediction fields.

Each row represents ONE auto-managed A/B experiment:
* ``source_report_id`` — the strategist report that surfaced the
  hypothesis
* ``hypothesis_index`` — index into that report's causal_hypotheses
  array
* ``spec`` — JSONB carrying arms, niche_id, expected metric shift,
  duration_days
* ``status`` — pending / running / completed / discarded
* ``result`` — populated when status = completed with metric values

Sibling to strategist_reports + learning_findings — same "OBSERVATIONS
auto-flow, ACTIONS need review" discipline: experiments are OBSERVATIONS
that inform future proposals, not ACTIONS that mutate the bandit space
(the ACTION is applying an arm_add after the experiment concludes).

Scaffold-only at this migration: the ``testable_prediction → spec``
parser is a future iteration. The table is created so downstream code
+ dashboards can be built against a stable schema.
"""

from alembic import op

revision = "k8g9h0i1j2k3"
down_revision = "j7f8g9h0i1j2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_experiments (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          source_report_id UUID REFERENCES strategist_reports(id) ON DELETE SET NULL,
          hypothesis_index INTEGER NOT NULL,
          niche_id TEXT NOT NULL,
          spec JSONB NOT NULL DEFAULT '{}'::jsonb,
          status TEXT NOT NULL DEFAULT 'pending' CHECK (
            status IN ('pending', 'running', 'completed', 'discarded')
          ),
          result JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          started_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          notes TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_auto_experiments_status_niche "
        "ON auto_experiments (status, niche_id) WHERE status IN ('pending', 'running')"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_experiments_source_hypo "
        "ON auto_experiments (source_report_id, hypothesis_index)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_auto_experiments_source_hypo")
    op.execute("DROP INDEX IF EXISTS idx_auto_experiments_status_niche")
    op.execute("DROP TABLE IF EXISTS auto_experiments")
