"""operator_briefings (Phase 5.D)

Revision ID: q6d6s7t8u9v0
Revises: p5c5r6s7t8u9
Create Date: 2026-08-15 00:20:00.000000+00:00

Daily LLM-synthesized briefing for the operator. Runner fires at
06:00 UTC via ``genlab-operator-briefing.timer`` — collects
mission-control state (pending flag flips, calibration progress,
yesterday's publishes, alerts) + Anthropic Haiku writes a 5-line
"what needs your judgment" summary + delivered via email.

Row shape:
  * ``summary_md`` — the LLM's 5-line rendered summary (Markdown).
    Also displayed on the dashboard card.
  * ``structured`` JSONB — raw aggregate the LLM saw, for
    provenance + card drill-in.
  * ``email_sent`` — True when OutlookMailSender.send() returned
    ok. False + a reason when it failed OR was skipped (no UPN
    configured, budget gate hit).
  * ``llm_cost_usd`` — recorded from ``CallResult.cost_usd`` so
    the daily briefing shows up on the cost card too.
  * ``n_pending_flag_flips`` / ``n_pending_strategist_proposals``
    — flat scalars useful for the card's "N items need review"
    badge without re-parsing the structured payload.
"""
from alembic import op

revision = "q6d6s7t8u9v0"
down_revision = "p5c5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_briefings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            summary_md TEXT NOT NULL,
            structured JSONB NOT NULL DEFAULT '{}'::jsonb,
            email_sent BOOLEAN NOT NULL DEFAULT FALSE,
            email_recipient TEXT,
            email_error TEXT,
            llm_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            n_pending_flag_flips INTEGER NOT NULL DEFAULT 0,
            n_pending_strategist_proposals INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_operator_briefings_generated_at
        ON operator_briefings (generated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_operator_briefings_generated_at")
    op.execute("DROP TABLE IF EXISTS operator_briefings")
