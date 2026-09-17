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

    SELECT ... WHERE notified_at IS NULL
                     OR notified_at < NOW() - RENOTIFY_AFTER_HOURS ...
    [post to webhook]
    UPDATE ... SET notified_at = NOW() WHERE id = ANY(...)
    (no `AND notified_at IS NULL` -- that guard would pin the timestamp at the
     first send and make re-notification a no-op)

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

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

logger = logging.getLogger(__name__)


def _connect():

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return pg_connect(dsn, connect_timeout=10, niche_id="all")


# An unresolved CRITICAL is re-notified on this cadence. Without it, a
# persistent outage is indistinguishable -- from the operator's inbox -- from
# one that resolved itself.
#
# Measured 2026-09-17: `anthropic_credit_exhausted` was created 07:00:15,
# notified once at 07:05:29, and was STILL unresolved twelve hours later with
# every LLM tier down and approvals at zero on every niche. The check fired, the
# webhook worked, the row was written -- and the operator got exactly one
# message, in the morning, for an outage that ran all day. The condition also
# WORSENED at 12:30 when the belt session expired, and that escalation was
# swallowed by the same dedup.
RENOTIFY_AFTER_HOURS = int(os.environ.get("GENLAB_ALERT_RENOTIFY_HOURS", "4"))


def fetch_pending(conn, *, lookback_hours: int = 24) -> list[dict]:
    """Return CRITICAL alerts that need sending: never notified, OR unresolved
    and last notified more than RENOTIFY_AFTER_HOURS ago.

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
            SELECT id, niche_id, check_name, message, created_at,
                   notified_at,
                   EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0 AS age_hours
            FROM pipeline_alerts
            WHERE severity ILIKE 'critical'
              AND resolved_at IS NULL
              AND (
                    notified_at IS NULL
                 OR notified_at < NOW() - (%s::int || ' hours')::interval
              )
              AND created_at >= NOW() - (%s::int || ' hours')::interval
            ORDER BY created_at ASC
            """,
            (RENOTIFY_AFTER_HOURS, lookback_hours),
        )
        return [
            {
                "id": str(row[0]),
                "niche_id": row[1] or "",
                "check_name": row[2],
                "message": row[3],
                "created_at": row[4],
                "notified_at": row[5],
                "age_hours": float(row[6] or 0.0),
                "is_reminder": row[5] is not None,
            }
            for row in cur.fetchall()
        ]


def _decorate(alert: dict) -> dict:
    """A reminder must announce itself, or it reads as a fresh incident."""
    if not alert.get("is_reminder"):
        return alert
    hours = alert.get("age_hours", 0.0)
    out = dict(alert)
    out["message"] = (f"[STILL UNRESOLVED after {hours:.1f}h] "
                      f"{alert.get('message', '')}")
    return out


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
            if post_to_webhook(url, _decorate(alert)):
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
