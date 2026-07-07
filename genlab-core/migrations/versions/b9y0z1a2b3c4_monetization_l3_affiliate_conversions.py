"""L3 PR 10: Monetization Layer 3 — affiliate_conversions table

Ships the schema for real-revenue attribution — the "purchases"
table that L3 PR 11 will read to escalate product-arm rewards from
click-signal (v1, Beta(1+n_clicks, 1)) to purchase-signal
(Bernoulli: α += n_purchases, β += n_clicks - n_purchases).

## Why a separate table (not a column on affiliate_clicks)

* Purchases arrive DAYS after clicks (Amazon reporting is T+1 for
  clicks but T+30 for shipped/returned). Batching purchases into a
  separate append-only table decouples the click hot-path from the
  slow conversion feedback.
* One click can produce ONE OR MORE purchases (Amazon attributes
  the whole session's cart, not just the specific product). A
  conversions row per line-item preserves the 1:N.
* Returned items are a subtraction event — schema needs to represent
  them as separate rows (items_returned column) not as retroactive
  UPDATEs on the original click.

## Amazon Associates CSV columns (as of 2026)

The daily-earnings CSV from associates.amazon.com/reports has these
columns per line:

    Date, ASIN, Title, Category, Items Shipped, Items Returned,
    Ordered Item Revenue, Ordered Item Earnings, Tracking ID

The importer (`scripts/import_amazon_conversions.py`) maps them
directly to the columns below.

## Fields

* ``report_date`` — the CSV row's Date column (transaction day)
* ``asin`` — Amazon Standard Identification Number (10-char alnum)
* ``product_slug`` — best-effort slug lookup from the catalog by
  ASIN → product name → slug. Nullable because a purchase can arrive
  for an ASIN we don't recognize (Amazon attributes bundle carts).
* ``items_shipped`` / ``items_returned`` — direct passthrough
* ``revenue_inr`` — 'Ordered Item Revenue' column
* ``earnings_inr`` — 'Ordered Item Earnings' column (the commission
  we actually receive after Amazon's rate)
* ``tracking_id`` — associates tag used for this row
  (``aspirehub06-20`` in our current config). Distinguishes revenue
  when we run multi-tag experiments in the future.
* ``source_csv_hash`` — sha256 of the source CSV file. Idempotency
  key: import runner skips rows already seen from a given file.

## Design non-goals

* **NOT a click join**: this table has NO foreign key to
  ``affiliate_clicks``. Amazon doesn't tell us which click produced
  which purchase (they don't expose per-click attribution). L3 PR 11
  will aggregate purchases per (asin, product_slug) and update the
  bandit arm's posterior in bulk, not per-click.
* **NOT niche-tagged**: a purchase from ``aspirehub06-20`` doesn't
  carry niche info. We derive niche from the ``product_slug`` →
  catalog lookup at import time. Ambiguous slugs (product in multiple
  niches' catalogs) split the credit evenly.

Revision ID: b9y0z1a2b3c4
Revises: a8w9x0y1z2a3
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b9y0z1a2b3c4"
down_revision = "a8w9x0y1z2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "affiliate_conversions",
        # UUID PK — matches other tables' convention
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Row semantics
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("asin", sa.Text(), nullable=False),
        sa.Column("product_slug", sa.Text(), nullable=True),
        sa.Column(
            "items_shipped",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "items_returned",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("revenue_inr", sa.Float(), nullable=True),
        sa.Column("earnings_inr", sa.Float(), nullable=True),
        sa.Column("tracking_id", sa.Text(), nullable=True),
        # Niche derived at import time via catalog lookup. Nullable
        # because ambiguous slugs may not resolve.
        sa.Column("niche_id", sa.Text(), nullable=True),
        # Idempotency: (source_csv_hash, csv_line_no) uniquely
        # identifies a row's origin. Re-importing the same file
        # skips duplicates.
        sa.Column("source_csv_hash", sa.Text(), nullable=False),
        sa.Column("csv_line_no", sa.Integer(), nullable=False),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Unique constraint for idempotency — one row per line per file.
    op.create_unique_constraint(
        "uq_affiliate_conversions_source",
        "affiliate_conversions",
        ["source_csv_hash", "csv_line_no"],
    )

    # Query indexes
    #
    # (product_slug) — hot path for L3 PR 11's reward wire (aggregate
    # purchases per product_slug, join back to bandit_arms).
    op.create_index(
        "idx_affiliate_conversions_product_slug",
        "affiliate_conversions",
        ["product_slug"],
        postgresql_where=sa.text("product_slug IS NOT NULL"),
    )
    # (niche_id) — for per-niche revenue queries on Mission Control.
    op.create_index(
        "idx_affiliate_conversions_niche_id",
        "affiliate_conversions",
        ["niche_id"],
        postgresql_where=sa.text("niche_id IS NOT NULL"),
    )
    # (report_date) — for time-series revenue dashboards.
    op.create_index(
        "idx_affiliate_conversions_report_date",
        "affiliate_conversions",
        ["report_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_affiliate_conversions_report_date",
        table_name="affiliate_conversions",
    )
    op.drop_index(
        "idx_affiliate_conversions_niche_id",
        table_name="affiliate_conversions",
    )
    op.drop_index(
        "idx_affiliate_conversions_product_slug",
        table_name="affiliate_conversions",
    )
    op.drop_constraint(
        "uq_affiliate_conversions_source",
        "affiliate_conversions",
        type_="unique",
    )
    op.drop_table("affiliate_conversions")
