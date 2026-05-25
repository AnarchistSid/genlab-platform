"""adopt content_pool + pipeline_alerts into Alembic (R-48)

Both tables are referenced by code but were created OUTSIDE the Alembic chain —
``content_pool`` by the ad-hoc ``create_content_pool.sql``, ``pipeline_alerts``
by a runtime ``CREATE TABLE IF NOT EXISTS`` in ``health_monitor.py``. A fresh
``alembic upgrade head`` therefore produced a database MISSING them (R-48). This
folds their authoritative DDL into the chain so a clean clone — a new dev box or
a future tenant — is complete and reproducible.

Idempotent (``CREATE TABLE/INDEX IF NOT EXISTS``) so it is a safe no-op on the
live database, which already holds both tables and their data.

RLS is intentionally NOT added here. ``content_pool`` is cross-niche by design
(the shared pool BEFORE routing — it has no ``niche_id`` column, only a
``routed_niches`` array), and ``pipeline_alerts`` is an ops table the dashboard
reads across niches; both authoritative sources are RLS-less. Per-table RLS is a
separate decision (R-63 / a later PR, after auditing that every writer sets
``app.niche_id``).

The four OTHER ad-hoc tables (affiliate_clicks, monetisationprogress,
audience_snapshots, ab_tests) were hand-created directly on prod with no
authoritative CREATE in the repo, so adopting them safely needs a prod
``pg_dump --schema-only`` to match columns/types exactly — deferred, not guessed
here.

Revision ID: k1f2g3h4i5j6
Revises: j0e1f2g3h4i5
Create Date: 2026-05-25 00:00:00.000000+00:00
"""

from alembic import op

revision = "k1f2g3h4i5j6"
down_revision = "j0e1f2g3h4i5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # content_pool — verbatim from genlab-core/migrations/create_content_pool.sql
    # + add_content_pool_video_idx.sql (both already IF NOT EXISTS).
    op.execute("""
    CREATE TABLE IF NOT EXISTS content_pool (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        content_hash TEXT NOT NULL,
        title TEXT,
        summary TEXT,
        source_url TEXT,
        source_name TEXT,
        source_platform TEXT,
        video_url TEXT,
        video_id TEXT,
        thumbnail_url TEXT,
        published_at TIMESTAMPTZ,
        duration_seconds INT,
        view_count BIGINT,
        view_velocity FLOAT,
        source_affinity TEXT[],
        youtube_category_id TEXT,
        niche_scores JSONB NOT NULL DEFAULT '{}',
        routed_niches TEXT[] NOT NULL DEFAULT '{}',
        routing_reason TEXT,
        status TEXT NOT NULL DEFAULT 'available',
        claimed_by TEXT,
        claimed_at TIMESTAMPTZ,
        fetched_at TIMESTAMPTZ DEFAULT NOW(),
        expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '48 hours',
        extra JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT uq_content_pool_hash UNIQUE (content_hash)
    );

    CREATE INDEX IF NOT EXISTS idx_cp_routed_status
        ON content_pool USING GIN(routed_niches) WHERE status = 'available';
    CREATE INDEX IF NOT EXISTS idx_cp_status ON content_pool(status);
    CREATE INDEX IF NOT EXISTS idx_cp_expires
        ON content_pool(expires_at) WHERE status = 'available';
    CREATE INDEX IF NOT EXISTS idx_cp_fetched ON content_pool(fetched_at DESC);
    CREATE INDEX IF NOT EXISTS idx_cp_video_id
        ON content_pool(video_id) WHERE video_id IS NOT NULL;
    """)

    # pipeline_alerts — verbatim from monitoring/health_monitor.py's runtime DDL.
    op.execute("""
    CREATE TABLE IF NOT EXISTS pipeline_alerts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT,
        check_name TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'warning',
        message TEXT NOT NULL,
        details JSONB,
        auto_fix_applied TEXT,
        auto_fix_result TEXT,
        resolved_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)


def downgrade() -> None:
    # Destructive — these hold data on a live DB. Provided for chain symmetry;
    # only run a deliberate downgrade.
    op.execute("DROP TABLE IF EXISTS pipeline_alerts;")
    op.execute("DROP TABLE IF EXISTS content_pool;")
