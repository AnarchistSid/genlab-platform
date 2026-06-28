"""pending_feedback: propensity + temperature for IPS counterfactual eval

Adds two columns to ``pending_feedback`` so every bandit decision can
log the selection propensity ``p(arm | context)`` at decision time.
Without these stored AT decision time, Inverse Propensity Scoring (IPS)
estimators that ask "what would last month's reward have been under a
different policy?" are impossible to reconstruct — the doc explicitly
calls out propensity logging as **painful to add retroactively** but
trivial forward (one float per decision).

Columns
=======

* ``propensity REAL`` — ``p(bandit_arm | bandit_context)`` under the
  policy that picked the arm. ``NULL`` for legacy rows predating this
  migration; downstream IPS estimators MUST filter on
  ``propensity IS NOT NULL`` before applying the ``1/p`` weight (the
  classic IPS variance-explosion mitigation).
* ``temperature REAL`` — the softmax temperature in effect at decision
  time. ``NULL`` indicates deterministic (argmax) selection, where
  propensity = 1.0 by construction. Persisting the temperature
  separately means a future analyst can verify "this row was decided
  under T=0.5" instead of inferring it from the row's propensity
  distribution shape.

Why ``REAL`` not ``DOUBLE PRECISION``: propensities live in [0, 1]
and a single-precision float gives 7 decimal digits of mantissa, far
more than the IPS estimator needs. Saves 4 bytes per row × ~6
publishes/day × 5 niches × 365 days = ~44KB/year. Trivial but
matches the column type of ``alpha`` / ``beta`` in ``bandit_arms``.

Why no NOT NULL or DEFAULT: backward compat. Every existing row +
every row written by a caller that hasn't been migrated yet stays
NULL. The producer side (LinUCBBandit.select_with_propensity in
learning/linucb.py) sets it explicitly; callers that don't pass
it through PendingFeedbackTask continue to write NULL — the IPS
filter handles both cases.

Indexing: NOT indexed. IPS replay scripts scan the full pending_feedback
window anyway (typically 30 days) so an index on a near-uniform float
column wouldn't help. Add a partial index ``WHERE propensity IS NOT
NULL`` later if the IPS replay becomes a hot path.

Move #8 context: AGENT-AUTONOMY-RESEARCH.md, Tier 2 move ranked HIGH
on counterfactual-eval value, MEDIUM on cost. The matching PR T
implements the producer (LinUCB.select_with_propensity) + writer
(PendingFeedbackTask.to_sharepoint_fields).

Revision ID: v3q4r5s6t7u8
Revises: g3h4i5j6k7l8
Create Date: 2026-06-28 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v3q4r5s6t7u8"
down_revision: str | Sequence[str] | None = "g3h4i5j6k7l8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``propensity`` + ``temperature`` columns to ``pending_feedback``.

    Both columns nullable. Existing rows keep NULL — IPS replay
    filters ``propensity IS NOT NULL`` to skip rows predating the
    migration.
    """
    op.execute(
        """
        ALTER TABLE pending_feedback
            ADD COLUMN IF NOT EXISTS propensity REAL,
            ADD COLUMN IF NOT EXISTS temperature REAL
        """
    )


def downgrade() -> None:
    """Drop the IPS columns.

    Safe to downgrade — every consumer treats both columns as optional
    (PendingFeedbackTask.propensity is ``float | None`` with default
    None). Reverting the migration only erases historical IPS data,
    it doesn't break any read path.
    """
    op.execute(
        """
        ALTER TABLE pending_feedback
            DROP COLUMN IF EXISTS temperature,
            DROP COLUMN IF EXISTS propensity
        """
    )
