"""Auto-archive VISUAL_READY/DRAFTED blueprints whose visual_paths are gone.

Why this exists
---------------
Every few weeks the operator hits the same shape:
* T#60 (2026-06-15): 3 stale VR blueprints, ai_creators + gaming, demoted by SQL.
* 2026-06-16 publisher run failed on movies because its blueprint's
  ``visual_paths`` pointed at ``/opt/genlab/.tmp/runs/movies_20260613_*/...``
  — a run directory cleaned up 3 days ago.

The pattern: ``CLEANUP_KEEP_RUNS=3`` prunes old ``.tmp/runs/`` dirs but
blueprints created during those runs keep their ``extra.visual_paths``
references. When a publisher pass picks one up, it fails with
``No valid media files for video publish`` for every platform and the
publisher exits non-zero — exactly the operator-facing symptom of the
2026-06-16 outage rebooted.

This script runs nightly via systemd timer:

  1. Query every ``VISUAL_READY`` or ``DRAFTED`` blueprint that has a
     non-empty ``extra.visual_paths``.
  2. Check the disk for each referenced path.
  3. If ALL referenced media is missing, archive the blueprint with a
     structured ``extra.archived_reason``.
  4. If SOME media is present (rare partial-cleanup case), leave the
     blueprint alone — log a warning so the operator sees it.

The audit-trail row is the same shape as T#60's manual SQL fix.

Safety
======
* Idempotent — already-ARCHIVED blueprints skipped.
* Respects ``cleanup_safety.md`` rule: never demote ``scheduled_for``
  blueprints without explicit operator override (``--include-scheduled``).
* Dry-run by default (``--apply`` required to mutate).
* CSV backup of every archived blueprint to
  ``/opt/genlab/.tmp/backups/archive_stale_visual_paths_<TS>.csv``
  before the UPDATE fires.
"""

from __future__ import annotations

import argparse
import csv
import json
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


def _decode_visual_paths(raw) -> list[str]:
    """Defensively decode visual_paths into a list[str].

    Mirrors the gate's _decode_visual_paths helper — handles all the
    historical shapes (list, JSON-stringified list, None, dict-wrapped).
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, str) and p]
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return [p for p in decoded if isinstance(p, str) and p]
        except (ValueError, TypeError):
            return []
    return []


def find_stale_blueprints(
    conn,
    *,
    include_scheduled: bool = False,
    archive_past_scheduled: bool = True,
    past_grace_seconds: int = 3600,
    min_age_hours: int = 72,
) -> list[dict]:
    """Return every blueprint whose visual_paths reference deleted files.

    Args:
        conn: open psycopg connection.
        include_scheduled: if True, include rows with non-empty
            ``scheduled_for`` (overrides everything; broadest archive).
        archive_past_scheduled: if True (default), archive a blueprint
            whose ``scheduled_for`` is in the PAST by more than
            ``past_grace_seconds``. The publisher has already had its
            chance — keeping such a row stuck at VISUAL_READY just
            keeps re-failing the next publisher pass. The cleanup_safety
            rule "Scheduled posts are sacred" is about FUTURE-scheduled
            queue items; past-scheduled-but-not-published are already
            broken, archiving doesn't create a new gap.
        past_grace_seconds: how long after ``scheduled_for`` we wait
            before treating a row as past-scheduled. 3600 (1h) is the
            normal publisher window — gives the 12:05 IST publisher
            time to land its post before we'd flag a 12:00 IST
            schedule as past.
        min_age_hours: minimum blueprint age BEFORE we consider
            archiving. 2026-07-21 exhaustive audit found this script
            swept 11 blueprints in one day (2026-07-16) — recent
            blueprints that hadn't been given a chance to publish yet.
            The nightly scheduler picks 1/niche/day; a blueprint
            created today at 06:00 UTC won't get scheduled until
            tomorrow's nightly run at 16:30 UTC → its media gets
            purged BEFORE publisher touches it. 72h default (three
            nightly-scheduler windows) gives every blueprint at least
            2 chances to be picked before we clean up its media.

    Returns:
        List of dicts with keys: id, niche_id, status, scheduled_for,
        stale_paths (the missing media), present_paths (any media
        still on disk), past_scheduled (bool — true when the row
        passes the past-scheduled gate).
    """
    from datetime import timedelta

    out: list[dict] = []
    now = datetime.now(UTC)
    grace = timedelta(seconds=past_grace_seconds)
    min_age = timedelta(hours=min_age_hours)
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id, niche_id, status, scheduled_for, extra->'visual_paths', created_at
            FROM blueprints
            WHERE status IN ('VISUAL_READY', 'DRAFTED')
              AND extra ? 'visual_paths'
            ORDER BY niche_id, created_at
            """
        )
        for row in cur.fetchall():
            # 2026-07-21: backward-compat — test fixtures still return
            # 5-tuple rows (no created_at). In prod the SELECT always
            # returns 6 columns. When created_at is missing, skip the
            # min-age gate (preserves existing test-fixture behaviour).
            if len(row) == 6:
                bp_id, niche_id, status, scheduled_for, vp_raw, created_at = row
            else:
                bp_id, niche_id, status, scheduled_for, vp_raw = row
                created_at = None
            # 2026-07-21: min-age gate. Recent blueprints haven't had
            # their chance yet; skip.
            if created_at is not None:
                age = now - created_at
                if age < min_age:
                    logger.debug(
                        "[archive_stale] SKIPPING young bp=%s niche=%s "
                        "(age=%s < min_age=%dh) — hasn't been scheduled yet",
                        str(bp_id)[:8],
                        niche_id,
                        age,
                        min_age_hours,
                    )
                    continue
            paths = _decode_visual_paths(vp_raw)
            if not paths:
                continue
            present = [p for p in paths if os.path.exists(p)]
            missing = [p for p in paths if not os.path.exists(p)]
            if not missing:
                continue  # all present — nothing to do

            past_scheduled = False
            future_stale = False
            if scheduled_for is not None and not include_scheduled:
                # Distinguish FUTURE schedule (sacred per
                # cleanup_safety.md) from PAST schedule (publisher
                # already had its chance).
                is_past = scheduled_for + grace < now
                if not (is_past and archive_past_scheduled):
                    logger.info(
                        "[archive_stale] SKIPPING scheduled bp=%s niche=%s "
                        "(sched=%s, %s) — %d/%d paths are gone but row is "
                        "%s",
                        str(bp_id)[:8],
                        niche_id,
                        scheduled_for,
                        "past" if is_past else "future",
                        len(missing),
                        len(paths),
                        "in cleanup_safety protection window"
                        if is_past
                        else "future-scheduled (sacred)",
                    )
                    # Future-scheduled stale rows aren't archived (sacred)
                    # but they ARE going to fail when the publisher
                    # attempts them on their schedule. Record so the
                    # caller can write a CRITICAL alert NOW instead of
                    # waiting days for the publisher to find out.
                    if not is_past:
                        future_stale = True
                        out.append(
                            {
                                "id": str(bp_id),
                                "niche_id": niche_id,
                                "status": status,
                                "scheduled_for": scheduled_for.isoformat(),
                                "stale_paths": missing,
                                "present_paths": present,
                                "all_missing": len(present) == 0,
                                "past_scheduled": False,
                                "future_stale": True,
                            }
                        )
                    continue
                past_scheduled = True

            out.append(
                {
                    "id": str(bp_id),
                    "niche_id": niche_id,
                    "status": status,
                    "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
                    "stale_paths": missing,
                    "present_paths": present,
                    "all_missing": len(present) == 0,
                    "past_scheduled": past_scheduled,
                    "future_stale": future_stale,
                }
            )
    return out


