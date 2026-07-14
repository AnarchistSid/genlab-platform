"""L3 PR 1: Monetization Layer 3 — product bandit schema

Adds the columns needed for the product-selection bandit + real
attribution.

* ``blueprints.product_slug`` — slugified product name for JOIN with
  ``affiliate_clicks.product_id``. Fixes the naming mismatch that
  makes click→blueprint attribution impossible today (blueprints
  store "NVIDIA RTX 4090"; clicks store "nvidia-rtx-4090").
* ``blueprints.price_inr`` — denormalized from catalog for CTA
  price-aware text + selector value-weighting.
* ``affiliate_clicks.blueprint_id`` — FK to blueprints so a click
  attributes to the exact reel that surfaced it (populated via
  UTM campaign parameter — Layer 3 PR 7).
* ``affiliate_clicks.commission_pct`` — denormalized from catalog
  at click time, so retroactive commission changes don't invalidate
  historical reward attribution.

The migration is READ-only-safe for existing rows: all new columns
are nullable. Backfill for ``product_slug`` from existing
``affiliate_product`` values happens as a data operation, not schema
change — done in L3 PR 2.

See docs/MONETIZATION-LAYER-3-DESIGN.md § Phase A for the full sprint
sequence.

Revision ID: a8w9x0y1z2a3
Revises: z7v8w9x0y1z2
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8w9x0y1z2a3"
down_revision = "z7v8w9x0y1z2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── blueprints columns ─────────────────────────────────────────
    #
    # product_slug: for JOIN with affiliate_clicks.product_id. Indexed
    # because Layer 3 attribution queries filter on
    # `WHERE product_slug = ?` in the reward wire hot path.
    #
    # price_inr: for CTA text ("Get X for ₹{price} 👇") and for
    # selector value-weighting (`reward *= log(price × commission)`).
    # Nullable — legacy blueprints don't have this data; the selector
    # falls back to unweighted for those rows.
    op.add_column(
        "blueprints",
        sa.Column("product_slug", sa.Text(), nullable=True),
    )
    op.add_column(
        "blueprints",
        sa.Column("price_inr", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_blueprints_product_slug",
        "blueprints",
        ["product_slug"],
        postgresql_where=sa.text("product_slug IS NOT NULL"),
    )

    # ── affiliate_clicks columns ───────────────────────────────────
    #
    # blueprint_id: FK to blueprints — the reel that surfaced this
    # click. Populated via UTM campaign parameter (Layer 3 PR 7 wires
    # bio-link hub redirect to append `utm_campaign=<blueprint_id>`).
    # Nullable — direct link clicks (e.g., legacy bio-link without
    # campaign param) still land in the click table without an FK,
    # and legacy rows stay valid.
    #
    # commission_pct: denormalized from the catalog at click time.
    # Without this snapshot, a retroactive commission-rate change in
    # the catalog would break historical reward attribution (a
    # click captured at 3% but re-scored at 4% would give the arm
    # a bogus posterior). Snapshotting protects the reward math.
    # 2026-07-14: DuplicateColumn conflict repair. Earlier migration
    # l2g3h4i5j6k7 (adopt_handcreated_tables) already creates
    # affiliate_clicks with blueprint_id TEXT column. This migration
    # was trying to ADD it as UUID → fails "column blueprint_id of
    # relation affiliate_clicks already exists" on any fresh CI/dev
    # DB that runs the migration chain end-to-end. Prod escaped by
    # having the column pre-existing (l2g3... adopts an already-
    # extant table). Fix: DROP + re-ADD (TEXT → UUID) if the
    # existing column is TEXT; skip if already UUID.
    op.execute(
        """
        DO $$
        DECLARE
            col_type TEXT;
        BEGIN
            SELECT data_type INTO col_type
              FROM information_schema.columns
              WHERE table_name = 'affiliate_clicks' AND column_name = 'blueprint_id';
            IF col_type IS NULL THEN
                ALTER TABLE affiliate_clicks
                  ADD COLUMN blueprint_id UUID;
            ELSIF col_type = 'text' THEN
                -- Existing TEXT column stores heterogeneous shapes
                -- (SHA256 candidate_ids + empty strings). Cannot cast
                -- to UUID safely — drop + re-add as UUID (nullable).
                -- Existing data is lost by design; the column was
                -- never usable as FK anyway due to the shape drift.
                ALTER TABLE affiliate_clicks DROP COLUMN blueprint_id;
                ALTER TABLE affiliate_clicks
                  ADD COLUMN blueprint_id UUID;
            END IF;
        END $$;
        """
    )
    # commission_pct — only add if not present
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'affiliate_clicks'
                  AND column_name = 'commission_pct'
            ) THEN
                ALTER TABLE affiliate_clicks
                  ADD COLUMN commission_pct DOUBLE PRECISION;
            END IF;
        END $$;
        """
    )
    # Idempotent FK + index: if migration is re-run against a DB that
    # already has them (e.g., partial-apply recovery), skip cleanly.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_affiliate_clicks_blueprint_id'
            ) THEN
                ALTER TABLE affiliate_clicks
                  ADD CONSTRAINT fk_affiliate_clicks_blueprint_id
                  FOREIGN KEY (blueprint_id) REFERENCES blueprints(id)
                  ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_blueprint_id
          ON affiliate_clicks(blueprint_id)
          WHERE blueprint_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    # Reverse order matters: drop indexes/FK before columns
    op.drop_index("idx_affiliate_clicks_blueprint_id", table_name="affiliate_clicks")
    op.drop_constraint("fk_affiliate_clicks_blueprint_id", "affiliate_clicks", type_="foreignkey")
    op.drop_column("affiliate_clicks", "commission_pct")
    op.drop_column("affiliate_clicks", "blueprint_id")

    op.drop_index("idx_blueprints_product_slug", table_name="blueprints")
    op.drop_column("blueprints", "price_inr")
    op.drop_column("blueprints", "product_slug")
