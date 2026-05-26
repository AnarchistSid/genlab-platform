"""adopt affiliate_revenue / dashboard_events / preference_data / product_embeddings (R-48)

A live-DB-vs-Alembic drift audit found four more code-referenced tables created
outside the migration chain (so a fresh `alembic upgrade head` lacked them):
  * affiliate_revenue   — monetization/revenue_tracker.py
  * dashboard_events     — observability/dashboard_events.py (real-time notices)
  * preference_data      — learning/preference_collector.py (DPO pairs)
  * product_embeddings   — monetization/embedding_matcher.py (pgvector match)

DDL was captured by introspecting the live genlab DB (columns, types, indexes,
RLS policies), not guessed. Each is wrapped in `DO ... IF to_regclass(...) IS
NULL` → a no-op on the live DB (zero prod risk) and a faithful create on a fresh
clone. RLS uses the standard 3-way niche_isolation, ENABLE + FORCE (the live
affiliate_revenue/preference_data had RLS enabled but NOT forced — meaning the
owner bypassed isolation; forcing it on fresh DBs is the correct baseline).
dashboard_events and product_embeddings are cross-niche (events feed / shared
product catalog) and stay RLS-less, like content_pool.

product_embeddings needs pgvector. Its block is additionally guarded on the
extension being AVAILABLE, so `alembic upgrade head` still succeeds on a base
Postgres without pgvector (e.g. the CI postgres:16-alpine image) — the table is
simply skipped there, which is fine since the embedding matcher requires
pgvector to run at all.

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
Create Date: 2026-05-26 00:00:00.000000+00:00
"""

from alembic import op

revision = "n4i5j6k7l8m9"
down_revision = "m3h4i5j6k7l8"
branch_labels = None
depends_on = None

_POLICY = """
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS niche_isolation ON {t};
        CREATE POLICY niche_isolation ON {t}
            USING (
                niche_id = current_setting('app.niche_id', true)
                OR current_setting('app.niche_id', true) IN ('', 'all')
                OR current_setting('app.niche_id', true) IS NULL
            );
"""


def upgrade() -> None:
    # affiliate_revenue (niche-scoped, RLS) ------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.affiliate_revenue') IS NULL THEN
        CREATE TABLE affiliate_revenue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            blueprint_id TEXT,
            network TEXT NOT NULL,
            product_id TEXT,
            clicks INTEGER DEFAULT 0,
            conversions INTEGER DEFAULT 0,
            revenue_amount NUMERIC DEFAULT 0,
            currency TEXT DEFAULT 'INR',
            date DATE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            extra JSONB DEFAULT '{}'
        );
        CREATE INDEX idx_affiliate_revenue_niche_date
            ON affiliate_revenue(niche_id, date DESC);
        """
        + _POLICY.format(t="affiliate_revenue")
        + """
      END IF;
    END $$;
    """
    )

    # preference_data (niche-scoped, RLS) --------------------------------
    op.execute(
        """
    DO $$
    BEGIN
      IF to_regclass('public.preference_data') IS NULL THEN
        CREATE TABLE preference_data (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            generation_context JSONB DEFAULT '{}',
            chosen_hook TEXT,
            chosen_caption TEXT,
            chosen_engagement JSONB DEFAULT '{}',
            chosen_blueprint_id TEXT,
            rejected_hook TEXT,
            rejected_caption TEXT,
            rejected_engagement JSONB DEFAULT '{}',
            rejected_blueprint_id TEXT,
            engagement_ratio DOUBLE PRECISION,
            created_at TIMESTAMPTZ DEFAULT now(),
            used_in_training BOOLEAN DEFAULT false
        );
        CREATE INDEX idx_pref_niche ON preference_data(niche_id, created_at DESC);
        """
        + _POLICY.format(t="preference_data")
        + """
      END IF;
    END $$;
    """
    )

    # dashboard_events (cross-niche notices feed, RLS-less) --------------
    op.execute("""
    DO $$
    BEGIN
      IF to_regclass('public.dashboard_events') IS NULL THEN
        CREATE TABLE dashboard_events (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            entity_id TEXT DEFAULT '',
            entity_type TEXT DEFAULT '',
            niche_id TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX idx_dash_events_created ON dashboard_events(created_at DESC);
      END IF;
    END $$;
    """)

    # product_embeddings (cross-niche, pgvector — guarded on availability)
    op.execute("""
    DO $$
    BEGIN
      IF to_regclass('public.product_embeddings') IS NULL
         AND EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE product_embeddings (
            product_id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            product_category TEXT,
            niche_ids TEXT[],
            embedding vector(1536),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX idx_product_emb ON product_embeddings
            USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
      END IF;
    END $$;
    """)


def downgrade() -> None:
    for t in ("product_embeddings", "dashboard_events", "preference_data", "affiliate_revenue"):
        op.execute(f"DROP TABLE IF EXISTS {t};")
