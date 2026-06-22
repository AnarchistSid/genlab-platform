"""add hook_classifier_score column to blueprints

Revision ID: z6u7v8w9x0y1
Revises: y5t6u7v8w9x0
Create Date: 2026-06-22 11:40:00.000000+00:00

Adds ``hook_classifier_score FLOAT NULL`` to ``blueprints`` so the
XGBoost predicted-engagement score from BaseHookStrategy (Lever D1,
PR #420) actually persists rather than getting silently dropped at
the blueprint boundary.

Background — the 2026-06-22 autonomy audit found this was a half-
wired loop:
- WRITE: PR #420 set ``story['hook_classifier_score'] = clf_score``
  at base_hooks.py:168 on every hook generation
- READ: NOWHERE — zero downstream consumers in production code
- EFFECT: NONE — XGBoost predictions computed then silently
  discarded at the story → blueprint boundary

A first attempt at PR #458 wired ``fields['hook_classifier_score']``
without this migration. Live prod probe revealed the column didn't
exist, which would have made the wire a silent no-op (Postgres
backend ignores unknown column writes). This migration unblocks
the wire by adding the column.

NULL is the natural cold-start value: blueprints created before
the column existed, or by code paths that don't compute the score
(e.g. backfill), default to NULL. The gate's 6th check at
auto_approval_gate.py treats missing scores as "no contribution
to confidence aggregate" — preserves the pre-Lever-D1 confidence
math exactly for legacy blueprints.

Storage choice — column not extra JSONB: matches the pattern set
by composite_score and virality_score (also FLOAT columns on
blueprints). Top-level columns are queryable via SQL without
JSONB extraction overhead, which matters for the gate_tuner-style
analytical queries that aggregate by niche over time windows.
"""

from alembic import op

revision = "z6u7v8w9x0y1"
down_revision = "y5t6u7v8w9x0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE blueprints "
        "ADD COLUMN IF NOT EXISTS hook_classifier_score FLOAT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE blueprints DROP COLUMN IF EXISTS hook_classifier_score")
