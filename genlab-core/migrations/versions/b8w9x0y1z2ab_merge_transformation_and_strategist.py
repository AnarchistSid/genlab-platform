"""merge intelligent_transformation + strategist heads

Revision ID: b8w9x0y1z2ab
Revises: a7v8w9x0y1z2, y6t7u8v9w0x1
Create Date: 2026-07-05 17:45:00.000000+00:00

Alembic merge migration — topologically unifies two heads that
independently descend from ``x5s6t7u8v9w0`` (post_decision_trace).

Chain 1: intelligent transformation sprint (2026-07-05)
    x5s6t7u8v9w0 → y5t6u7v8w9x0 → z6u7v8w9x0y1 → a7v8w9x0y1z2

Chain 2: strategist tables (2026-07-01)
    x5s6t7u8v9w0 → y6t7u8v9w0x1

Both chains landed on main between 2026-07-01 and 2026-07-05, but
neither PR knew about the other's migration. This merge migration
lets ``alembic upgrade head`` resolve without ambiguity.

Empty upgrade/downgrade — pure topological merge, no schema change.
Both parent migrations still run in order; this migration just
declares the merge point.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8w9x0y1z2ab"
down_revision: str | Sequence[str] | None = ("a7v8w9x0y1z2", "y6t7u8v9w0x1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No-op: this migration exists only to unify the two divergent
    # branches so ``alembic upgrade head`` has a single target.
    pass


def downgrade() -> None:
    pass
