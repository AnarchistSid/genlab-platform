"""merge late_reward_deltas + intelligent_transformation heads

Revision ID: c9x0y1z2ab34
Revises: b8w9x0y1z2ab, z7v8w9x0y1z2
Create Date: 2026-07-05 22:15:00.000000+00:00

Alembic merge migration — topologically unifies two heads that
independently descend from ``y6t7u8v9w0x1`` (strategist tables):

Chain 1: intelligent transformation merge (2026-07-05 evening)
    y6t7u8v9w0x1 → b8w9x0y1z2ab (mergepoint)
    ← itself merges a7v8w9x0y1z2 (transformation schema)

Chain 2: late_reward_deltas (2026-07-02 sprint — PR #654)
    y6t7u8v9w0x1 → z7v8w9x0y1z2

PR #654 landed on ``main`` at 2026-07-05 17:32 UTC (tonight), during
the intelligent-transformation sprint deploy, so neither PR's author
knew about the other's migration. Running ``alembic upgrade head``
now errors with:

    Multiple head revisions are present for given argument 'head';
    please specify a specific target revision, '<branchname>@head'
    to narrow to a specific head, or 'heads' for all heads

Empty upgrade/downgrade — pure topological merge, no schema change.
Both parent migrations still run in order; this migration just
declares the merge point so future migrations can build on a single
head.

Companion: ``b8w9x0y1z2ab_merge_transformation_and_strategist.py``
(same pattern from earlier tonight — this is the second mergepoint
in the same evening, both caused by parallel PR chains landing on
the same base revision).
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c9x0y1z2ab34"
down_revision: str | Sequence[str] | None = ("b8w9x0y1z2ab", "z7v8w9x0y1z2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration — no schema change."""


def downgrade() -> None:
    """Merge migration — no schema change to revert."""
