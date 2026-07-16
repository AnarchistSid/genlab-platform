"""merge PA cleanup branch with blueprints cleanup branch

2026-07-17: b9x0y1z2a3b4 (publishing_analytics dedupe/heal/UNIQUE)
was created chaining from a8w9x0y1z2a3 but i5e6f7g8h9i0 (blueprints
legacy data cleanup, 2026-07-14) had ALSO chained from
a8w9x0y1z2a3's parent chain, leaving two heads. This merge is a no-op
DDL revision that unifies the branches so `alembic upgrade head` has
a single unambiguous target again.

Root cause: the head-check when authoring b9x0y1z2a3b4 grep'd
single-quoted `down_revision = '...'` but the codebase uses double
quotes — the earlier i5e6f7g8h9i0 branch was invisible. Future
migration authors: use `alembic heads` (not grep) to confirm the
current head before setting down_revision.

Revision ID: 05c64267a3c6
Revises: b9x0y1z2a3b4, i5e6f7g8h9i0
Create Date: 2026-07-17 00:32:17.997523

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "05c64267a3c6"
down_revision: str | Sequence[str] | None = ("b9x0y1z2a3b4", "i5e6f7g8h9i0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op — merge revision only unifies parallel branches."""


def downgrade() -> None:
    """No-op — the parent branches carry their own downgrade paths."""
