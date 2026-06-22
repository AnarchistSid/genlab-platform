"""add episodic_events table (Lever R1 PostgresBackend)

Revision ID: y5t6u7v8w9x0
Revises: x4s5t6u7v8w9
Create Date: 2026-06-22 14:00:00.000000+00:00

Adds the ``episodic_events`` table backing
``genlab_core.learning.episodic_memory.PostgresBackend`` (Lever R1 of
the autonomy roadmap, primitive shipped PR #441, backend shipped
2026-06-22).

The primitive defines an ``EpisodicBackend`` Protocol with two
methods: ``record(event)`` and ``query(**filters)``. The in-memory
test backend already implements the contract; THIS migration lets
the PostgresBackend implement it durably so emit calls from publish /
reward / operator-review / bandit-pick code paths produce queryable
history that survives process restart.

## Schema rationale

- ``id UUID`` primary key with ``gen_random_uuid()`` default — matches
  the rest of the genlab-core tables (blueprints, publishing_analytics,
  bandit_arms, content_pool).
- ``event_type TEXT NOT NULL`` — closed enum per
  ``episodic_memory.coerce_event_type``: publish /
  reward_window_closed / operator_review / operator_edit /
  bandit_pick / experiment_assignment / post_bombed / unknown. NOT
  NULL because every event has a type by construction.
- ``niche_id TEXT NOT NULL`` — tenant scope for RLS-friendly queries.
  Indexed (with timestamp) for the typical "give me last N events
  for niche X" query.
- ``blueprint_id TEXT`` — nullable because some event types (e.g.
  drift snapshots, system_health) aren't blueprint-scoped.
- ``timestamp TIMESTAMPTZ NOT NULL`` — captures when the event
  happened (set by emit-call site, not insert time). Indexed for
  date-window queries.
- ``payload JSONB NOT NULL DEFAULT '{}'::jsonb`` — schemaless
  per-event-type data. The primitive's dataclass has ``payload: dict``
  with default ``{}``.
- ``created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`` — when the row
  was inserted. Useful for diagnosing emit-lag (timestamp vs
  created_at gap = how stale the event was at insert time).

## Indexes

- ``idx_episodic_niche_ts`` on ``(niche_id, timestamp DESC)`` —
  primary query pattern for the dashboard ("show me last 24h of
  events for gaming").
- ``idx_episodic_type`` on ``event_type`` — secondary scan for
  cross-niche aggregations ("how many publishes today across all
  niches").

The composite (niche_id, timestamp DESC) is the highest-leverage
index because every dashboard query filters by niche FIRST, then
time-windows. PostgreSQL btree handles DESC efficiently for the
``ORDER BY timestamp DESC LIMIT N`` access pattern from
``filter_events(... limit=N)``.

## Cost

Insert: <1ms per event (uuid + 4 short columns + jsonb).
Query: indexed; lookback windows of ~1 week at ~50 events/day per
niche = ~350 rows scanned per filter — milliseconds.
Storage: ~1KB per event; 5 niches × ~50 events/day × 365 days = ~90MB/yr.

## Rollback

``downgrade()`` drops both indexes + the table. No data preserved.
Safe to roll back as long as no code path is reading from the table
yet — PostgresBackend ships separately in the same PR but is opt-in
via DATABASE_URL availability; no caller in genlab-core emits events
until a follow-up wire PR.
"""

from __future__ import annotations

from alembic import op

# Revision identifiers, used by Alembic.
revision = "y5t6u7v8w9x0"
down_revision = "x4s5t6u7v8w9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS episodic_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type TEXT NOT NULL,
            niche_id TEXT NOT NULL,
            blueprint_id TEXT,
            "timestamp" TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_episodic_niche_ts
        ON episodic_events (niche_id, "timestamp" DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_episodic_type
        ON episodic_events (event_type)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_episodic_type")
    op.execute("DROP INDEX IF EXISTS idx_episodic_niche_ts")
    op.execute("DROP TABLE IF EXISTS episodic_events")
