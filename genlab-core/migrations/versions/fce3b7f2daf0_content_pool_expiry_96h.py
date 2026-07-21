"""content_pool: bump expires_at default from 48h to 96h

Revision ID: fce3b7f2daf0
Revises: 43c4084cf927
Create Date: 2026-07-21

2026-07-21 throughput audit finding: the movies pipeline has a chronic
3.3% story→BP conversion rate. Root cause is compound but one factor
is `content_pool` rows expiring before the pipeline's composer stage
gets to them.

Composer runs once daily per niche via systemd timer. If a fetch
lands at 03:00 UTC and the composer for that niche runs at 06:30 UTC
tomorrow, the row has ~27h of headroom. But when Anthropic is
exhausted (as happened 2026-07-18→21), the composer retries the
whole batch on next fire — losing another 24h. At 48h TTL, the pool
row was gone before the second attempt.

96h (3× nightly cycle) gives the pipeline two full retry windows +
one clean run. Cost: a modest bump in `content_pool` row count.
Pre-audit at 47,564 rows total; even at 2× we're well under any
Postgres pressure threshold. autovacuum is broken on this table
per the 2026-07-16 audit so this doesn't make cleanup worse — the
scheduled DELETE query in shared_ingestion.py:906 is the actual
purge path and its `WHERE expires_at < NOW()` still triggers.

ID note: `alembic heads` returned `43c4084cf927` at author-time.
`fce3b7f2daf0` was generated with `secrets.token_hex(6)` per the
class-of-bug docstring in `05c64267a3c6` (never pick migration IDs
from `ls | tail`; they collide silently with stale versions).
"""

from alembic import op

revision = "fce3b7f2daf0"
down_revision = "43c4084cf927"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Bump column default from 48h to 96h.

    Existing rows keep their original expires_at (was set at INSERT
    time using the old default). Only affects new INSERTs after this
    migration runs.
    """
    op.execute(
        "ALTER TABLE content_pool "
        "ALTER COLUMN expires_at SET DEFAULT NOW() + INTERVAL '96 hours'"
    )


def downgrade() -> None:
    """Restore the 48h default.

    Same caveat: existing rows keep whatever expires_at they had.
    """
    op.execute(
        "ALTER TABLE content_pool "
        "ALTER COLUMN expires_at SET DEFAULT NOW() + INTERVAL '48 hours'"
    )
