#!/usr/bin/env python3
"""Phase 4.B session 1 — aesthetic training data labeler.

Fires nightly via ``genlab-aesthetic-training-labeler.timer``. For
each niche:

  1. Compute p20 and p80 of reward_48h over the last 30d for
     blueprints that have finalized rewards.
  2. Find blueprints with reward >= p80 (label=1) OR reward <= p20
     (label=0) that don't yet have a training row.
  3. Locate the rendered video (reuse the disk-glob from Phase 4.A
     session 3).
  4. Extract 15 aesthetic features via
     :func:`extract_aesthetic_features`.
  5. Persist to ``aesthetic_training_data``.

## Why nightly (not per-render)

Reward_48h needs a full 48h to settle. Nightly cadence lets us
snapshot the finalized reward buckets cleanly + backfill any
blueprint that just crossed into a bucket without racing the
metric collector.

## Rebuild-safe

UNIQUE (blueprint_id, video_hash). Re-scoring the same blueprint's
video is a no-op via ON CONFLICT DO NOTHING. A re-render (different
hash) creates a new training row so the model sees both variants.

## Usage

    uv run python scripts/run_aesthetic_training_labeler.py
    uv run python scripts/run_aesthetic_training_labeler.py --dry-run
    uv run python scripts/run_aesthetic_training_labeler.py --niche gaming
    uv run python scripts/run_aesthetic_training_labeler.py --lookback-days 30

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
from pathlib import Path

logger = logging.getLogger("run_aesthetic_training_labeler")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--lookback-days", type=int, default=30)
    return ap.parse_args(argv)


def _compute_percentile_thresholds(
    conn, niche_id: str, lookback_days: int,
) -> tuple[float, float] | None:
    """Return (p20, p80) of reward_48h for the niche. None when
    insufficient data — minimum 10 finalized rewards."""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS n,
                   percentile_cont(0.20) WITHIN GROUP (ORDER BY reward_48h)::float AS p20,
                   percentile_cont(0.80) WITHIN GROUP (ORDER BY reward_48h)::float AS p80
            FROM pending_feedback
            WHERE niche_id = %s
              AND reward_48h IS NOT NULL
              AND updated_at >= NOW() - (%s || ' days')::INTERVAL
            """,
            (niche_id, lookback_days),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            "[labeler] percentile query failed niche=%s: %s",
            niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    n = row.get("n") if hasattr(row, "get") else row[0]
    p20 = row.get("p20") if hasattr(row, "get") else row[1]
    p80 = row.get("p80") if hasattr(row, "get") else row[2]
    if n is None or n < 10 or p20 is None or p80 is None:
        return None
    return float(p20), float(p80)


def _find_labeled_candidates(
    conn, niche_id: str, p20: float, p80: float,
    lookback_days: int,
):
    """Blueprints in top-20 or bottom-20 reward buckets without a
    training row. Returns list of dicts.

    JOIN path (discovered 2026-08-14 during first prod dry-run):
    pending_feedback has no blueprint_id column — link via post_id
    → publishing_analytics.post_id → publishing_analytics.blueprint_id.
    Sibling class-of-bug to
    ``class-of-bug-join-shape-mismatch-signal-always-empty``.
    """
    try:
        rows = conn.execute(
            """
            SELECT pa.blueprint_id::text AS bp_id,
                   b.story_id,
                   pf.reward_48h,
                   CASE
                     WHEN pf.reward_48h >= %s THEN 1
                     ELSE 0
                   END AS label
            FROM pending_feedback pf
            JOIN publishing_analytics pa ON pa.post_id = pf.post_id
            JOIN blueprints b ON b.id = pa.blueprint_id
            LEFT JOIN aesthetic_training_data at
              ON at.blueprint_id = pa.blueprint_id
            WHERE pf.niche_id = %s
              AND pf.reward_48h IS NOT NULL
              AND (pf.reward_48h >= %s OR pf.reward_48h <= %s)
              AND pf.updated_at >= NOW() - (%s || ' days')::INTERVAL
              AND at.id IS NULL
            ORDER BY pf.updated_at DESC
            LIMIT 50
            """,
            (p80, niche_id, p80, p20, lookback_days),
        ).fetchall()
    except Exception as exc:
        logger.warning("[labeler] candidate query failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "blueprint_id": r.get("bp_id") if hasattr(r, "get") else r[0],
            "story_id": r.get("story_id") if hasattr(r, "get") else r[1],
            "reward_48h": float(
                r.get("reward_48h") if hasattr(r, "get") else r[2]
            ),
            "label": int(r.get("label") if hasattr(r, "get") else r[3]),
        }
        for r in rows or []
    ]


