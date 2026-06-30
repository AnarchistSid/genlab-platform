"""post_decision_trace: canonical per-post record for bandit analysis

Captures (bandit score, gate prediction, operator decision, actual
engagement) per blueprint in a SINGLE row. Downstream analysis
(drift detection, exploration calibration, AUTO #2 readiness check,
the bandit_validation harness) can draw from one canonical source
instead of re-joining 4 tables (blueprints, auto_approval_calibration,
publishing_analytics, analytics) every time.

Per-blueprint upsert
====================

The row is enriched across 3 lifecycle events:

  1. At push time (PushToBacklog stage in push_to_backlog.py) —
     write bandit_arm_id + bandit_predicted_score + bandit_confidence.

  2. At operator-decision time (calibration_helper.py wire) — UPSERT
     gate_verdict + gate_confidence + operator_action.

  3. At engagement window completion (metric_collector.py at the 24h
     and 168h windows) — UPSERT engagement metrics + computed_reward.

All writes UPSERT on blueprint_id PK so the row is enriched, not
duplicated. ON CONFLICT DO UPDATE only updates fields the new event
brings — older values are preserved (e.g. the operator's decision
event must not clobber the bandit fields from step 1).

Why a separate table (not a JSONB column on blueprints)
========================================================

* The 3 events fire at very different times (push: hours before
  publish; decision: minutes after push; engagement: 24h+168h after
  publish). Mutating blueprints.extra from 3 paths at 3 cadences
  invites lost-update races.
* Analysis queries want a flat schema: "show me posts where
  bandit_predicted_score > 0.7 AND operator_action = 'rejected'"
  is one WHERE clause, not a JSON traversal.
* Migrations: adding a new lifecycle field is one ALTER TABLE here,
  not a JSONB schema migration that requires backfill.

Storage shape
=============

  blueprint_id              uuid PK (also the join key to blueprints)
  niche_id                  text — RLS scope
  published_at              timestamptz — when publishing_analytics
                            recorded the SUCCESS; used to window
                            analysis queries
  bandit_arm_id             text — the arm the picker chose
                            (e.g. 'source:youtube_trending',
                            'gameplay_clip')
  bandit_predicted_score    real — Beta posterior mean of the
                            per-source arm at push time (NOT the
                            generic content arm — the source arm
                            is the bandit_validation key)
  bandit_confidence         real — IPS softmax weight from LinUCB
                            picker (NULL when non-LinUCB path)
  gate_verdict              boolean — auto_approval_gate.evaluate's
                            approved field at operator-review time
  gate_confidence           real
  operator_action           text — approved / rejected / revised /
                            skipped
  engagement_reach_24h      bigint
  engagement_reach_168h     bigint
  engagement_likes          bigint
  computed_reward           real — RewardShaper.compute_reward output
                            at the 168h window (the final reward
                            value the bandit was actually trained on)

Indexes
=======

  idx_pdt_niche_published — primary read pattern is
    "give me the last N days of trace rows for niche X" — composite
    on (niche_id, published_at DESC).

  No index on bandit_arm_id alone — analysis queries on arm always
  also filter niche_id (RLS pattern), so the composite index above
  covers it.

RLS
===

Standard niche_isolation — matches publishing_analytics,
analytics, bandit_arms, bandit_validation.

Revision ID: x5s6t7u8v9w0
Revises: w4r5s6t7u8v9
Create Date: 2026-06-30 19:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "x5s6t7u8v9w0"
down_revision: str | Sequence[str] | None = "w4r5s6t7u8v9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the post_decision_trace table + index + RLS."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS post_decision_trace (
            blueprint_id UUID PRIMARY KEY,
            niche_id TEXT NOT NULL,
            published_at TIMESTAMPTZ,
            bandit_arm_id TEXT,
            bandit_predicted_score REAL,
            bandit_confidence REAL,
            gate_verdict BOOLEAN,
            gate_confidence REAL,
            operator_action TEXT,
            engagement_reach_24h BIGINT,
            engagement_reach_168h BIGINT,
            engagement_likes BIGINT,
            computed_reward REAL,
            created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pdt_niche_published
        ON post_decision_trace (niche_id, published_at DESC NULLS LAST)
        """
    )

    op.execute("ALTER TABLE post_decision_trace ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE post_decision_trace FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS niche_isolation ON post_decision_trace")
    op.execute(
        """
        CREATE POLICY niche_isolation ON post_decision_trace
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

    op.execute("GRANT ALL ON post_decision_trace TO genlab")


def downgrade() -> None:
    """Drop the post_decision_trace table + index."""
    op.execute("DROP INDEX IF EXISTS idx_pdt_niche_published")
    op.execute("DROP TABLE IF EXISTS post_decision_trace CASCADE")
