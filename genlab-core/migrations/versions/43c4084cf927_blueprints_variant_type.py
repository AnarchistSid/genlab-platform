"""blueprints — add variant_type + variant_payload (Layer 3 foundation)

Revision ID: 43c4084cf927
Revises: 05c64267a3c6
Create Date: 2026-07-17 17:45:00.000000+00:00

Layer 3 kickoff (2026-07-17). See `[[variant-architecture-roadmap]]`.

## What this adds

- ``variant_type TEXT DEFAULT 'single_clip' NOT NULL`` — structural
  variant enum: ``single_clip`` | ``series_part`` | ``split_screen`` |
  ``storytime`` | ``watch_till_end`` | ``question_reveal``. Source of
  truth for allowed values: ``genlab_core.variant_types.VARIANT_TYPES``.
- ``variant_payload JSONB DEFAULT '{}' NOT NULL`` — per-variant fields
  (e.g. ``series_part`` has ``{series_id, part_number, total_parts}``;
  ``split_screen`` has ``{clip_a, clip_b}``). Empty dict for ``single_clip``.

## Why this migration is safe to run under live pipeline

- **NOT NULL with DEFAULT** — every existing row retroactively becomes
  ``variant_type='single_clip'``, ``variant_payload='{}'``. Zero
  behavioral change for any current writer/renderer/publisher path.
- **Additive** — no column dropped, no type change, no constraint on
  existing data beyond the default.
- **Idempotent** — ``ADD COLUMN IF NOT EXISTS``. Re-running is a no-op.
- **Backward-compat in storage layer**: ``BlueprintStore.create_blueprint``
  keeps its graceful-retry-without-unknown-columns pattern so callers
  can pass ``variant_type`` before the migration lands without a hard
  fail; after the migration, the column persists normally.

## Storage choice — column not JSONB extra

``variant_type`` is a TOP-LEVEL column (not stashed in ``blueprints.extra``)
because:
1. Bandit sampling will WHERE-filter on it heavily (`variant:X` arm scoping)
2. Analytics queries aggregate by variant × niche over time windows
3. Matches the pattern set by ``composite_score``, ``virality_score``,
   ``hook_classifier_score`` — all query-critical dimensions get columns

``variant_payload`` is JSONB because per-variant field sets differ
(series_id vs clip_a/clip_b vs question/reveal). Query patterns will
be per-variant so JSONB-extraction cost is acceptable — it's already
the pattern for ``stories.extra`` and ``publishing_analytics.extra``.

## Index

``idx_blueprints_variant_type ON (variant_type, niche_id)`` — supports
the bandit-sampling query "get most-recent rewards per variant per niche"
which is the S5 bandit-extension load pattern. Partial index excludes
``single_clip`` because it's the default and doesn't need indexing until
non-default variants ship in volume.

## Revision ID lesson

Initial attempt used ``a8w9x0y1z2a3`` which collided with the existing
monetization_l3_product_bandit_schema migration. Same class-of-bug the
2026-07-17 merge migration (``05c64267a3c6``) already warned about:
using ``ls | tail`` (lexicographic sort) instead of ``alembic heads``
misses the true chain head. Future migration authors: use
``alembic -c genlab-core/alembic.ini heads`` before setting revision ID
+ down_revision.

## Downgrade

DROP COLUMN — destructive but only removes the variant metadata; every
blueprint still ships as it does today (readers default to single_clip
when the column is absent, via the storage-layer graceful-retry path).
"""

from alembic import op

revision = "43c4084cf927"
down_revision = "05c64267a3c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE blueprints
        ADD COLUMN IF NOT EXISTS variant_type TEXT NOT NULL DEFAULT 'single_clip'
        """
    )
    op.execute(
        """
        ALTER TABLE blueprints
        ADD COLUMN IF NOT EXISTS variant_payload JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    # Partial index — skip the single_clip default (dominates the table)
    # until non-default variants ship. Analytics + bandit queries filter
    # to non-single_clip variants explicitly, so the partial index will
    # actually be used.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_blueprints_variant_type
        ON blueprints (variant_type, niche_id)
        WHERE variant_type <> 'single_clip'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_blueprints_variant_type")
    op.execute("ALTER TABLE blueprints DROP COLUMN IF EXISTS variant_payload")
    op.execute("ALTER TABLE blueprints DROP COLUMN IF EXISTS variant_type")
