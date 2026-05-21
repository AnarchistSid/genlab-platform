"""bandit_arms: replace UNIQUE(arm_id) with UNIQUE(niche_id, arm_id)

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-05-17 20:45:00.000000+00:00

The original c3d4e5f6a7b8 migration declared bandit_arms.arm_id with an
inline ``UNIQUE`` clause.  Postgres auto-named the resulting constraint
``bandit_arms_arm_id_key`` and the backing index doubled as the lookup
index for ``WHERE arm_id = ...`` queries.

This worked because arm names happen to be niche-scoped by convention:
``gameplay_clip`` (gaming), ``highlight_play`` (sports), ``cast_reveal``
(movies), ``fight_scene`` (anime), ``ai_explainer`` (ai_creators).  The
hook-style arms added on 2026-05-17 use an explicit ``style:{niche}:{name}``
prefix for the same reason — they would collide otherwise.

The migration converts the constraint to ``UNIQUE(niche_id, arm_id)`` so:
  1. Future arm names that don't carry a niche prefix can't accidentally
     collide across niches.
  2. The RLS-isolated query pattern (set_config('app.niche_id') + scan)
     still gets a fast composite index for free.

No data migration is needed — verified pre-migration that no
arm_id appears in more than one niche on production (5 niches × 9 arms
= 45 rows, all niche-scoped).
"""
from alembic import op

revision = "i9d0e1f2g3h4"
down_revision = "h8c9d0e1f2g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safety check inside the migration itself — if any row violates the new
    # constraint, fail loudly with a useful error rather than letting the
    # CREATE CONSTRAINT raise a generic uniqueness violation.
    op.execute("""
    DO $$
    DECLARE
        collision_count INTEGER;
    BEGIN
        SELECT COUNT(*) INTO collision_count
        FROM (
            SELECT arm_id
            FROM bandit_arms
            GROUP BY arm_id
            HAVING COUNT(DISTINCT niche_id) > 1
        ) AS dupes;

        IF collision_count > 0 THEN
            RAISE EXCEPTION
                'Cannot apply UNIQUE(niche_id, arm_id): % arm_id(s) exist '
                'in multiple niches. Resolve manually before migrating.',
                collision_count;
        END IF;
    END $$;
    """)

    # Drop the old single-column UNIQUE constraint (also drops its index).
    op.execute("ALTER TABLE bandit_arms DROP CONSTRAINT bandit_arms_arm_id_key")

    # Add the composite UNIQUE constraint.  Postgres auto-creates a
    # backing UNIQUE B-tree index on (niche_id, arm_id), which serves
    # as the lookup index for the niche-scoped queries the app uses.
    op.execute(
        "ALTER TABLE bandit_arms "
        "ADD CONSTRAINT bandit_arms_niche_id_arm_id_key "
        "UNIQUE (niche_id, arm_id)"
    )


def downgrade() -> None:
    """Roll back to the single-column UNIQUE(arm_id) constraint.

    Will fail if any cross-niche arm_id collisions have been introduced
    since the upgrade — same DO block pattern in reverse.
    """
    op.execute("""
    DO $$
    DECLARE
        collision_count INTEGER;
    BEGIN
        SELECT COUNT(*) INTO collision_count
        FROM (
            SELECT arm_id
            FROM bandit_arms
            GROUP BY arm_id
            HAVING COUNT(*) > 1
        ) AS dupes;

        IF collision_count > 0 THEN
            RAISE EXCEPTION
                'Cannot revert to UNIQUE(arm_id): % duplicate arm_id(s) '
                'exist across niches. Drop or rename rows before downgrading.',
                collision_count;
        END IF;
    END $$;
    """)

    op.execute(
        "ALTER TABLE bandit_arms DROP CONSTRAINT bandit_arms_niche_id_arm_id_key"
    )
    op.execute(
        "ALTER TABLE bandit_arms ADD CONSTRAINT bandit_arms_arm_id_key UNIQUE (arm_id)"
    )
