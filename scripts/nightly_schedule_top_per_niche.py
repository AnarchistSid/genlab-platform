#!/usr/bin/env python3
"""Nightly auto-scheduler — the Path B safety net for autonomous
publishing (2026-07-06).

Purpose
-------
Every night at ~22:00 IST (16:30 UTC), this script picks the top-scoring
VISUAL_READY blueprint per niche and schedules it for tomorrow's
publisher fire at 06:00 UTC (11:30 IST). Publisher then picks it up at
06:35 UTC (12:05 IST).

Why this exists
---------------
The auto-approver ships in observation-only mode until per-niche
calibration data proves the gate is trustworthy (see
``[[agent-learning-state-2026-06-30]]`` for calibration state). For
niches where enforce mode isn't safe yet (gaming at 22.4% agreement,
sports/movies/anime lacking calibration data), the approval → schedule
step is a manual bottleneck. This script fills that gap.

It COMPOSES with the auto-approver, doesn't replace it:
* Auto-approver picks up qualifying blueprints throughout the day
  (every 30 min). Those get ``action_taken=approved`` +
  ``scheduled_for=<next available slot>``.
* This script runs late at night and schedules only niches that DON'T
  already have a blueprint scheduled for tomorrow's slot. Idempotent —
  if auto-approver already handled ai_creators, this script skips
  ai_creators and only schedules the other 4.

Filters — same as tonight's manual SQL
--------------------------------------
* status = VISUAL_READY, scheduled_for IS NULL
* action_taken IS NULL or NOT IN (rejected, archived)
* hook NOT ILIKE 'I need to stop%', 'I cannot%', 'I can''t%',
  'I am unable%', 'I''m sorry%', 'I apologize%' — LLM refusal filter
  (real anime bug from 2026-07-05: 2 of 5 top anime blueprints had
  refusal text in hook field)
* length(hook) BETWEEN 15 AND 100

Ranking
-------
``priority_score DESC NULLS LAST, created_at ASC`` — highest-scoring
first, oldest as tiebreaker (so long-waiting content ships eventually).

Exit codes
----------
* ``0`` — all 5 niches now have tomorrow scheduled (either by this
  script or by auto-approver earlier)
* ``1`` — at least one niche has no schedulable candidate (empty
  VISUAL_READY queue or all filtered out) — alertable
* ``2`` — argparse error
* ``3`` — DB connect error

Usage
-----

::

    # Preview only — no writes
    uv run python3 scripts/nightly_schedule_top_per_niche.py --dry-run

    # Execute — schedules for tomorrow (Mon 12:05 IST publisher fire)
    uv run python3 scripts/nightly_schedule_top_per_niche.py

Systemd wraps this at ``deploy/systemd-phase2/genlab-nightly-schedule.
{service,timer}`` — fires 16:30 UTC daily (22:00 IST).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")


def _load_env_file(path: Path) -> None:
    """Same shape as verify_intelligent_transform's loader — dep-free."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set; source /opt/genlab/.env before running.")
    return psycopg.connect(url, row_factory=dict_row)


def compute_target_slot(now_utc: datetime | None = None) -> datetime:
    """Tomorrow's publisher slot in UTC.

    Publisher fires 12:05 IST = 06:35 UTC. We set scheduled_for to
    06:00 UTC (11:30 IST) for a 35-min buffer — publisher sees it as
    already-past-due at fire time and picks it up immediately.

    "Tomorrow" is computed against UTC current date so it matches the
    UTC-based Postgres ``current_date + 1`` semantics.
    """
    if now_utc is None:
        now_utc = datetime.now(UTC)
    tomorrow_utc: date = now_utc.date() + timedelta(days=1)
    return datetime.combine(tomorrow_utc, time(6, 0, 0), tzinfo=UTC)


def niches_needing_scheduling(cur, target_date: date) -> set[str]:
    """Return niches that DON'T yet have anything scheduled on target_date.
    This is what makes the script idempotent with the auto-approver.

    A niche is considered "already scheduled" for target_date if it has
    a blueprint whose ``scheduled_for::date`` matches AND that blueprint
    is in one of the two shapes that count as scheduled:

    1. **Legacy** — ``status IN ('SCHEDULED', 'PUBLISHED')``. Any
       historical rows written before the 2026-07-06 live-fire fix.
    2. **Current** — ``status = 'VISUAL_READY' AND action_taken =
       'approved'``. Matches what ``schedule_blueprints`` (this file,
       line 189+) and the auto-approver both write. Publisher's
       ``blueprint_selector.select_blueprint`` only sees ``VISUAL_READY``
       so we can't advance to ``SCHEDULED`` at write time — the
       read side has to catch that shape.

    2026-07-08 bug: this query originally only checked case 1. Result:
    sports' Arsenal-Newcastle blueprint (VISUAL_READY + action_taken=
    approved + scheduled_for=today) was invisible to idempotency, so
    nightly cron scheduled a stale 11-day-old Getafe-Barcelona
    blueprint on top of it. ``DailyCapEnforcer`` at publish time
    prevented the double-publish but the queue clutter was real and
    the operator had to demote by hand. See task #570.
    """
    cur.execute(
        """
        SELECT niche_id
        FROM blueprints
        WHERE scheduled_for::date = %s
          AND (
            status IN ('SCHEDULED', 'PUBLISHED')
            OR (status = 'VISUAL_READY' AND action_taken = 'approved')
          )
        GROUP BY niche_id
        """,
        (target_date,),
    )
    already = {row["niche_id"] for row in cur.fetchall()}
    return set(NICHES) - already


