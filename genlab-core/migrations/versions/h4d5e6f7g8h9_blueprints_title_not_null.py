"""blueprints — NOT NULL constraint on title

Revision ID: h4d5e6f7g8h9
Revises: g3c4d5e6f7g8
Create Date: 2026-07-14 18:00:00.000000+00:00

## Background

2026-07-14 audit verified `blueprints.title` had 0 NULL rows across
2091 records — safe to add NOT NULL constraint. Prior state allowed
NULL (schema default), risking downstream NPE in rendering + publish
paths that assume title is populated (e.g., YouTube title uses
``story.title[:100]`` as a fallback per multiple call sites).

## Upgrade

`ALTER COLUMN title SET NOT NULL` — full table scan, but with 2091
rows on a healthy prod DB it completes in <1s. ACCESS EXCLUSIVE lock
is held for the duration but short enough that concurrent write
contention is unlikely.

## Downgrade

Drops the constraint. Historical data unchanged.

## Verification pre-migration (2026-07-14 17:52 IST)

```
SELECT count(*) FROM blueprints WHERE title IS NULL OR title = '';
-- returned: 0
```

Any future writer that attempts NULL title will fail at write time
with a clear error instead of silently corrupting downstream state.
"""

from alembic import op

revision = "h4d5e6f7g8h9"
down_revision = "g3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard: assert 0 NULL rows before applying, else fail loudly.
    # Prevents this migration from silently succeeding on a DB where
    # title has become NULL between audit time and migration time.
    op.execute(
        """
        DO $$
        DECLARE
            null_count INT;
        BEGIN
            SELECT count(*) INTO null_count FROM blueprints WHERE title IS NULL;
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Cannot apply NOT NULL: % rows have NULL title', null_count;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE blueprints ALTER COLUMN title SET NOT NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE blueprints ALTER COLUMN title DROP NOT NULL;")
