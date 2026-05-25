"""adopt the 4 hand-created niche tables into Alembic (R-48)

affiliate_clicks, monetisationprogress, audience_snapshots and ab_tests are
referenced by code but were created by hand directly on the production database
— no authoritative CREATE existed in the repo, so a fresh `alembic upgrade head`
produced a DB missing all four (R-48).

The DDL here was captured by introspecting the live ``genlab`` database
(columns, types, defaults, constraints, indexes and RLS policies), NOT guessed —
so it faithfully reproduces the deployed schema on a clean clone.

Every table is wrapped in a ``DO ... IF to_regclass(...) IS NULL`` block: on the
live DB (where the tables already exist with data) the block is a complete
no-op, so this migration carries ZERO production risk; on a fresh database it
creates the table + indexes + RLS exactly. RLS uses the standard 3-way
``niche_isolation`` escape consistent with every other Alembic table (the live
audience_snapshots/ab_tests policies used a stricter ``= 'all'``-only escape — a
hand-creation inconsistency that only affects unset-context reads; the standard
form is the right baseline for fresh DBs).

content_pool (already adopted in k1f2g3h4i5j6) stays RLS-less — it is the
cross-niche shared pool (no niche_id, a routed_niches array), and its writers
deliberately do not set app.niche_id.

Revision ID: l2g3h4i5j6k7
Revises: k1f2g3h4i5j6
Create Date: 2026-05-25 00:00:00.000000+00:00
"""

from alembic import op

revision = "l2g3h4i5j6k7"
down_revision = "k1f2g3h4i5j6"
branch_labels = None
depends_on = None

# Standard niche-isolation RLS policy used across the schema.
_POLICY = """
        DROP POLICY IF EXISTS niche_isolation ON {t};
        CREATE POLICY niche_isolation ON {t}
            USING (
                niche_id = current_setting('app.niche_id', true)
                OR current_setting('app.niche_id', true) IN ('', 'all')
                OR current_setting('app.niche_id', true) IS NULL
            );
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
"""


def upgrade() -> None:
    # affiliate_clicks ----------------------------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.affiliate_clicks') IS NULL THEN
        CREATE TABLE affiliate_clicks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            network TEXT,
            affiliate_url TEXT,
            referrer TEXT,
            country TEXT,
            platform_source TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            extra JSONB DEFAULT '{}',
            blueprint_id TEXT DEFAULT '',
            channel_id TEXT DEFAULT ''
        );
        CREATE INDEX idx_ac_created_at ON affiliate_clicks(created_at);
        CREATE INDEX idx_ac_blueprint ON affiliate_clicks(blueprint_id) WHERE blueprint_id IS NOT NULL;
        CREATE INDEX idx_ac_channel ON affiliate_clicks(channel_id) WHERE channel_id IS NOT NULL;
        """
        + _POLICY.format(t="affiliate_clicks")
        + """
      END IF;
    END $$;
    """
    )

    # monetisationprogress ------------------------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.monetisationprogress') IS NULL THEN
        CREATE TABLE monetisationprogress (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL DEFAULT '',
            platform TEXT,
            metric_name TEXT,
            current_value DOUBLE PRECISION,
            target_value DOUBLE PRECISION,
            pct_complete DOUBLE PRECISION,
            delta_7d DOUBLE PRECISION,
            days_to_threshold_est INTEGER,
            is_threshold_met BOOLEAN DEFAULT false,
            data_source TEXT DEFAULT 'api',
            as_of_date TEXT,
            error_log TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            extra JSONB DEFAULT '{}',
            CONSTRAINT uq_monetisation_niche_plat_metric UNIQUE (niche_id, platform, metric_name)
        );
        """
        + _POLICY.format(t="monetisationprogress")
        + """
      END IF;
    END $$;
    """
    )

    # audience_snapshots --------------------------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.audience_snapshots') IS NULL THEN
        CREATE TABLE audience_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT,
            platform TEXT,
            metric_name TEXT,
            metric_value NUMERIC,
            snapshot_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            extra JSONB DEFAULT '{}'
        );
        CREATE UNIQUE INDEX idx_audience_snap_unique
            ON audience_snapshots(niche_id, platform, metric_name, snapshot_date);
        """
        + _POLICY.format(t="audience_snapshots")
        + """
      END IF;
    END $$;
    """
    )

    # ab_tests ------------------------------------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.ab_tests') IS NULL THEN
        CREATE TABLE ab_tests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT,
            test_name TEXT,
            variant_a TEXT,
            variant_b TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            extra JSONB DEFAULT '{}'
        );
        """
        + _POLICY.format(t="ab_tests")
        + """
      END IF;
    END $$;
    """
    )


def downgrade() -> None:
    # Destructive on a live DB (these hold data). Provided for chain symmetry;
    # run a deliberate downgrade only.
    for t in ("ab_tests", "audience_snapshots", "monetisationprogress", "affiliate_clicks"):
        op.execute(f"DROP TABLE IF EXISTS {t};")
