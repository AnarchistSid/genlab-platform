"""create gate_examinations table

Revision ID: m9h0i1j2k3l4
Revises: k8g9h0i1j2k3
Create Date: 2026-07-23 23:30:00.000000+00:00

Persists every AutoApprovalGate evaluation with per-check pass/fail
detail. Motivating problem: on prod (2026-07-23 fires) the
auto-approver reports ``examined=1 approved=0 rejected=1`` for every
enabled niche — but the operator can't see WHICH gate check is
rejecting them. That leaves the ratchet stuck without a tuning
target.

This table is the diagnostic layer. Every write happens inside the
auto-approver's main loop after ``gate_evaluate`` returns the
decision (including the strategy adjustment layer's output). Both
approve AND reject rows land, so the ratio of approvals to
examinations is a direct read.

Difference from auto_approval_calibration:
* calibration = ``(gate verdict, operator action)`` pairs; requires
  operator engagement to write.
* gate_examinations = ``(gate verdict, gate detail)`` rows; every
  auto-approver fire produces N rows regardless of operator
  activity.

Design choices:
* passed_checks + failed_checks stored as JSONB arrays. Allows
  aggregation by check name via ``jsonb_array_elements_text`` without
  a separate join table. Trade-off: NoSQL-style analytics, but the
  cardinality is 5 checks × 5 niches × ~2 fires/hour = manageable.
* No RLS. This is diagnostic data the dashboard reads across all
  niches; per-niche isolation would require duplicating the read
  path and only hurts observability without adding a real security
  boundary (blueprint IDs already leak across niches via other
  audit tables).
* No FK to blueprints. Blueprints can be deleted (test fixtures,
  cleanup), and we want the historical record preserved.
"""

from alembic import op

revision = "m9h0i1j2k3l4"
down_revision = "k8g9h0i1j2k3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS gate_examinations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            blueprint_id TEXT NOT NULL,
            niche_id TEXT NOT NULL,
            approved BOOLEAN NOT NULL,
            confidence REAL,
            passed_checks JSONB DEFAULT '[]'::jsonb,
            failed_checks JSONB DEFAULT '[]'::jsonb,
            -- Which caller ran the gate. Currently 'auto_approver_v1'
            -- (the AUTO #2 timer). Nightly_scheduler could adopt the
            -- same log path in future; the column lets us slice by
            -- caller without conflating the two.
            caller_source TEXT NOT NULL DEFAULT 'auto_approver_v1',
            -- Free-form JSON escape hatch. Currently used to record
            -- gate-strategy layer's ``pre_strategy_confidence`` when
            -- strategies adjusted the decision — helps distinguish
            -- rule-based reject from strategy-flipped reject.
            extra JSONB DEFAULT '{}'::jsonb,
            examined_at TIMESTAMPTZ DEFAULT now() NOT NULL
        )
    """)

    # Hot path: dashboard endpoint filters by niche_id + examined_at
    # DESC for the rolling window.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ge_niche_examined
        ON gate_examinations(niche_id, examined_at DESC)
    """)
    # Duplicate-detection: same blueprint gets examined every fire
    # until it's approved or aged out. Keeping the timestamp lets
    # the endpoint dedupe by blueprint for "distinct blueprints
    # examined" counts.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ge_blueprint_examined
        ON gate_examinations(blueprint_id, examined_at DESC)
    """)
    # Aggregation path: WHERE approved = false GROUP BY niche_id +
    # examined_at bucket for the per-day rejection view.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ge_approved_examined
        ON gate_examinations(approved, examined_at DESC)
    """)
    op.execute("GRANT ALL ON gate_examinations TO genlab")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ge_approved_examined")
    op.execute("DROP INDEX IF EXISTS idx_ge_blueprint_examined")
    op.execute("DROP INDEX IF EXISTS idx_ge_niche_examined")
    op.execute("DROP TABLE IF EXISTS gate_examinations")
