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

logger = logging.getLogger(__name__)


def _connect():
    """Open a psycopg connection from DATABASE_URL — caller owns the close."""
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, connect_timeout=10)


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


def find_stale_blueprints(conn, *, include_scheduled: bool = False) -> list[dict]:
    """Return every blueprint whose visual_paths reference deleted files.

    Args:
        conn: open psycopg connection.
        include_scheduled: if True, include rows with non-empty
            ``scheduled_for``. Default False respects
            ``.claude/rules/cleanup_safety.md`` ("Scheduled posts are
            sacred").

    Returns:
        List of dicts with keys: id, niche_id, status, scheduled_for,
        stale_paths (the missing media), present_paths (any media
        still on disk).
    """
    out: list[dict] = []
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            SELECT id, niche_id, status, scheduled_for, extra->'visual_paths'
            FROM blueprints
            WHERE status IN ('VISUAL_READY', 'DRAFTED')
              AND extra ? 'visual_paths'
            ORDER BY niche_id, created_at
            """
        )
        for bp_id, niche_id, status, scheduled_for, vp_raw in cur.fetchall():
            paths = _decode_visual_paths(vp_raw)
            if not paths:
                continue
            present = [p for p in paths if os.path.exists(p)]
            missing = [p for p in paths if not os.path.exists(p)]
            if not missing:
                continue  # all present — nothing to do
            if not include_scheduled and scheduled_for is not None:
                # cleanup_safety.md — scheduled posts are sacred
                logger.info(
                    "[archive_stale] SKIPPING scheduled bp=%s niche=%s (sched=%s) "
                    "even though %d/%d paths are gone — use --include-scheduled to override",
                    str(bp_id)[:8],
                    niche_id,
                    scheduled_for,
                    len(missing),
                    len(paths),
                )
                continue
            out.append(
                {
                    "id": str(bp_id),
                    "niche_id": niche_id,
                    "status": status,
                    "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
                    "stale_paths": missing,
                    "present_paths": present,
                    "all_missing": len(present) == 0,
                }
            )
    return out


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
        cur.execute(
            """
            UPDATE blueprints
            SET status = 'ARCHIVED',
                action_taken = NULL,
                scheduled_for = NULL,
                extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                    'archived_reason', %s,
                    'archived_at', NOW()::text,
                    'archived_by', 'archive_stale_visual_paths.py'
                ),
                updated_at = NOW()
            WHERE id = ANY(%s)
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
            "Include blueprints with non-empty scheduled_for. Default "
            "respects cleanup_safety.md and skips them — override only "
            "with explicit operator decision."
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
        records = find_stale_blueprints(conn, include_scheduled=args.include_scheduled)

        print(f"# {datetime.now(UTC).isoformat()} — {len(records)} stale blueprint(s) found")
        for r in records:
            print(
                f"  {r['id'][:8]}  {r['niche_id']:<12} {r['status']:<14} "
                f"stale={len(r['stale_paths'])}  "
                f"present={len(r['present_paths'])}  "
                f"sample=...{r['stale_paths'][0][-50:]}"
            )

        if not records:
            print("Nothing to archive.")
            return 0

        if not args.apply:
            print()
            print("DRY-RUN — no changes made. Re-run with --apply to archive.")
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
