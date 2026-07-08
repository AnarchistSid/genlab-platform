"""blueprints — CHECK constraint: approved+scheduled requires visual_paths (#577)

Revision ID: d0z1a2b3c4d5
Revises: c0z1a2b3c4d5, c9x0y1z2ab34
Create Date: 2026-07-08 16:50:00.000000+00:00

## Also a mergepoint

Prod had 2 unmerged heads: ``c9x0y1z2ab34`` (2026-07-05 mergepoint
of transformation + late_reward) and ``c0z1a2b3c4d5`` (the L3 chain
which extended late_reward but never merged with transformation).
Applying THIS migration also unifies those 2 heads — the down_revision
tuple declares both as parents, following the same pattern used in
``c9x0y1z2ab34_merge_late_reward_and_transformation.py`` and
``b8w9x0y1z2ab_merge_transformation_and_strategist.py``.

After this migration applies, ``d0z1a2b3c4d5`` is the single head
and future migrations descend cleanly from it.

## Background

Task #576's investigation revealed a recurring class of bug: operator
manual ``psql UPDATE`` statements setting
``action_taken='approved' + scheduled_for=<future>`` on blueprints
that lack rendered media in ``extra.visual_paths``. Publisher then
fails at fire time with ``No valid media files for video publish``.

Pattern history (all `action_taken='approved' AND reviewed_at IS NULL`
which is the fingerprint of raw-SQL bypasses):

  * 2026-03-17 — 1 blueprint (1 niche)
  * 2026-06-29 — 6 blueprints (2 niches)
  * 2026-07-04 — 1 blueprint (1 niche)
  * 2026-07-05 — 1 blueprint (1 niche)

Task #575 shipped a dashboard-layer guard in
``_execute_review_action`` that prevents the class from recurring via
the API path. But raw ``psql UPDATE`` on prod bypasses the guard.

This constraint closes the raw-SQL gap by enforcing the invariant at
the database layer. Any future write that sets
``action_taken='approved' + scheduled_for=<any>`` on a video-format
blueprint MUST also carry a non-empty ``extra.visual_paths`` value,
or the write is rejected.

## Why NOT VALID

24 historical ``PUBLISHED`` blueprints violate the invariant — they
were approved+scheduled with no visual_paths in extra (either the
field was cleared post-publish, or an older pipeline stored the path
elsewhere). Those rows are terminal and never change. ``NOT VALID``
tells Postgres: "trust me, don't back-scan; enforce only on future
INSERT/UPDATE." An optional ``ALTER TABLE ... VALIDATE CONSTRAINT``
in a later migration can back-scan once the historical rows are
cleaned up.

## Visual_paths shape

Prod stores ``extra.visual_paths`` as a JSON-encoded STRING, not a
native JSONB array. All 511 rows with the key use ``jsonb_typeof =
'string'``. The check tolerates both:

  * String form: ``extra->>'visual_paths'`` returns the JSON text.
    We compare against ``''``, ``'[]'``, and ``'null'`` — the 3
    empty forms observed.
  * Array form (future migration might normalize): fall through the
    OR — ``extra ? 'visual_paths'`` is TRUE either way.

## Revision ID note

Initial attempt used revision ``a8w9x0y1z2a3`` which collided with
the L3 sprint's ``a8w9x0y1z2a3_monetization_l3_product_bandit_schema
.py``. Second attempt used down_revision ``c9x0y1z2ab34`` (2026-07-05
mergepoint) but the L3 chain descends from ``z7v8w9x0y1z2``
(late_reward) NOT ``c9x0y1z2ab34``, so 2 unmerged heads persisted:
``c0z1a2b3c4d5`` (L3 selector_divergences) and ``d0z1a2b3c4d5``
(this migration). Correct linear chain requires this migration
depend on ``c0z1a2b3c4d5`` — the current L3 head. Now:
``… → c0z1a2b3c4d5 → d0z1a2b3c4d5`` is the single upgrade path.

## Downgrade

Drops the constraint. Historical + intermediate rows remain
untouched (constraint only affects future writes).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d0z1a2b3c4d5"
# Two parents — merges the c9x… (transformation+late_reward) head
# with the L3 chain (c0z…) that extended late_reward independently.
# See the docstring above for the topology diagram.
down_revision = ("c0z1a2b3c4d5", "c9x0y1z2ab34")
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "chk_approve_requires_visual_paths"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE blueprints
        ADD CONSTRAINT {CONSTRAINT_NAME} CHECK (
          NOT (
            action_taken = 'approved'
            AND scheduled_for IS NOT NULL
            AND (format IS NULL OR format IN ('reel', 'short', 'video'))
          )
          OR (
            extra ? 'visual_paths'
            AND COALESCE(extra->>'visual_paths', '') NOT IN ('', '[]', 'null')
          )
        ) NOT VALID;
        """
    )

    # Comment on the constraint for future audit visibility (psql `\d
    # blueprints` shows constraints; the comment surfaces the intent
    # without requiring a git-blame back to this migration).
    op.execute(
        f"""
        COMMENT ON CONSTRAINT {CONSTRAINT_NAME} ON blueprints IS
        'Task #577: blocks operator-SQL bypasses that would create '
        '"approved + scheduled but no media" ghosts publisher fails on. '
        'Applied NOT VALID because 24 historical PUBLISHED rows fail '
        'the check; enforcement is on new INSERT/UPDATE only.';
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE blueprints DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME};")
