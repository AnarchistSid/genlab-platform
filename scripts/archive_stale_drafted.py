"""Auto-archive DRAFTED blueprints that never acquired a source video.

Why this exists
---------------
DRAFTED blueprints sit at the very start of the pipeline. They were
created by ``compose_blueprints`` but no source video was ever found
for them by ``fetch_trending_videos`` — typically because the topic
itself wasn't covered on YouTube on the day the blueprint was scored
in, or the relevance filter rejected every candidate.

Symptoms today (2026-06-24 audit):

  * 3 stuck DRAFTED rows (2 gaming + 1 anime), all 2-3 days old,
    all ``video_state = 'no_video'``.
  * The pipeline NEVER retries these — once a blueprint is DRAFTED
    without a video, it stays that way forever.
  * They don't show up in the operator review queue (only
    ``VISUAL_READY`` blueprints with ``action_taken IS NULL`` do —
    PR #510), so the operator can't manually triage them.
  * They DO show up in noisy SELECTs (e.g. ``pending`` aggregates,
    diagnostic queries) and slowly accumulate over time.

The fix is the simplest possible: after a grace period (default 7
days), demote them to ARCHIVED with a clear ``archived_reason``.
This is the right action because:

  * The topic that drove the blueprint is no longer trending —
    re-publishing the same topic days later wouldn't be useful.
  * The pipeline doesn't currently retry video acquisition for old
    DRAFTED rows (and probably shouldn't — YouTube trends move
    too fast for re-tries to be valuable).
  * The audit trail (``extra.archived_reason``) preserves the
    original blueprint for forensics.

This script is the **one-time cleanup tool**. A follow-up PR can
wire it to a systemd timer for nightly runs once the operator
confirms the dry-run finds the right rows. Keeping the two
shippable separately so the data-mutation can be reviewed in
isolation before the cron lands.

Pattern mirrors ``archive_stale_visual_paths.py`` exactly:
  * Dry-run by default; ``--apply`` to mutate.
  * CSV backup before any UPDATE fires.
  * Respects ``cleanup_safety.md``: never demotes ``scheduled_for``
    rows (a stale DRAFTED with ``scheduled_for`` set is a different
    bug class — the scheduler shouldn't have touched it; surface
    it instead of silently archiving).
  * Idempotent — already-ARCHIVED rows ignored by the WHERE clause.

Operator usage::

    # On prod (read-only preview):
    sudo -u genlab bash -c 'cd /opt/genlab && \\
      PYTHONPATH=genlab-core/src python3 scripts/archive_stale_drafted.py'

    # Apply with default 7-day grace:
    sudo -u genlab bash -c 'cd /opt/genlab && \\
      PYTHONPATH=genlab-core/src python3 scripts/archive_stale_drafted.py --apply'

    # Apply with tighter grace for today's 3 stuck rows (~3 days old):
    sudo -u genlab bash -c 'cd /opt/genlab && \\
      PYTHONPATH=genlab-core/src python3 scripts/archive_stale_drafted.py \\
        --apply --age-days 3'
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

logger = logging.getLogger(__name__)


def _connect():
    """Open a psycopg connection from DATABASE_URL — caller owns the close."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return pg_connect(dsn, connect_timeout=10, niche_id="all")


def find_stale_drafted(conn, *, age_days: int) -> list[dict]:
    """Return DRAFTED blueprints older than ``age_days`` with no video.

    The "no video" check matches both visual_paths empty/missing AND
    source_video_url empty/missing — a row that has EITHER side filled
    might still be in flight (e.g. the renderer is mid-job, or a
    next-run fetch will pick it up). Conservative by default.

    Skips:
      * Rows with ``scheduled_for`` set (cleanup_safety.md) — surface
        these separately for operator attention; auto-archive would
        silently break a scheduled slot.
    """
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id::text, niche_id, status, created_at, scheduled_for,
                   extra->>'source_video_url' AS source_video_url,
                   extra->>'visual_paths' AS visual_paths_raw,
                   hook_text, title
            FROM blueprints
            WHERE status = 'DRAFTED'
              AND created_at < NOW() - (%s * INTERVAL '1 day')
              AND scheduled_for IS NULL
              AND (extra->>'source_video_url' IS NULL OR extra->>'source_video_url' = '')
              AND (extra->>'visual_paths' IS NULL
                   OR extra->>'visual_paths' = ''
                   OR extra->>'visual_paths' = '[]')
            ORDER BY created_at ASC
            """,
            (age_days,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def find_stuck_with_schedule(conn, *, age_days: int) -> list[dict]:
    """The defensive complement: DRAFTED rows that ARE scheduled.

    These shouldn't exist (scheduler operates on VISUAL_READY) — when
    they appear it's a separate bug worth surfacing. Returns the rows
    so the operator can investigate, but never auto-archives them.
    """
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id::text, niche_id, created_at, scheduled_for
            FROM blueprints
            WHERE status = 'DRAFTED'
              AND created_at < NOW() - (%s * INTERVAL '1 day')
              AND scheduled_for IS NOT NULL
            """,
            (age_days,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def write_backup(records: list[dict], backup_dir: Path) -> Path | None:
    """CSV backup before any UPDATE — mirrors ``archive_stale_visual_paths``."""
    if not records:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"archive_stale_drafted_{ts}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "niche_id",
                "status",
                "created_at",
                "scheduled_for",
                "source_video_url",
                "visual_paths_raw",
                "hook_text",
                "title",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r["id"],
                    r["niche_id"],
                    r["status"],
                    r["created_at"],
                    r["scheduled_for"],
                    r["source_video_url"] or "",
                    r["visual_paths_raw"] or "",
                    r["hook_text"] or "",
                    r["title"] or "",
                ]
            )
    return path