def pick_top_per_niche(cur, needing: set[str]) -> list[dict]:
    """Return one row per niche in ``needing`` — the top-scoring
    VISUAL_READY that passes the LLM-refusal filter.
    """
    if not needing:
        return []
    cur.execute(
        """
        SELECT DISTINCT ON (niche_id)
          id, niche_id, priority_score, hook, title, created_at
        FROM blueprints
        WHERE niche_id = ANY(%s)
          AND status = 'VISUAL_READY'
          AND scheduled_for IS NULL
          AND (action_taken IS NULL OR action_taken NOT IN ('rejected', 'archived'))
          AND hook IS NOT NULL
          AND hook NOT ILIKE 'I need to stop%%'
          AND hook NOT ILIKE 'I cannot%%'
          AND hook NOT ILIKE 'I can''t%%'
          AND hook NOT ILIKE 'I am unable%%'
          AND hook NOT ILIKE 'I''m sorry%%'
          AND hook NOT ILIKE 'I apologize%%'
          AND length(hook) BETWEEN 15 AND 100
        ORDER BY niche_id, priority_score DESC NULLS LAST, created_at ASC
        """,
        (list(needing),),
    )
    return list(cur.fetchall())


def schedule_blueprints(
    cur,
    picks: list[dict],
    target_slot: datetime,
) -> list[dict]:
    """Set action_taken=approved + scheduled_for=slot on each pick,
    LEAVING status='VISUAL_READY' untouched. Returns the mutated rows.

    2026-07-06 live-fire fix: publisher's ``blueprint_selector.
    select_blueprint`` calls ``get_blueprints_by_status('VISUAL_READY',
    niche_id=...)``. Setting ``status='SCHEDULED'`` here made the
    resulting blueprints INVISIBLE to publisher — Mon's 12:05 IST
    fire published only 1 gaming blueprint (dashboard-approved,
    VISUAL_READY) while the 5 blueprints I'd SQL-scheduled overnight
    with status='SCHEDULED' were skipped entirely.

    The correct shape mirrors the auto-approver's UPDATE
    (see genlab_core.scheduling.auto_approver): only touch
    action_taken + reviewed_at + scheduled_for. Status remains
    VISUAL_READY until publisher advances it to PUBLISHED (or the
    approval gate rejects and it's demoted elsewhere).
    """
    if not picks:
        return []
    ids = [row["id"] for row in picks]
    cur.execute(
        """
        UPDATE blueprints
        SET action_taken = 'approved',
            reviewed_at = now(),
            scheduled_for = %s,
            updated_at = now()
        WHERE id = ANY(%s)
        RETURNING niche_id, id::text AS blueprint_id,
                  priority_score, hook, scheduled_for
        """,
        (target_slot, ids),
    )
    return list(cur.fetchall())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview picks; do NOT write to database",
    )
    ap.add_argument(
        "--env-file",
        default="/opt/genlab/.env",
        help="Path to .env (default /opt/genlab/.env)",
    )
    args = ap.parse_args()

    _load_env_file(Path(args.env_file))

    target_slot = compute_target_slot()
    target_date = target_slot.date()
    print(f"Target slot: {target_slot.isoformat()} (scheduling for {target_date})")

    try:
        with _connect() as conn, conn.cursor() as cur:
            needing = niches_needing_scheduling(cur, target_date)
            already = set(NICHES) - needing
            if already:
                print(f"Already scheduled for {target_date}: {sorted(already)}")
            if not needing:
                print("Every niche already has tomorrow scheduled. Done.")
                return 0

            print(f"Scheduling for: {sorted(needing)}")
            picks = pick_top_per_niche(cur, needing)

            # Warn if any niche had no schedulable candidate
            picked_niches = {p["niche_id"] for p in picks}
            missing = needing - picked_niches
            if missing:
                print(
                    f"⚠️  No schedulable candidate for: {sorted(missing)} "
                    "(empty VISUAL_READY queue or all filtered)"
                )

            if args.dry_run:
                for p in picks:
                    print(
                        f"  DRY  {p['niche_id']:12s}  "
                        f"score={p['priority_score']:.4f}  "
                        f"hook={p['hook'][:60]!r}"
                    )
                return 1 if missing else 0

            rows = schedule_blueprints(cur, picks, target_slot)
            conn.commit()
            for r in rows:
                print(
                    f"  ✓ {r['niche_id']:12s}  "
                    f"score={r['priority_score']:.4f}  "
                    f"hook={r['hook'][:60]!r}"
                )

            return 1 if missing else 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
