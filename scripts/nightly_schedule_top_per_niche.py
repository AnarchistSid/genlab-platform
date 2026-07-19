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


# 2026-07-14: single source of truth for LLM refusal prefixes.
#
# Pre-fix: this file had its own hardcoded list of 6 refusal prefixes
# duplicated across two SQL blocks. `pre_render_quality._LLM_REFUSAL_
# PREFIXES` has 15. Divergence: prod "I need the Story Summary to
# write a hook for Moana" refusal PUBLISHED because this file's filter
# missed 'I need the%' — the pre_render_quality gate caught it (would
# have) but that gate was itself bypassed on ai_creators + gaming
# (fixed commit 7da5a204).
#
# Now imports the canonical list. When pre_render_quality's list
# grows, this filter grows automatically. Class-of-bug fix at the
# source instead of the symptom.
# 2026-07-19 lesson: yesterday's --cancel-scheduled-stale set
# scheduled_for=NULL on 14 blueprints whose media was GC'd + source
# lost (unrecoverable). Nightly scheduler re-picked 2 of them today
# because the query filtered only ``action_taken NOT IN ('rejected',
# 'archived')`` and these blueprints had ``action_taken='approved'``.
# The cleanup-marker in ``error_message`` was the durable signal that
# these are unrecoverable — schedulers need to honour it.
#
# Same marker is used by:
#   * cleanup_stale_visual_ready.sh --cancel-scheduled-stale
#     (writes ``cleanup:media_gc_removed_and_source_lost:...``)
#   * The re-cancel operator escape hatch (writes ``recancel:...``)
# LIKE '%cleanup:media_gc_removed%' catches both.
_CLEANUP_MARKER_EXCLUSION_CLAUSE = (
    "AND (error_message IS NULL "
    "OR error_message NOT LIKE '%cleanup:media_gc_removed%')"
)


def _refusal_where_clause(field: str = "hook") -> str:
    """Return a SQL fragment excluding LLM-refusal-prefix hooks.

    Emits ``AND {field} NOT ILIKE 'prefix1%%' AND {field} NOT ILIKE ...``
    for each prefix in pre_render_quality._LLM_REFUSAL_PREFIXES.

    The ``%%`` (two percent signs) escapes psycopg's parameter marker —
    what the DB sees is ``%`` (one).
    """
    try:
        from genlab_core.rendering.pre_render_quality import _LLM_REFUSAL_PREFIXES
    except ImportError:
        # Fail-open on old prefix set — never let an import error block
        # the whole scheduler. Better to schedule with weaker filter
        # than to leave niches empty for a full day.
        _LLM_REFUSAL_PREFIXES = (
            "i need to stop",
            "i cannot",
            "i can't",
            "i am unable",
            "i'm sorry",
            "i apologize",
        )
    clauses = []
    for prefix in _LLM_REFUSAL_PREFIXES:
        # Escape single quote by doubling per SQL convention. Prefixes
        # are literals from the module so no injection risk.
        escaped = prefix.replace("'", "''")
        clauses.append(f"          AND {field} NOT ILIKE '{escaped}%%'")
    return "\n".join(clauses)


# 2026-07-09 (task #611): pull-back branch buffer.
#
# When the primary candidate filter (scheduled_for IS NULL) returns no
# rows for a niche, but that niche has VISUAL_READY blueprints already
# committed to far-future dates, we can shift the earliest of those
# back to the target slot. This buffer is the minimum gap between the
# future slot we're willing to steal FROM and the target slot we're
# scheduling FOR.
#
# = 2 means: don't cannibalize tomorrow's slot to fill today. If we
# pulled from target+1, next night's run (target = old target+1) would
# find that slot empty and cascade the same problem forward. Requiring
# >= 2 days of buffer means the pull-back leaves the immediately-
# adjacent slot alone, breaking the cascade.
#
# Worst case with a fully stalled pipeline: the queue's far tail
# shortens by ~1 day per night as we consume future slots. That's
# graceful degradation vs the fail-immediately behavior that caused
# sports to miss 2026-07-09.
MIN_PULLBACK_DAYS = 2


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


