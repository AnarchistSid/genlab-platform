"""intelligent transformation — multi-dimensional bandit schema

Revision ID: a7v8w9x0y1z2
Revises: z6u7v8w9x0y1
Create Date: 2026-07-05 20:15:00.000000+00:00

Foundational schema for the intelligent transformation architecture
(see memory: intelligent-transformation-architecture-2026-07-05).

The transformation stack registers 11 render-time choice dimensions
(music_mood, caption_style, hook_framing, intro_animation, outro_cta,
pan_zoom, highlight_moment, audio_ducking, music_visual_sync,
caption_emphasis_color, caption_pacing) as bandit arms per niche.
Each published reel picks one arm per dimension; reward attribution
routes per-window retention signals to the specific dimension's arm
posterior.

Two schema additions:

1. ``pending_feedback.arm_ids_by_dimension JSONB``
   Multi-arm attribution needs to carry N arm assignments per reel
   between publish and metric-fetch. Existing ``arm_id`` (TEXT) is
   single-arm; leave untouched for the legacy content-type arm.
   New column is JSONB dict: {"music_mood": "arm_id", "caption_style":
   "arm_id", ...}. When collector fires at 6h/24h/48h/168h, reward
   iterates this dict and updates each arm's posterior with the
   window-appropriate signal.

   Default '{}' — legacy publishes (transformation off, or before
   this migration) get empty dict, no multi-arm routing occurs.

2. ``bandit_arms.arm_type TEXT`` + ``bandit_arms.dimension TEXT`` +
   ``bandit_arms.dimension_value TEXT``
   Enables efficient queries by arm class without parsing the
   composite ``arm_id`` string. Existing arms migrate as follows:
   - ``arm_type='content'``  — historical content-type arms (default)
   - ``arm_type='transformation'`` — new arms added by this sprint
   - ``arm_type='hour'`` — per-hour bandit arms (`hour:H:platform:niche`)
   - ``arm_type='source'`` — per-source arms (`source:name` or
     `source:name__platform`)
   - ``arm_type='style'`` — hook-style arms (`style:niche:name`)

   For transformation arms only:
   - ``dimension``       — dimension name (e.g. 'music_mood')
   - ``dimension_value`` — chosen value (e.g. 'cinematic')

   Existing rows get ``arm_type=NULL`` — a follow-up backfill script
   (see PR #TBD) categorizes them based on arm_id prefix. This
   migration is backward-compatible: legacy code paths that read
   ``arm_id`` continue to work; only new consumers read the new
   columns.

Indexes:
- ``idx_ba_arm_type_niche`` — filter "all X-type arms for niche Y"
- ``idx_ba_dimension_niche`` — "all arms of dimension X for niche Y"

Storage choice — TOP-LEVEL COLUMNS not JSONB:
Following the pattern set by `hook_classifier_score` (migration
`z6u7v8w9x0y1`), transformation arms are queried analytically
by dimension frequently (e.g. Mission Control cards, Strategist
weekly meta-analysis). Top-level columns are queryable via SQL
without JSONB extraction overhead.

FrameDrift (anime) inclusion:
Operator authorized on 2026-07-05 that FrameDrift is included in
the transformation sprint (5 niches, not 4). Schema is niche-
agnostic; per-niche activation is via flag + config not migration.

Related:
- [[intelligent-transformation-architecture-2026-07-05]] — full design
- [[zero-cost-transformation-stack-2026-07-05]] — render stack
- [[sprint-intelligent-transformation-commit-2026-07-05]] — commit
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7v8w9x0y1z2"
down_revision: str | Sequence[str] | None = "z6u7v8w9x0y1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pending_feedback — multi-arm attribution carrier
    op.execute(
        "ALTER TABLE pending_feedback "
        "ADD COLUMN IF NOT EXISTS arm_ids_by_dimension JSONB DEFAULT '{}'::jsonb"
    )

    # bandit_arms — arm classification columns
    op.execute("ALTER TABLE bandit_arms ADD COLUMN IF NOT EXISTS arm_type TEXT")
    op.execute("ALTER TABLE bandit_arms ADD COLUMN IF NOT EXISTS dimension TEXT")
    op.execute("ALTER TABLE bandit_arms ADD COLUMN IF NOT EXISTS dimension_value TEXT")

    # Indexes — analytical query paths (Mission Control cards, Strategist)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ba_arm_type_niche "
        "ON bandit_arms (arm_type, niche_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ba_dimension_niche "
        "ON bandit_arms (dimension, niche_id) "
        "WHERE dimension IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ba_dimension_niche")
    op.execute("DROP INDEX IF EXISTS idx_ba_arm_type_niche")
    op.execute("ALTER TABLE bandit_arms DROP COLUMN IF EXISTS dimension_value")
    op.execute("ALTER TABLE bandit_arms DROP COLUMN IF EXISTS dimension")
    op.execute("ALTER TABLE bandit_arms DROP COLUMN IF EXISTS arm_type")
    op.execute("ALTER TABLE pending_feedback DROP COLUMN IF EXISTS arm_ids_by_dimension")
