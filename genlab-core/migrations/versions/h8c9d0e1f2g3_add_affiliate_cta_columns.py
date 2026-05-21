"""add affiliate_cta and affiliate_cta_variant columns to blueprints

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-03-25 16:00:00.000000+00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "h8c9d0e1f2g3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("blueprints", sa.Column("affiliate_cta", sa.Text(), nullable=True))
    op.add_column("blueprints", sa.Column("affiliate_cta_variant", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("blueprints", "affiliate_cta_variant")
    op.drop_column("blueprints", "affiliate_cta")