def pick_top_per_niche(
    cur,
    needing: set[str],
    target_date: date | None = None,
) -> list[dict]:
    """Return one row per niche in ``needing`` — the top-scoring
    VISUAL_READY that passes the LLM-refusal filter.

    Two-pass selection:

    1. **Primary (fresh)**: ``scheduled_for IS NULL`` — never-scheduled
       candidates freshly produced by the pipeline. Highest signal:
       these reflect today's ranking, haven't been reached-ahead by a
       previous nightly run.
    2. **Pull-back (recovery)**: for any niche in ``needing`` still
       without a pick, look for VISUAL_READY blueprints whose
       ``scheduled_for::date >= target_date + MIN_PULLBACK_DAYS``. Pick
       the earliest. ``schedule_blueprints`` will rewrite
       ``scheduled_for = target_slot`` in the follow-up UPDATE, so the
       future-committed row is effectively shifted back to the target.

    Rationale (2026-07-09, task #611): sports missed 2026-07-09 because
    its queue had 5 VISUAL_READY blueprints all scheduled for 07-10
    through 07-14 (a previous run had reached ahead). The primary
    filter's ``scheduled_for IS NULL`` correctly rejected all 5 to avoid
    double-scheduling, but no fallback existed. Runner exited 1
    (correct — the slot was empty) and alerted, but nobody handled the
    alert overnight and operator had to manually SQL-shift a blueprint.

    ``target_date=None`` disables pull-back entirely — pin-tested to
    preserve the primary-only shape when tests call this directly
    without a date. Production always passes a date via ``main()``.
    """
    if not needing:
        return []
    cur.execute(
        f"""
        SELECT DISTINCT ON (niche_id)
          id, niche_id, priority_score, hook, title, created_at
        FROM blueprints
        WHERE niche_id = ANY(%s)
          AND status = 'VISUAL_READY'
          AND scheduled_for IS NULL
          AND (action_taken IS NULL OR action_taken NOT IN ('rejected', 'archived'))
          {_CLEANUP_MARKER_EXCLUSION_CLAUSE}
          AND hook IS NOT NULL
{_refusal_where_clause("hook")}
          AND length(hook) BETWEEN 15 AND 100
        ORDER BY niche_id, priority_score DESC NULLS LAST, created_at ASC
        """,
        (list(needing),),
    )
    fresh = list(cur.fetchall())
    if target_date is None:
        return fresh
    fresh_niches = {row["niche_id"] for row in fresh}
    still_needing = needing - fresh_niches
    if not still_needing:
        return fresh
    pullback = _pick_pullback_candidates(cur, still_needing, target_date)
    return fresh + pullback


def _pick_pullback_candidates(
    cur,
    needing: set[str],
    target_date: date,
) -> list[dict]:
    """Fallback picker: pull an existing VISUAL_READY blueprint from a
    far-future scheduled slot back to the target slot.

    Filters mirror ``pick_top_per_niche``'s primary query EXCEPT:

    * ``scheduled_for::date >= target_date + MIN_PULLBACK_DAYS`` instead
      of ``scheduled_for IS NULL`` — pull-back only reaches into slots
      at least ``MIN_PULLBACK_DAYS`` (=2) ahead of target, never
      cannibalizes tomorrow's slot to fill today.
    * ``ORDER BY scheduled_for ASC`` — pull the *earliest* far-future
      blueprint so we minimally disturb the queue tail. If sports has
      07-11, 07-12, 07-13 and target is 07-09, we take 07-11 (leaving
      the tail 07-12, 07-13 intact for future nights). We do NOT take
      07-13 first because that would strand 07-11 as the new gap.

    Not called when ``needing`` is empty. Caller (``pick_top_per_niche``)
    guarantees this.
    """
    min_pullback = target_date + timedelta(days=MIN_PULLBACK_DAYS)
    cur.execute(
        f"""
        SELECT DISTINCT ON (niche_id)
          id, niche_id, priority_score, hook, title, created_at,
          scheduled_for AS pullback_from
        FROM blueprints
        WHERE niche_id = ANY(%s)
          AND status = 'VISUAL_READY'
          AND scheduled_for::date >= %s
          AND (action_taken IS NULL OR action_taken NOT IN ('rejected', 'archived'))
          {_CLEANUP_MARKER_EXCLUSION_CLAUSE}
          AND hook IS NOT NULL
{_refusal_where_clause("hook")}
          AND length(hook) BETWEEN 15 AND 100
        ORDER BY niche_id, scheduled_for ASC, priority_score DESC NULLS LAST
        """,
        (list(needing), min_pullback),
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
            picks = pick_top_per_niche(cur, needing, target_date=target_date)

            # Distinguish fresh (scheduled_for was NULL) from pull-back
            # picks (scheduled_for was a future date we're stealing back).
            # _pick_pullback_candidates returns rows with a
            # ``pullback_from`` field; the primary query does not.
            pullback_niches = {p["niche_id"] for p in picks if p.get("pullback_from")}
            if pullback_niches:
                print(
                    f"↩ Pulled back from far-future slots (task #611): "
                    f"{sorted(pullback_niches)} — pipeline hadn't produced "
                    "fresh candidates; recovered from future-committed queue."
                )

            # Warn if any niche had no schedulable candidate AT ALL
            # (neither fresh nor pull-back). This is a genuine empty
            # queue — pipeline stalled AND no future-committed rows.
            picked_niches = {p["niche_id"] for p in picks}
            missing = needing - picked_niches
            if missing:
                print(
                    f"⚠️  No schedulable candidate for: {sorted(missing)} "
                    "(empty VISUAL_READY queue AND no pull-back available)"
                )

            if args.dry_run:
                for p in picks:
                    tag = "PULL" if p.get("pullback_from") else "FRESH"
                    print(
                        f"  DRY  [{tag}] {p['niche_id']:12s}  "
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
