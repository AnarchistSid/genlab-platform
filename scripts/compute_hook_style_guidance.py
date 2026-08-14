#!/usr/bin/env python3
"""Phase 4.C session 1 — weekly hook style guidance aggregator.

Fires Sun 04:00 UTC (before the Sun 07:30 strategist run) via
``genlab-hook-style-guidance.timer``. For each niche:

  1. Read bandit_arms rows where arm_id ~= ``style:{niche}:%``
  2. Compute Beta posterior mean per style
  3. Rank top-3 by mean (tiebreak: n_plays)
  4. Persist to ``hook_style_guidance`` with this week's Monday
     as ``week_of``.

Session 2 writer wire reads
``SELECT top_styles FROM hook_style_guidance WHERE niche_id=? AND week_of=?``
and injects into the LLM system prompt as "style guidance".

## Idempotency

UNIQUE (niche_id, week_of). Re-running same day updates the row
via ON CONFLICT with the fresh posterior — useful when the
retrainer catches new reward signal within a week.

## Usage

    uv run python scripts/compute_hook_style_guidance.py
    uv run python scripts/compute_hook_style_guidance.py --dry-run
    uv run python scripts/compute_hook_style_guidance.py --niche gaming

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger("compute_hook_style_guidance")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--week-of", default=None,
                    help="Override week (YYYY-MM-DD, default = today's Monday)")
    return ap.parse_args(argv)


def _monday_of(d: date) -> date:
    """Match the convention used by portfolio_bandit + meta_strategist
    weekly runners — anchor on Monday so cross-analysis lines up."""
    return d - timedelta(days=d.weekday())


def _persist(
    conn, niche_id: str, week_of: date, top_styles: list, sample_size: int,
) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO hook_style_guidance
              (niche_id, week_of, top_styles, sample_size)
            VALUES (%s, %s, %s::jsonb, %s)
            ON CONFLICT (niche_id, week_of) DO UPDATE SET
              top_styles = EXCLUDED.top_styles,
              sample_size = EXCLUDED.sample_size,
              computed_at = NOW()
            """,
            (
                niche_id, week_of,
                json.dumps([s.to_dict() for s in top_styles]),
                sample_size,
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[style_guidance] persist failed niche=%s: %s", niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _run_niche(conn, niche_id: str, week_of: date, dry_run: bool) -> dict:
    from genlab_core.writing.style_guidance import compute_top_styles

    counts = {"styles": 0, "persisted": 0}
    top_styles, sample_size = compute_top_styles(conn, niche_id)
    counts["styles"] = len(top_styles)

    if not top_styles:
        print(f"  {niche_id}: no style arms with >=3 plays — skipping")
        return counts

    print(f"  {niche_id}: {len(top_styles)} top styles (total n={sample_size})")
    for s in top_styles:
        print(
            f"    rank={s.rank} {s.style_name} "
            f"reward={s.reward_mean:.3f} n={s.n_plays}"
        )

    if dry_run:
        return counts

    if _persist(conn, niche_id, week_of, top_styles, sample_size):
        counts["persisted"] = 1
        conn.commit()
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

    week_of = (
        date.fromisoformat(args.week_of) if args.week_of
        else _monday_of(date.today())
    )
    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    totals = {"styles": 0, "persisted": 0}
    print(f"\nComputing hook style guidance for week_of={week_of}")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(conn, niche_id, week_of, args.dry_run)
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[style_guidance] totals: styles=%d persisted=%d",
        totals["styles"], totals["persisted"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
