"""Normalize pipeline_alerts.severity to lowercase + add CHECK constraint.

Revision ID: v2q3r4s5t6u7
Revises: u1p2q3r4s5t6
Create Date: 2026-06-17

Post-Wave-4 audit (2026-06-17) found ``severity`` had drifted: 4 of 6
writers used lowercase ``'critical'``, 2 shell scripts wrote
``'CRITICAL'``. PR #296 fixed the reader (``notify_critical_alerts.py``
now uses ``ILIKE``); PR ``fix/severity-normalize-writers`` switches the
2 shell-script writers to lowercase.

This migration closes the loop:

  1. UPDATEs any existing uppercase rows on prod to lowercase. The
     reader's ILIKE was already tolerant so this is purely tidy-up.
     Prod query 2026-06-17 showed 3 'critical' + 3 'CRITICAL' rows.

  2. Adds a CHECK constraint ``severity IN ('critical', 'warning',
     'error', 'info')`` so any future writer that drifts uppercase
     gets a hard SQL error at write time — much louder than the
     silent drop we just spent 2 hours debugging.

Casings considered + rejected
==============================
- ``CHECK (severity = lower(severity))``: PostgreSQL allows the
  function in CHECK constraints (lower is IMMUTABLE) but the error
  message a writer sees would be cryptic ("new row violates check
  constraint"). The explicit IN list gives "value not in allowed set".
- ``CHECK (severity ~ '^[a-z]+')``: regex would work but rules out
  legitimate new severities like ``'high'`` if we extend later.

Tradeoff: any future severity must be added to BOTH the IN list AND
the writer code. Acceptable — there are 4 severities total and they
rarely change.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2q3r4s5t6u7"
down_revision = "u1p2q3r4s5t6"
branch_labels = None
depends_on = None


# Canonical severities. New entries here must be reflected in
# ``monitoring.health_monitor.Alert.severity`` + the 4 Python writers
# + the 2 shell-script writers. The audit doc references the list.
_ALLOWED_SEVERITIES = ("critical", "warning", "error", "info")


def upgrade() -> None:
    """Normalize + constrain ``pipeline_alerts.severity``.

    Order matters: the UPDATE must run BEFORE the CHECK is added,
    otherwise the constraint creation would fail on the existing
    uppercase rows.
    """
    # 1. Normalize existing rows. Lower() is safe on any string.
    op.execute(
        """
        UPDATE pipeline_alerts
        SET severity = lower(severity)
        WHERE severity != lower(severity)
        """
    )

    # 2. Add the CHECK constraint. Named so the next operator can
    # drop/rename it cleanly without guessing the auto-generated name.
    allowed_csv = ", ".join(f"'{s}'" for s in _ALLOWED_SEVERITIES)
    op.execute(
        f"""
        ALTER TABLE pipeline_alerts
        ADD CONSTRAINT pipeline_alerts_severity_chk
        CHECK (severity IN ({allowed_csv}))
        """
    )


def downgrade() -> None:
    """Drop the CHECK constraint. We DON'T re-uppercase the rows on
    downgrade — that'd be re-introducing the bug the upgrade fixed."""
    op.execute(
        """
        ALTER TABLE pipeline_alerts
        DROP CONSTRAINT IF EXISTS pipeline_alerts_severity_chk
        """
    )
