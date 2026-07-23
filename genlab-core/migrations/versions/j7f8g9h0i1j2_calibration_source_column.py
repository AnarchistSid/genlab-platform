"""add source column to auto_approval_calibration

Revision ID: j7f8g9h0i1j2
Revises: fce3b7f2daf0
Create Date: 2026-07-23 22:00:00.000000+00:00

Adds ``source TEXT NOT NULL DEFAULT 'operator'`` to
``auto_approval_calibration`` so calibration rows written by the new
shadow reviewer (a scheduled LLM pass that produces a
"would-approve" verdict for every VISUAL_READY blueprint) don't get
confused with genuine operator dashboard clicks.

Why a new column instead of a sentinel operator_action value like
'approved_shadow'?

Existing queries filter WHERE operator_action = 'approved' — a
sentinel would silently break every one of them (that's the exact
class-of-bug rule #22 warns about, hit by the 2026-07-17
auto-approver enrollment revert). A dedicated column keeps the
semantic clean: `operator_action` remains the verdict itself,
`source` says who produced it.

Default 'operator' preserves the semantic of every existing row —
all 92 rows in prod as of 2026-07-23 are dashboard clicks, correctly
labelled 'operator' after backfill.

Downstream consumers:
* ``calibration_logger.log()`` gains ``source: str = "operator"`` kwarg
* ``calibration_logger.stats()`` gains ``source_filter`` kwarg
  defaulting to 'operator' so the confusion matrix stays clean
* ``CalibrationStats`` output surface gets ``shadow_agreement_rate``
  as a separate signal

Idempotency: sub-second column adds use IF NOT EXISTS + safe backfill.
"""

from alembic import op

revision = "j7f8g9h0i1j2"
# Merge the two pre-existing heads that were on prod when the shadow
# reviewer work began: fce3b7f2daf0 (content_pool_expiry_96h) and
# a8w9x0y1z2a3 (which itself is duplicated across
# blueprints_action_taken_source.py and monetization_l3_product_bandit_schema.py
# — an old collision that we accept and merge across). Making this a
# tuple downgrade_revision collapses the two heads into one — the shadow
# reviewer's source column becomes the sole new head going forward.
down_revision = ("fce3b7f2daf0", "a8w9x0y1z2a3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE auto_approval_calibration "
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'operator'"
    )
    # Backfill any rows that predate the column with the correct
    # semantic label. IF NOT EXISTS keeps the migration re-runnable.
    op.execute(
        "UPDATE auto_approval_calibration SET source = 'operator' WHERE source IS NULL"
    )
    # Composite index used by the source-filtered confusion matrix
    # in calibration_logger.stats(). WHERE clause keeps the index
    # small — 'operator' will remain the vast majority.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_calibration_niche_source_decided "
        "ON auto_approval_calibration (niche_id, source, decided_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_calibration_niche_source_decided")
    op.execute("ALTER TABLE auto_approval_calibration DROP COLUMN IF EXISTS source")
