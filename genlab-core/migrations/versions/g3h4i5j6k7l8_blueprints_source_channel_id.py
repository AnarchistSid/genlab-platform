"""blueprints.source_channel_id — foundation for the source-discovery proposer

Adds ``source_channel_id TEXT NULL`` to the blueprints table so the
future source-discovery proposer (PR after this) can ask "which
source channels produce the highest-engagement clips on each
niche?" via:

    SELECT b.source_channel_id, COUNT(*) AS clips,
           SUM(a.value) AS total_engagement
    FROM blueprints b
    JOIN analytics a ON a.post_id = b.candidate_id
    WHERE b.source_channel_id IS NOT NULL
      AND b.niche_id = %s
    GROUP BY b.source_channel_id
    ORDER BY total_engagement DESC
    LIMIT 20

## Why on blueprints (not analytics)

A blueprint is created ONCE per published clip with a single source
channel. The analytics table writes MANY rows per post (per metric
type × per collection window). Putting source_channel_id on
blueprints keeps a single source of truth per clip and lets the
proposer query at the right granularity without denormalising.

## Why NULL (not NOT NULL DEFAULT '')

  * Backwards compat with the 1000s of existing blueprints — none
    have source channel data captured.
  * Empty-string default would conflate "unknown channel" with
    "channel that legitimately has an empty ID" — NULL is the
    semantically correct missing-data marker.
  * The proposer's WHERE clause filters ``IS NOT NULL`` to scope
    to new rows that have the data; old rows pass through
    untouched.

## Merge of two alembic heads

This migration's down_revision is a tuple, merging the two
outstanding heads in the repo:

  * p6k7l8m9n0o1 — pipeline_run_costs (PR #183, 2026-06-14)
  * f2g3h4i5j6k7 — niche_pauses_table (PR #575, this session)

Both are deployed on prod. The branch split occurred when the
compliance + niche-pause work landed without rebasing through
p6k7l8m9n0o1's lineage. Merging here resolves alembic's
'multiple heads detected' warning without rewriting either branch.

## Index

Partial index ``WHERE source_channel_id IS NOT NULL`` so the
proposer's GROUP BY scans only populated rows — at Phase A
volumes the populated fraction will be small for weeks until the
producer wire has run on enough new clips.

Revision ID: g3h4i5j6k7l8
Revises: (p6k7l8m9n0o1, f2g3h4i5j6k7) — merge
Create Date: 2026-06-25 22:00:00.000000+00:00
"""

from alembic import op

revision = "g3h4i5j6k7l8"
down_revision = ("p6k7l8m9n0o1", "f2g3h4i5j6k7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE blueprints
        ADD COLUMN IF NOT EXISTS source_channel_id TEXT
        """
    )

    # Partial index — only blueprints with channel data are
    # in the index, keeping it small. Operator can ANALYZE the
    # table to see the actual size after the producer wire has
    # run for some time.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bp_source_channel
            ON blueprints (niche_id, source_channel_id)
            WHERE source_channel_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Drop the index first (CONCURRENTLY would be safer but
    # alembic.op doesn't expose it without raw connection access;
    # at Phase A volumes the table lock is sub-second anyway).
    op.execute("DROP INDEX IF EXISTS idx_bp_source_channel")
    op.execute("ALTER TABLE blueprints DROP COLUMN IF EXISTS source_channel_id")