def archive_blueprints(conn, records: list[dict], reason: str) -> int:
    """Demote a list of DRAFTED blueprints to ARCHIVED.

    Uses an explicit ``status='DRAFTED'`` filter in the UPDATE so a
    TOCTOU flip (DRAFTED → VISUAL_READY between detect and archive) is
    safely a no-op rather than an inappropriate downgrade.
    """
    if not records:
        return 0
    ids = [r["id"] for r in records]
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = NULL,
                extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                    'archived_reason', %s::text,
                    'archived_at', NOW()::text,
                    'archived_by', 'archive_stale_drafted.py'
                ),
                updated_at = NOW()
            WHERE id = ANY(%s::uuid[])
              AND status = 'DRAFTED'
            """,
            (reason, ids),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="archive_stale_drafted",
        description=(
            "Auto-archive DRAFTED blueprints that never acquired a source "
            "video. Conservative by default — pass --apply to mutate."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate. Default is dry-run (read-only preview).",
    )
    parser.add_argument(
        "--age-days",
        type=int,
        default=7,
        help=(
            "Grace period in days before a no-video DRAFTED row is "
            "considered stale. Default 7. Lower values are more aggressive; "
            "today's 3 stuck rows are ~3 days old."
        ),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/opt/genlab/.tmp/backups"),
        help="Where to write the pre-update CSV backup.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        conn = _connect()
    except Exception as exc:
        logger.error("DB connection failed: %s", exc)
        return 1

    try:
        records = find_stale_drafted(conn, age_days=args.age_days)
        scheduled_anomaly = find_stuck_with_schedule(conn, age_days=args.age_days)

        ts_header = datetime.now(UTC).isoformat()
        print(
            f"# {ts_header} — {len(records)} stale DRAFTED blueprint(s) "
            f"older than {args.age_days} day(s) with no source video"
        )
        for r in records:
            age_days = (datetime.now(UTC) - r["created_at"]).total_seconds() / 86400
            print(
                f"  [ARCHIVE   ] {r['id'][:8]}  {r['niche_id']:<12} "
                f"created={r['created_at'].date()}  age={age_days:.1f}d  "
                f"hook={(r['hook_text'] or '')[:60]!r}"
            )

        if scheduled_anomaly:
            print()
            print(
                f"# DEFENSIVE: {len(scheduled_anomaly)} DRAFTED rows "
                f"ALSO have scheduled_for set — NOT auto-archiving (cleanup_safety.md)"
            )
            for r in scheduled_anomaly:
                print(
                    f"  [SKIP-SCHED] {r['id'][:8]}  {r['niche_id']:<12} "
                    f"sched_for={r['scheduled_for']}"
                )

        if not records:
            print("Nothing to archive.")
            return 0

        if not args.apply:
            print()
            print(
                f"DRY-RUN — no changes made. Re-run with --apply to "
                f"archive {len(records)} stale DRAFTED row(s)."
            )
            return 0

        backup_path = write_backup(records, args.backup_dir)
        print(f"# backup written: {backup_path}")

        reason = (
            f"DRAFTED never acquired source video — auto-archive after "
            f"{args.age_days}-day grace (on "
            f"{datetime.now(UTC).strftime('%Y-%m-%d')})"
        )
        updated = archive_blueprints(conn, records, reason)
        print(f"# {updated} blueprint(s) demoted to ARCHIVED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