def _resolve_video(story_id: str | None) -> Path | None:
    """Same disk-glob strategy as Phase 4.A session 3 runner."""
    if not story_id:
        return None
    tmp_root_str = os.environ.get("GENLAB_TMP", "").strip()
    tmp_root = Path(tmp_root_str) if tmp_root_str else Path.cwd() / ".tmp"
    runs_dir = tmp_root / "runs"
    if not runs_dir.exists():
        return None
    prefix = story_id[:16]
    matches = sorted(
        runs_dir.glob(f"*/visuals/{story_id}/{prefix}_reel.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _persist(
    conn, blueprint_id: str, niche_id: str, video_hash: str,
    label: int, reward: float, features: dict,
) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO aesthetic_training_data
              (blueprint_id, niche_id, video_hash, label, reward_48h, features)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (blueprint_id, video_hash) DO NOTHING
            """,
            (blueprint_id, niche_id, video_hash, label, reward,
             json.dumps(features)),
        )
        return True
    except Exception as exc:
        logger.warning("[labeler] persist failed bp=%s: %s", blueprint_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _run_niche(conn, niche_id: str, lookback_days: int, dry_run: bool):
    from genlab_core.quality.aesthetic_features import (
        extract_aesthetic_features,
    )
    from genlab_core.quality.joint_score import _hash_video

    counts = {
        "candidates": 0, "positives": 0, "negatives": 0,
        "no_video": 0, "extract_failed": 0, "persisted": 0,
    }
    thresholds = _compute_percentile_thresholds(conn, niche_id, lookback_days)
    if thresholds is None:
        print(f"  {niche_id}: insufficient reward data (< 10 finalized) — skipping")
        return counts
    p20, p80 = thresholds

    candidates = _find_labeled_candidates(
        conn, niche_id, p20, p80, lookback_days,
    )
    counts["candidates"] = len(candidates)
    if not candidates:
        return counts
    print(
        f"  {niche_id}: p20={p20:.3f} p80={p80:.3f} · "
        f"{len(candidates)} candidates"
    )

    for cand in candidates:
        video = _resolve_video(cand["story_id"])
        if video is None:
            counts["no_video"] += 1
            continue
        feats = extract_aesthetic_features(video)
        if not feats.ok:
            counts["extract_failed"] += 1
            continue
        if cand["label"] == 1:
            counts["positives"] += 1
        else:
            counts["negatives"] += 1
        if dry_run:
            print(
                f"    [DRY] bp={cand['blueprint_id'][:8]} label={cand['label']} "
                f"reward={cand['reward_48h']:.3f}"
            )
            continue
        vh = _hash_video(video)
        if _persist(
            conn, cand["blueprint_id"], niche_id, vh,
            cand["label"], cand["reward_48h"], feats.to_dict(),
        ):
            counts["persisted"] += 1
    if not dry_run and counts["persisted"] > 0:
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

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    totals = {
        "candidates": 0, "positives": 0, "negatives": 0,
        "no_video": 0, "extract_failed": 0, "persisted": 0,
    }
    print(f"\nLabeling aesthetic training data (lookback={args.lookback_days}d)")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(conn, niche_id, args.lookback_days, args.dry_run)
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[labeler] totals: candidates=%d pos=%d neg=%d "
        "no_video=%d extract_failed=%d persisted=%d",
        totals["candidates"], totals["positives"], totals["negatives"],
        totals["no_video"], totals["extract_failed"], totals["persisted"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
