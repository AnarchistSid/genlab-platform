"""blueprints — legacy data cleanup + hook NOT NULL + chk_approve VALIDATE

Revision ID: i5e6f7g8h9i0
Revises: h4d5e6f7g8h9
Create Date: 2026-07-14 19:30:00.000000+00:00

## Background

Three related legacy-data issues verified as of 2026-07-14 19:15 IST:

  1. **44 NULL-hook blueprints** (11 PUBLISHED + 33 ARCHIVED, all April 2026).
     ALL have populated `title` — safe to backfill hook from title.
     After backfill: can enforce NOT NULL on hook column.

  2. **24 chk_approve_requires_visual_paths CHECK constraint violations**.
     ALL are `status='PUBLISHED'` from before the constraint landed
     (migration `d0z1a2b3c4d5`). These reels ALREADY published
     successfully to their platforms — the missing visual_paths field
     is a data-hygiene artifact, not a real semantic problem
     (visual_paths is only meaningful pre-publish for the publisher's
     media lookup). Relaxing the constraint to exempt PUBLISHED status
     is safe: (a) PUBLISHED means media is on-platform already, and
     (b) any future write with visual_paths still enforces properly.

  3. Constraint has been `NOT VALID` since d0z1a2b3c4d5 landed
     2026-07-08 because of #2 — this migration relaxes + VALIDATEs.

## Upgrade steps

1. Backfill 44 NULL hooks with `LEFT(title, 60)` — matches YouTube
   Shorts title 40-char cap + 20-char slack for punctuation.
2. `ALTER COLUMN hook SET NOT NULL` — future writes with NULL fail
   at write time (fast surface for regressions).
3. Drop chk_approve constraint.
4. Recreate with `OR status = 'PUBLISHED'` exception for historical
   rows.
5. `VALIDATE CONSTRAINT` — should succeed since 24 violations now
   pass the PUBLISHED exemption.

## Downgrade

Reverses in inverse order:
  * Drop the NOT NULL on hook.
  * Drop + recreate chk_approve constraint WITHOUT the PUBLISHED
    exception, applied as NOT VALID (matches the pre-migration state).
  * NULL hooks are NOT restored — the backfill's synthetic titles
    stay (no way to distinguish "was NULL" from "was populated with
    title" post-downgrade without a marker column).

## Verification pre-migration (2026-07-14 19:15 IST)

```
null_hooks              44
null_hooks_with_title   44   ← all have title, backfill safe
chk_approve_violations  24
chk_violations_published 24   ← all PUBLISHED
chk_violations_non_published 0  ← no legit-broken rows
```
"""

from alembic import op

revision = "i5e6f7g8h9i0"
down_revision = "h4d5e6f7g8h9"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "chk_approve_requires_visual_paths"


def upgrade() -> None:
    # Step 1: backfill NULL hooks from title
    op.execute(
        """
        UPDATE blueprints
        SET hook = LEFT(title, 60)
        WHERE (hook IS NULL OR hook = '')
          AND title IS NOT NULL
          AND title != ''
        """
    )

    # Step 2: guard — no NULL hooks should remain
    op.execute(
        """
        DO $$
        DECLARE
            null_count INT;
        BEGIN
            SELECT count(*) INTO null_count FROM blueprints
              WHERE hook IS NULL OR hook = '';
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Cannot apply NOT NULL: % rows still have NULL/empty hook after backfill', null_count;
            END IF;
        END $$;
        """
    )

    # Step 3: enforce NOT NULL on hook
    op.execute("ALTER TABLE blueprints ALTER COLUMN hook SET NOT NULL;")

    # Step 4: relax + VALIDATE chk_approve. PUBLISHED-status rows
    # exempt because their media is already shipped — visual_paths
    # only matters pre-publish for the publisher's media lookup.
    op.execute(f"ALTER TABLE blueprints DROP CONSTRAINT {CONSTRAINT_NAME};")
    op.execute(
        f"""
        ALTER TABLE blueprints
        ADD CONSTRAINT {CONSTRAINT_NAME} CHECK (
          status = 'PUBLISHED'
          OR NOT (
            action_taken = 'approved'
            AND scheduled_for IS NOT NULL
            AND (format IS NULL OR format IN ('reel', 'short', 'video'))
          )
          OR (
            extra ? 'visual_paths'
            AND COALESCE(extra->>'visual_paths', '') NOT IN ('', '[]', 'null')
          )
        );
        """
    )


def downgrade() -> None:
    # Drop NOT NULL first (constraint depends on the column being
    # accepting of NULL for downgrade behaviour parity).
    op.execute("ALTER TABLE blueprints ALTER COLUMN hook DROP NOT NULL;")

    # Restore constraint to the NOT VALID pre-migration shape.
    op.execute(f"ALTER TABLE blueprints DROP CONSTRAINT {CONSTRAINT_NAME};")
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
