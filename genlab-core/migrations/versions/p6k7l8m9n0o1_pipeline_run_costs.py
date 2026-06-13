"""create pipeline_run_costs table

Persists per-run cost summaries to Postgres so the operator can
answer "what did I spend on gaming this week" without grepping 50
run_report.json files.

The CostAccumulator (genlab_core.intelligence.cost_accumulator) tracks
costs in contextvars per-run and dumps a summary into run_report.json.
That dump never leaves the per-run filesystem; this table makes the
summary queryable cross-run, cross-niche.

Revision ID: p6k7l8m9n0o1
Revises: o5j6k7l8m9n0
Create Date: 2026-06-14 00:00:00.000000+00:00
"""

from alembic import op

revision = "p6k7l8m9n0o1"
down_revision = "o5j6k7l8m9n0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_run_costs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- (run_id, niche_id) is the natural key; one row per pipeline
            -- run per niche. Unique-indexed below to make idempotent
            -- re-runs an UPDATE rather than a duplicate insert.
            run_id TEXT NOT NULL,
            niche_id TEXT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Headline numbers. Most reporting queries will scan
            -- (niche_id, completed_at) and aggregate over total_usd.
            total_usd NUMERIC(10, 4) NOT NULL DEFAULT 0,
            -- Per-category split for "where did the $ go". Mirrors
            -- CostAccumulator.by_category — llm / image / tts / compute /
            -- media / bandwidth. NULL means no category-tagged entries.
            llm_usd NUMERIC(10, 4) DEFAULT 0,
            image_usd NUMERIC(10, 4) DEFAULT 0,
            tts_usd NUMERIC(10, 4) DEFAULT 0,
            compute_usd NUMERIC(10, 4) DEFAULT 0,
            media_usd NUMERIC(10, 4) DEFAULT 0,
            bandwidth_usd NUMERIC(10, 4) DEFAULT 0,
            -- Per-model JSONB: {"claude-haiku-4-5": 0.0123, "gpt-4o-mini": 0.0004, ...}
            -- Use jsonb so dashboard queries can drill into model-level spend
            -- without re-reading run_report.json.
            by_model JSONB DEFAULT '{}'::jsonb,
            -- Bookkeeping for budget-overrun analysis. NULL when no budget
            -- was configured for the run.
            budget_usd NUMERIC(10, 4),
            budget_remaining_pct NUMERIC(5, 2),
            -- Count of CostAccumulator entries (raw LLM/image/tts calls).
            -- Useful for "average $/call this week" queries.
            entry_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Hot path: dashboard rollup queries scan by (niche, time).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_prc_niche_completed
        ON pipeline_run_costs(niche_id, completed_at DESC)
    """)
    # Idempotent re-run protection: same (run_id, niche_id) → UPSERT path.
    # Without this a retried RunReport would write duplicate rows.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_prc_run_niche
        ON pipeline_run_costs(run_id, niche_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_prc_run_niche")
    op.execute("DROP INDEX IF EXISTS idx_prc_niche_completed")
    op.execute("DROP TABLE IF EXISTS pipeline_run_costs")