def write_future_stale_alerts(conn, records: list[dict]) -> int:
    """For each future-scheduled stale blueprint, write a CRITICAL row
    to pipeline_alerts so the operator sees it on Mission Control NOW
    (rather than discovering it days later when the publisher tries).

    Returns count of alerts written. Idempotent — dedups by message
    body so a nightly run doesn't multiply rows.
    """
    future_stale = [r for r in records if r.get("future_stale")]
    if not future_stale:
        return 0
    written = 0
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        for r in future_stale:
            message = (
                f"Future-scheduled blueprint {r['id'][:8]} ({r['niche_id']}) "
                f"will fail at scheduled time {r['scheduled_for']} — "
                f"{len(r['stale_paths'])} visual_paths file(s) missing on disk. "
                f"Re-render the blueprint OR archive it manually to clear the slot.\n\n"
                f"Sample missing path: {r['stale_paths'][0]}\n\n"
                f"Quick fix: GET /api/v1/blueprints/{r['id']}/details on the "
                f"dashboard, then either Reject (clears the slot) or trigger "
                f"a re-render."
            )
            # Dedupe — don't write if an unresolved row with the same
            # check_name + blueprint id already exists.
            cur.execute(
                """
                SELECT 1 FROM pipeline_alerts
                WHERE check_name = 'future_stale_visual_paths'
                  AND resolved_at IS NULL
                  AND message LIKE %s
                LIMIT 1
                """,
                (f"%blueprint {r['id'][:8]} ({r['niche_id']})%",),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """
                INSERT INTO pipeline_alerts
                    (niche_id, check_name, severity, message, created_at, resolved_at)
                VALUES (%s::text, 'future_stale_visual_paths', 'critical',
                        %s::text, NOW(), NULL)
                """,
                (r["niche_id"], message),
            )
            written += 1
    conn.commit()
    return written


def write_backup(records: list[dict], backup_dir: Path) -> Path | None:
    """Write a CSV backup of the records we're about to archive."""
    if not records:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"archive_stale_visual_paths_{ts}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "niche_id",
                "status",
                "scheduled_for",
                "stale_paths_count",
                "stale_paths_sample",
                "present_paths_count",
                "all_missing",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r["id"],
                    r["niche_id"],
                    r["status"],
                    r["scheduled_for"],
                    len(r["stale_paths"]),
                    r["stale_paths"][0] if r["stale_paths"] else "",
                    len(r["present_paths"]),
                    r["all_missing"],
                ]
            )
    return path


