"""Cross-source CRITICAL pipeline_alerts → Slack/webhook pump.

Why this exists
---------------
`genlab_core.monitoring.health_monitor.notify()` covers alerts the
health-monitor itself generates (in-memory ``list[Alert]``). It does
NOT see alerts written to the ``pipeline_alerts`` table by OTHER
sources — the 2026-06-16 sweep introduced 3 such sources:

* ``permissions_drift`` — nightly drift detector
* ``future_stale_visual_paths`` — auto-archive layer
* ``systemd_unit_failed`` — OnFailure handler for every genlab-* unit

Without this script, the operator only sees these via the dashboard's
CriticalAlertsBanner — fine during working hours, invisible when
they're not looking. This script runs every 5 min via systemd timer,
finds new unresolved CRITICAL rows that haven't been notified yet,
POSTs each to the webhook, and marks them notified.

Configuration
-------------
* ``GENLAB_ALERT_WEBHOOK_URL`` (env) — Slack-compatible incoming
  webhook URL. When unset: script is a no-op and exits 0 silently
  (dev / first-day prod state).
* ``GENLAB_ALERT_LOOKBACK_HOURS`` (env, default 24) — only consider
  alerts created within this window. Prevents a backlog-replay storm
  if the column is added to a table with months of history.

Idempotency
-----------
Uses the ``notified_at`` column added by migration s9n0o1p2q3r4:

    SELECT ... WHERE notified_at IS NULL AND ...
    [post to webhook]
    UPDATE ... SET notified_at = NOW() WHERE id = ANY(...) AND notified_at IS NULL

The double-check in the UPDATE handles the rare case of two notifier
runs racing — only the first one's POST actually wins.

Output / Exit codes
-------------------
* 0 — webhook unset (no-op) OR 0 new alerts OR all posted successfully
* 1 — pre-flight failure (DATABASE_URL missing, psycopg missing)
* 2 — at least one webhook POST failed (operator sees in journalctl
  + the unresolved alert stays in the table for the next run to retry)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _connect():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, connect_timeout=10)


def fetch_pending(conn, *, lookback_hours: int = 24) -> list[dict]:
    """Return un-notified unresolved CRITICAL alerts in the window.

    Case-insensitive severity match (``ILIKE 'critical'``) — the
    post-Wave-4 audit (2026-06-17) found this query was previously
    case-sensitive ``= 'CRITICAL'``, which silently missed 4 of 6
    alert-writer sources because they emit lowercase ``'critical'``:

      * ``monitoring/health_monitor.py``  (pipeline / channel failures)
      * ``engagement/token_health.py``    (OAuth expiry)
      * ``learning/hook_classifier.py``   (training failure)
      * ``scripts/archive_stale_visual_paths.py``

    The 2 writers that survived the prior filter emit uppercase:

      * ``scripts/systemd_failure_alert.sh``
      * ``scripts/check_permissions_drift.sh``

    Prod query on 2026-06-17 returned 3 of each casing — operator's
    Slack pump had been silently dropping half the critical class for
    however long. Using ``ILIKE`` keeps the fix surgical (one query)
    and tolerant of future writer drift.
    """
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id, niche_id, check_name, message, created_at
            FROM pipeline_alerts
            WHERE severity ILIKE 'critical'
              AND resolved_at IS NULL
              AND notified_at IS NULL
              AND created_at >= NOW() - (%s::int || ' hours')::interval
            ORDER BY created_at ASC
            """,
            (lookback_hours,),
        )
        return [
            {
                "id": str(row[0]),
                "niche_id": row[1] or "",
                "check_name": row[2],
                "message": row[3],
                "created_at": row[4],
            }
            for row in cur.fetchall()
        ]


def post_to_webhook(url: str, alert: dict) -> bool:
    """POST a Slack-compatible payload. Returns True on 2xx, False otherwise.

    Network failures, non-2xx, and any exception are caught and
    returned as False — the caller leaves notified_at NULL so the
    next run retries.
    """
    import requests  # local import — only needed when webhook is configured

    title = f"🚨 [{alert['check_name']}] {alert['niche_id'] or 'all'}"
    # Trim to 2000 chars — Slack's blocks limit
    body = alert["message"][:2000]
    text = f"*{title}*\n{body}\n\n_alert id_: `{alert['id']}`"
    try:
        r = requests.post(url, json={"text": text}, timeout=10)
        if not (200 <= r.status_code < 300):
            logger.warning(
                "webhook POST returned %d for alert %s: %s",
                r.status_code,
                alert["id"][:8],
                r.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("webhook POST raised for alert %s: %s", alert["id"][:8], exc)
        return False


def mark_notified(conn, alert_ids: list[str]) -> int:
    """Atomically mark the alerts notified. Returns updated count."""
    if not alert_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            UPDATE pipeline_alerts
            SET notified_at = NOW()
            WHERE id = ANY(%s::uuid[])
              AND notified_at IS NULL
            """,
            (alert_ids,),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notify_critical_alerts",
        description="POST unresolved CRITICAL pipeline_alerts to the operator webhook.",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=int(os.environ.get("GENLAB_ALERT_LOOKBACK_HOURS", "24")),
        help="Only consider alerts newer than this. Default 24.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be sent but don't POST or mark notified.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    url = os.environ.get("GENLAB_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        logger.info(
            "[notifier] GENLAB_ALERT_WEBHOOK_URL not set — no-op. Set it in .env to enable."
        )
        return 0

    try:
        conn = _connect()
    except Exception as exc:
        logger.error("[notifier] DB connect failed: %s", exc)
        return 1

    try:
        pending = fetch_pending(conn, lookback_hours=args.lookback_hours)
        logger.info("[notifier] %d unresolved CRITICAL alert(s) un-notified", len(pending))

        if not pending:
            return 0

        if args.dry_run:
            for a in pending:
                logger.info(
                    "[notifier-dryrun] would post: [%s] %s — %s",
                    a["check_name"],
                    a["niche_id"] or "all",
                    a["message"][:80],
                )
            return 0

        posted_ids: list[str] = []
        failed = 0
        for alert in pending:
            if post_to_webhook(url, alert):
                posted_ids.append(alert["id"])
            else:
                failed += 1

        if posted_ids:
            updated = mark_notified(conn, posted_ids)
            logger.info("[notifier] marked %d alert(s) notified", updated)

        if failed:
            logger.warning(
                "[notifier] %d/%d POSTs failed — will retry next run", failed, len(pending)
            )
            return 2
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
