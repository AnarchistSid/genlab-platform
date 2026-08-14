#!/usr/bin/env python3
"""Phase 4.E session 2 — promote pool ideas to blueprints.

Fires every 6h via ``genlab-ideation-promoter.timer``. Per niche:

  1. If ``GENLAB_IDEATION_POOL_ENABLED`` is off → no-op.
  2. Count recent (last 24h) blueprints for the niche.
  3. If count < ``min_daily_target`` (default 1 per CLAUDE.md rule
     "1 reel per channel per day"), reserve + promote ideas to fill
     the gap.
  4. For each reserved idea:
       INSERT INTO blueprints (niche_id, story_id, title, hook_text,
         status, extra) VALUES (..., 'DRAFTED', {origin: 'ideation_pool',
         ...})
       Then link_to_blueprint to record consumed_by_blueprint_id.

## origin=ideation_pool

Persisted in blueprint.extra so session-3 analyzer can partition
reward: pool-origin blueprints vs trending-origin.

## Safety

  * Rollout flag GENLAB_IDEATION_POOL_ROLLOUT_PCT (0-100, default
    0). Same deterministic dice per niche as style guidance so
    A/B partition is stable.
  * Never promotes above min_daily_target — respects the
    1-reel-per-day cap that DailyCapEnforcer honours downstream.
  * Never touches PUBLISHED blueprints. New rows only.

## Usage

    uv run python scripts/promote_ideas_to_blueprints.py
    uv run python scripts/promote_ideas_to_blueprints.py --dry-run
    uv run python scripts/promote_ideas_to_blueprints.py --niche gaming

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

logger = logging.getLogger("promote_ideas_to_blueprints")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

_FLAG_ENV = "GENLAB_IDEATION_POOL_ENABLED"
_ROLLOUT_ENV = "GENLAB_IDEATION_POOL_ROLLOUT_PCT"

# CLAUDE.md rule: 1 reel per channel per day hard cap
DEFAULT_MIN_DAILY = 1


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--min-daily", type=int, default=DEFAULT_MIN_DAILY)
    return ap.parse_args(argv)


def _flag_enabled() -> bool:
    return os.environ.get(_FLAG_ENV, "").strip().lower() in {
        "1", "true", "yes",
    }


def _rollout_pct() -> int:
    raw = os.environ.get(_ROLLOUT_ENV, "0").strip()
    try:
        pct = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, pct))


def _niche_dice(niche_id: str) -> int:
    """Deterministic 0-99 per niche_id so A/B assignment is stable.
    Fires or doesn't fire consistently per niche — not per-idea —
    so within-niche behavior stays uniform for reward analysis."""
    h = hashlib.sha256(niche_id.encode("utf-8")).hexdigest()
    return int(h, 16) % 100


def _niche_should_fire(niche_id: str) -> bool:
    if not _flag_enabled():
        return False
    return _niche_dice(niche_id) < _rollout_pct()


def _count_recent_blueprints(conn, niche_id: str) -> int:
    """Blueprints created in last 24h — used to check if the pool
    should fill in. Fail-open to a high number so we DON'T promote
    on query error (better safe than double-post)."""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS n
            FROM blueprints
            WHERE niche_id = %s
              AND created_at >= NOW() - INTERVAL '24 hours'
            """,
            (niche_id,),
        ).fetchone()
        return int(row.get("n") if hasattr(row, "get") else row[0])
    except Exception as exc:
        logger.warning("[promote] recent-count failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return 999


def _create_blueprint_from_idea(conn, idea, dry_run: bool) -> str | None:
    """INSERT a new blueprint row from an ideation-pool idea.
    Returns blueprint_id on success, None on failure."""
    if dry_run:
        return "dry-run-id"

    # Stable story_id from idea title so re-runs don't duplicate
    # if the runner fires within a session (though reserve should
    # prevent that anyway).
    story_id = hashlib.sha256(
        f"ideation:{idea.batch_id}:{idea.id}".encode("utf-8")
    ).hexdigest()

    try:
        row = conn.execute(
            """
            INSERT INTO blueprints
              (niche_id, story_id, title, hook_text, status, extra)
            VALUES (%s, %s, %s, %s, 'DRAFTED', %s::jsonb)
            RETURNING id::text AS bp_id
            """,
            (
                idea.niche_id, story_id, idea.title, idea.hook_seed,
                json.dumps({
                    "origin": "ideation_pool",
                    "ideation_pool_idea_id": idea.id,
                    "ideation_pool_batch_id": idea.batch_id,
                    "ideation_pool_score": idea.score,
                    "ideation_pool_rationale": idea.rationale,
                }),
            ),
        ).fetchone()
        return row.get("bp_id") if hasattr(row, "get") else row[0]
    except Exception as exc:
        logger.warning(
            "[promote] blueprint insert failed niche=%s idea=%s: %s",
            idea.niche_id, idea.id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _run_niche(conn, niche_id: str, min_daily: int, dry_run: bool) -> dict:
    from genlab_core.intelligence.ideation_pool_consumer import (
        link_to_blueprint,
        release_reservation,
        reserve_top_pending,
    )

    counts = {
        "recent": 0, "gap": 0, "reserved": 0, "promoted": 0, "released": 0,
    }

    if not _niche_should_fire(niche_id):
        print(f"  {niche_id}: flag off OR rollout dice bucket excluded")
        return counts

    recent = _count_recent_blueprints(conn, niche_id)
    counts["recent"] = recent
    gap = max(0, min_daily - recent)
    counts["gap"] = gap

    if gap == 0:
        print(f"  {niche_id}: {recent} recent (>= {min_daily}) — no gap to fill")
        return counts

    print(f"  {niche_id}: {recent} recent, gap={gap} — reserving from pool")

    reserved = reserve_top_pending(conn, niche_id, limit=gap)
    counts["reserved"] = len(reserved)
    if not reserved:
        print(f"    pool empty for {niche_id}")
        return counts

    for idea in reserved:
        print(
            f"    [{'DRY' if dry_run else 'PROMOTE'}] "
            f"idea={idea.id[:8]} score={idea.score:.2f} "
            f"title={idea.title[:60]}"
        )
        bp_id = _create_blueprint_from_idea(conn, idea, dry_run)
        if bp_id is None:
            # Release the reservation so the next run can retry
            if not dry_run:
                release_reservation(conn, idea.id)
                counts["released"] += 1
            continue
        if not dry_run:
            link_to_blueprint(conn, idea.id, bp_id)
        counts["promoted"] += 1

    return counts


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    print(
        f"\nPromoting ideas to blueprints "
        f"(flag={_flag_enabled()}, rollout={_rollout_pct()}%, "
        f"min_daily={args.min_daily}, dry_run={args.dry_run})"
    )
    totals = {"recent": 0, "gap": 0, "reserved": 0, "promoted": 0, "released": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(conn, niche_id, args.min_daily, args.dry_run)
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[promote] totals: recent=%d gap=%d reserved=%d "
        "promoted=%d released=%d",
        totals["recent"], totals["gap"], totals["reserved"],
        totals["promoted"], totals["released"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
