"""strategist tables (PR Strategist-1b)

Revision ID: y6t7u8v9w0x1
Revises: x5s6t7u8v9w0
Create Date: 2026-07-01 00:30:00.000000+00:00

Adds three tables that back the Strategist meta-layer (docs/STRATEGIST-SPEC.md §3):

1. **strategist_reports** — one row per (niche, week) Strategist run.
   Stores LLM-generated phase detection, proposals, causal hypotheses,
   and operator-review state (accepted/rejected proposals + notes).
   The complete `inputs_json` snapshot is persisted so runs are
   reproducible offline.

2. **learning_findings** — surface for hypotheses the operator has
   approved as real patterns. Writer prompts read the top-N active
   findings per niche as inline context, giving the LLM specific
   patterns to lean on instead of guessing.

3. **universal_playbook** — cross-niche patterns confirmed across ≥2
   niches. Used to seed priors for new niches at launch (instead of
   starting from scratch) and to inform writer prompts globally.

The `extra` JSONB columns and `superseded_by` self-references are
deliberate — they let the Strategist evolve its proposal shapes
without future migrations, and let findings be soft-replaced over
time without deleting history.

Downgrade is destructive: DROP TABLE wipes all reports. Operator
data loss is the cost of rollback; the alternative (preserving on
downgrade) leaves orphaned rows when the column shapes change.
"""

from alembic import op

revision = "y6t7u8v9w0x1"
down_revision = "x5s6t7u8v9w0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- strategist_reports ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategist_reports (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          niche_id        TEXT NOT NULL,
          run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          week_of         DATE NOT NULL,

          -- Input state snapshot (reproducibility)
          inputs_json     JSONB NOT NULL DEFAULT '{}'::jsonb,

          -- LLM-generated structured output
          detected_phase  TEXT NOT NULL,
          phase_evidence  TEXT NOT NULL,
          proposals       JSONB NOT NULL DEFAULT '[]'::jsonb,
          causal_hypotheses JSONB NOT NULL DEFAULT '[]'::jsonb,
          universal_playbook_proposals JSONB NOT NULL DEFAULT '[]'::jsonb,
          weekly_summary  TEXT NOT NULL,

          -- LLM telemetry
          cost_usd        DOUBLE PRECISION,
          input_tokens    INTEGER,
          output_tokens   INTEGER,

          -- Operator review state
          reviewed_at     TIMESTAMPTZ,
          reviewed_by     TEXT,
          proposals_accepted JSONB,
          proposals_rejected JSONB,
          operator_notes  TEXT,

          -- Future-proofing for proposal-shape evolution
          extra           JSONB NOT NULL DEFAULT '{}'::jsonb,

          CONSTRAINT week_unique_per_niche UNIQUE (niche_id, week_of)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_strategist_reports_run_at ON strategist_reports(run_at DESC)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategist_reports_unreviewed
        ON strategist_reports(niche_id, run_at DESC)
        WHERE reviewed_at IS NULL
        """
    )

    # --- learning_findings ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_findings (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          niche_id        TEXT NOT NULL,
          finding_text    TEXT NOT NULL,
          evidence_count  INTEGER NOT NULL DEFAULT 1,
          source          TEXT NOT NULL DEFAULT 'strategist',
          source_report_id UUID REFERENCES strategist_reports(id) ON DELETE SET NULL,
          active          BOOLEAN NOT NULL DEFAULT true,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          superseded_by   UUID REFERENCES learning_findings(id) ON DELETE SET NULL,

          CONSTRAINT valid_source CHECK (source IN ('strategist', 'manual', 'analyst'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_findings_active ON learning_findings(niche_id, active) WHERE active = true"
    )

    # --- universal_playbook ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS universal_playbook (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          pattern_text    TEXT NOT NULL,
          evidence_niches TEXT[] NOT NULL,
          confidence      TEXT NOT NULL,
          active          BOOLEAN NOT NULL DEFAULT true,
          source_report_id UUID REFERENCES strategist_reports(id) ON DELETE SET NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

          CONSTRAINT valid_confidence CHECK (confidence IN ('high', 'medium', 'low')),
          CONSTRAINT min_niches CHECK (array_length(evidence_niches, 1) >= 2)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_playbook_active ON universal_playbook(active) WHERE active = true"
    )


def downgrade() -> None:
    # Destructive on rollback — operator data loss accepted as cost of
    # downgrade. See module docstring.
    op.execute("DROP TABLE IF EXISTS universal_playbook")
    op.execute("DROP TABLE IF EXISTS learning_findings")
    op.execute("DROP TABLE IF EXISTS strategist_reports")
