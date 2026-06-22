"""Auto-repair root-owned-file drift in /opt/genlab.

Runs every 15 min as ROOT via systemd timer. Queries pipeline_alerts
for unresolved permissions_drift alerts in the last 24h. If any are
found, invokes ``scripts/repair_permissions.sh --apply`` (which can
only run as root since it chowns files back to genlab:genlab).
On success, marks the alerts resolved in the DB so the dashboard's
CriticalAlertsBanner clears them.

Why root + a separate process
-----------------------------
``notify_critical_alerts.py`` (sibling) runs as the ``genlab`` user
and lacks the privilege to chown root-owned files. Trying to grant
``genlab`` passwordless sudo on ``repair_permissions.sh`` would work
but introduces a privilege-escalation surface that's harder to audit
than a separate root-owned service running on a tight schedule.

A periodic root-run check is simpler to reason about:
- The script is small + read-only against the DB until it confirms
  drift exists
- The repair script (``repair_permissions.sh``) is already audited
  and idempotent — re-runs are safe
- Failure modes are loud (alert stays unresolved → operator sees it)

Configuration
-------------
* ``DATABASE_URL`` (env) — same as the notifier
* ``GENLAB_PERMISSIONS_REPAIR_ENABLED`` (env, default "1") — kill
  switch. Set to "0" to disable auto-repair without un-installing
  the timer. Useful during operator-attended incidents where the
  operator wants to investigate before any automated touch.

Exit codes
----------
* 0 — no drift alerts OR repair succeeded OR auto-repair disabled
* 1 — pre-flight failure (DB connect, missing repair script)
* 2 — repair script failed (alerts stay unresolved → operator sees)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

REPAIR_SCRIPT = "/opt/genlab/scripts/repair_permissions.sh"


def _connect():
    """Open a Postgres connection using the same tenant-context helper
    the notifier uses. Centralizes the SET app.niche_id pattern."""
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, connect_timeout=10)


def fetch_unresolved_drift_alerts(conn, *, lookback_hours: int = 24) -> list[dict]:
    """Return unresolved permissions_drift alerts in the lookback window.

    Mirrors the notifier's ILIKE-case-insensitive severity check (sources
    emit both 'CRITICAL' and 'critical' — see notify_critical_alerts.py
    docstring for the full story). Only ``check_name = 'permissions_drift'``
    here — this script must NEVER touch other alert types.
    """
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id, niche_id, message, created_at
            FROM pipeline_alerts
            WHERE check_name = 'permissions_drift'
              AND severity ILIKE 'critical'
              AND resolved_at IS NULL
              AND created_at >= NOW() - (%s::int || ' hours')::interval
            ORDER BY created_at ASC
            """,
            (lookback_hours,),
        )
        return [
            {
                "id": str(row[0]),
                "niche_id": row[1] or "all",
                "message": row[2],
                "created_at": row[3],
            }
            for row in cur.fetchall()
        ]


def run_repair() -> tuple[bool, str]:
    """Invoke repair_permissions.sh --apply. Return (success, output).

    Captures both stdout + stderr so the operator can see what happened
    in journalctl when the repair fails.
    """
    if not os.path.exists(REPAIR_SCRIPT):
        return False, f"repair script not found at {REPAIR_SCRIPT}"
    try:
        result = subprocess.run(
            [REPAIR_SCRIPT, "--apply"],
            capture_output=True,
            text=True,
            timeout=300,  # repair on 1500-file drift takes ~10s; 5min headroom
            check=False,
        )
        # repair_permissions.sh exit codes:
        #   0 = nothing to fix OR --apply succeeded
        #   2 = --dry-run with drift (we use --apply, so shouldn't see)
        #   3 = --apply failed
        ok = result.returncode == 0
        output = (result.stdout + "\n" + result.stderr).strip()
        return ok, output
    except subprocess.TimeoutExpired:
        return False, "repair_permissions.sh timed out after 5min"
    except Exception as exc:  # noqa: BLE001 — surface to caller
        return False, f"repair_permissions.sh raised: {exc}"


def mark_alerts_resolved(conn, alert_ids: list[str], resolution_note: str) -> int:
    """Atomically mark the alerts resolved with an audit trail.

    Updates resolved_at + resolved_note so the dashboard shows what
    auto-resolved them (operator can audit retrospectively).
    """
    if not alert_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            UPDATE pipeline_alerts
            SET resolved_at = NOW(),
                resolved_note = %s
            WHERE id = ANY(%s::uuid[])
              AND resolved_at IS NULL
            """,
            (resolution_note, alert_ids),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Kill switch: lets the operator disable auto-repair during
    # incidents without uninstalling the timer.
    if os.environ.get("GENLAB_PERMISSIONS_REPAIR_ENABLED", "1") != "1":
        logger.info("[auto-repair] disabled via GENLAB_PERMISSIONS_REPAIR_ENABLED!=1")
        return 0

    try:
        conn = _connect()
    except Exception as exc:
        logger.error("[auto-repair] DB connect failed: %s", exc)
        return 1

    try:
        alerts = fetch_unresolved_drift_alerts(conn)
        if not alerts:
            logger.info("[auto-repair] no unresolved permissions_drift alerts — nothing to do")
            return 0

        logger.info(
            "[auto-repair] %d unresolved permissions_drift alert(s) — running repair",
            len(alerts),
        )

        ok, output = run_repair()
        if not ok:
            logger.error(
                "[auto-repair] repair_permissions.sh --apply FAILED; alerts stay unresolved:\n%s",
                output[:2000],
            )
            # Exit 2 (not 1) — pre-flight succeeded, the repair itself
            # failed. systemd-side this is a soft failure; the alerts
            # stay unresolved so the operator sees them in the dashboard.
            return 2

        logger.info(
            "[auto-repair] repair_permissions.sh --apply succeeded (output: %d chars)",
            len(output),
        )
        alert_ids = [a["id"] for a in alerts]
        note = (
            "auto-repaired by genlab-permission-auto-repair.service; "
            "repair_permissions.sh --apply succeeded"
        )
        updated = mark_alerts_resolved(conn, alert_ids, note)
        logger.info(
            "[auto-repair] marked %d/%d alert(s) resolved",
            updated,
            len(alerts),
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
