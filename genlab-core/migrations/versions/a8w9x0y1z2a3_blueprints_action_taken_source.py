"""add action_taken_source column to blueprints

Revision ID: a8w9x0y1z2a3
Revises: z7v8w9x0y1z2
Create Date: 2026-07-23 06:15:00.000000+00:00

Adds ``action_taken_source TEXT NULL`` to ``blueprints`` so the
nightly scheduler + auto-approver can tag WHO approved each
blueprint (nightly_scheduler, auto_approver_v1, operator).

Background — the 2026-07-21 nightly scheduler patch shipped code
that writes ``action_taken_source = 'nightly_scheduler'`` on
schedule commit, but the column was NEVER added. From ~22:00 IST
2026-07-21 the nightly scheduler has been exiting status=3 on
every fire with:

    ERROR: column "action_taken_source" of relation "blueprints"
    does not exist

Systemd caught the failure and fired ``service_down`` CRITICAL
alerts, but the underlying data-side effect was worse: the
scheduler couldn't fill empty per-niche slots. This is why
2026-07-24 gaming + 2026-07-30-onward runway had gaps that
should have been auto-filled by pulling back far-future blueprints.

NULL is the natural cold-start value: pre-migration blueprints
default to NULL. Downstream consumers filter on the tag to
distinguish safety-net auto-approvals from operator reviews for
calibration analysis — NULL rows fall through as "unlabelled"
in the confusion matrix, matching pre-2026-07-21 behavior.

Storage choice — TEXT NULL, not enum. Options are open-ended
(new automation sources could ship with new tags) and TEXT keeps
the column additive without further schema churn.
"""

from alembic import op

revision = "a8w9x0y1z2a3"
down_revision = "z7v8w9x0y1z2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS action_taken_source TEXT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE blueprints DROP COLUMN IF EXISTS action_taken_source")