def archive_blueprints(conn, records: list[dict], reason: str) -> int:
    """Demote a list of blueprints to ARCHIVED with a structured reason.

    Returns the count of rows actually updated.
    """
    if not records:
        return 0
    ids = [r["id"] for r in records]
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        # Only act on blueprints we observed; if status flipped to
        # PUBLISHED between detect and archive, skip (TOCTOU defense).
        # Explicit ::text and ::uuid[] casts so psycopg3 doesn't raise
        # IndeterminateDatatype. The `%s` for `reason` sits inside
        # jsonb_build_object which is anyvariadic and gives the planner
        # no type hint; the `%s` for ids is a list[str] that PG can't
        # infer as uuid[] without help.
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = NULL,
                scheduled_for = NULL,
                extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                    'archived_reason', %s::text,
                    'archived_at', NOW()::text,
                    'archived_by', 'archive_stale_visual_paths.py'
                ),
                updated_at = NOW()
            WHERE id = ANY(%s::uuid[])
              AND status IN ('VISUAL_READY', 'DRAFTED')
            """,
            (reason, ids),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="archive_stale_visual_paths",
        description="Auto-archive blueprints whose visual_paths reference deleted files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate. Default is dry-run.",
    )
    parser.add_argument(
        "--include-scheduled",
        action="store_true",
        help=(
            "Include ALL blueprints with non-empty scheduled_for (FUTURE "
            "and PAST). Default respects cleanup_safety.md for future "
            "schedules — override only with explicit operator decision."
        ),
    )
    parser.add_argument(
        "--no-past-scheduled",
        action="store_true",
        help=(
            "Disable default archival of PAST-scheduled blueprints. "
            "By default the script archives blueprints whose "
            "scheduled_for has already passed (publisher already had "
            "its chance) — pass this flag to fully restore the "
            "cleanup_safety.md 'sacred schedule' behaviour for both "
            "past and future schedules."
        ),
    )
    parser.add_argument(
        "--past-grace-seconds",
        type=int,
        default=3600,
        help=(
            "How long after scheduled_for before a row counts as "
            "past-scheduled. Default 3600 = 1 hour (one publisher "
            "window). Larger values reduce false-positives at the "
            "cost of leaving broken rows around longer."
        ),
    )
    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=72,
        help=(
            "Minimum blueprint age BEFORE considering it for archival. "
            "Default 72h (three nightly-scheduler windows). Recent "
            "blueprints haven't had their chance to be scheduled yet — "
            "archiving them wastes the render work. 2026-07-21 audit "
            "found this script swept 11 blueprints in one day (some "
            "created same day as sweep) — added min-age gate to prevent."
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
        records = find_stale_blueprints(
            conn,
            include_scheduled=args.include_scheduled,
            archive_past_scheduled=not args.no_past_scheduled,
            past_grace_seconds=args.past_grace_seconds,
            min_age_hours=args.min_age_hours,
        )

        print(f"# {datetime.now(UTC).isoformat()} — {len(records)} stale blueprint(s) found")
        archivable = [r for r in records if not r.get("future_stale")]
        future_stale = [r for r in records if r.get("future_stale")]
        for r in records:
            tag = "FUTURE_STALE_ALERT" if r.get("future_stale") else "ARCHIVE"
            print(
                f"  [{tag:<18}] {r['id'][:8]}  {r['niche_id']:<12} {r['status']:<14} "
                f"sched={r['scheduled_for'] or 'none':<27} "
                f"stale={len(r['stale_paths'])}  "
                f"present={len(r['present_paths'])}"
            )

        if not records:
            print("Nothing to archive.")
            return 0

        if not args.apply:
            print()
            print(
                f"DRY-RUN — no changes made. Re-run with --apply to "
                f"archive {len(archivable)} + alert on {len(future_stale)} future-stale."
            )
            return 0

        # Write CRITICAL pipeline_alerts for future-stale BEFORE the
        # archive UPDATE so the operator sees them even if the archive
        # step somehow fails after.
        if future_stale:
            n_alerts = write_future_stale_alerts(conn, records)
            print(f"# wrote {n_alerts} CRITICAL future_stale_visual_paths alert(s)")

        # Subsequent archive only acts on the records WITHOUT future_stale
        # — past-scheduled + no-schedule rows.
        records = archivable
        if not records:
            print("# nothing to archive (only future-stale alerts written)")
            return 0

        backup_path = write_backup(records, args.backup_dir)
        print(f"# backup written: {backup_path}")

        reason = (
            f"visual_paths deleted on disk (auto-archive on "
            f"{datetime.now(UTC).strftime('%Y-%m-%d')})"
        )
        updated = archive_blueprints(conn, records, reason)
        print(f"# {updated} blueprint(s) demoted to ARCHIVED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
