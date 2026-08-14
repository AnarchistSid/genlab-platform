#!/usr/bin/env python3
"""Phase 4.A session 3 — content quality scorer runner.

Fires every 30 min via ``genlab-quality-scorer.timer``. For each
niche:

  1. Find blueprints published in the last 48h that don't yet have
     a content_quality_scores row (or have an OLDER one — the video
     may have been re-rendered).
  2. Locate the rendered video file (extra->'media'->'render_path'
     or similar).
  3. Extract all 7 features + fuse into joint_score.
  4. Persist to content_quality_scores.

## Idempotency

UNIQUE (blueprint_id, video_hash). ON CONFLICT DO NOTHING when
the same hash is already scored. A re-render with different bytes
produces a different hash → new row (history preserved).

## Cost

FFmpeg-only extraction takes ~2-5 seconds per video on the VPS.
30-min cadence × ~5 new blueprints per day = trivial CPU load.

## Usage

    uv run python scripts/run_quality_scorer.py
    uv run python scripts/run_quality_scorer.py --dry-run
    uv run python scripts/run_quality_scorer.py --niche gaming
    uv run python scripts/run_quality_scorer.py --blueprint-id <uuid>

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
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("run_quality_scorer")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Per-niche brand accent color from CLAUDE.md. Keeps the runner
# self-contained rather than pulling from every niche's
# visuals.yaml (which needs the pipeline path resolution).
_BRAND_COLORS = {
    "ai_creators": "#00D4FF",
    "anime": "#7B3FE4",
    "gaming": "#f97316",
    "movies": "#C9A84C",
    "sports": "#FF2040",
}


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--blueprint-id", default=None,
                    help="Score a specific blueprint id (skip status filter)")
    ap.add_argument("--lookback-hours", type=int, default=48)
    return ap.parse_args(argv)


def _find_unscored_blueprints(conn, niche_id: str, lookback_hours: int,
                              blueprint_id_filter: str | None):
    """Return blueprints published within lookback that lack a
    quality_scores row for their current video_path."""
    if blueprint_id_filter:
        try:
            rows = conn.execute(
                """
                SELECT b.id::text AS id, b.niche_id, b.extra, b.story_id
                FROM blueprints b
                WHERE b.id = %s::uuid
                LIMIT 1
                """,
                (blueprint_id_filter,),
            ).fetchall()
        except Exception as exc:
            logger.warning("[scorer] specific-blueprint query failed: %s", exc)
            return []
    else:
        try:
            rows = conn.execute(
                """
                SELECT b.id::text AS id, b.niche_id, b.extra, b.story_id
                FROM blueprints b
                WHERE b.niche_id = %s
                  AND b.status IN ('VISUAL_READY', 'APPROVED', 'PUBLISHED')
                  AND b.updated_at >= NOW() - (%s || ' hours')::INTERVAL
                ORDER BY b.updated_at DESC
                LIMIT 25
                """,
                (niche_id, lookback_hours),
            ).fetchall()
        except Exception as exc:
            logger.warning(
                "[scorer] blueprint query failed niche=%s: %s", niche_id, exc,
            )
            return []
    return [
        {
            "id": r.get("id") if hasattr(r, "get") else r[0],
            "niche_id": r.get("niche_id") if hasattr(r, "get") else r[1],
            "extra": r.get("extra") if hasattr(r, "get") else r[2],
            "story_id": r.get("story_id") if hasattr(r, "get") else r[3],
        }
        for r in rows or []
    ]


def _resolve_video_path(
    extra, story_id: str | None = None,
) -> Path | None:
    """Locate the rendered video for a blueprint. Two strategies:

      1. Check extra->'media'->'render_path' (and fallback keys).
         Ideal path — future renderers should populate.

      2. If (1) fails, glob the disk for the standard convention
         ``$GENLAB_TMP/runs/*/visuals/{story_id}/{story_id[:16]}_reel.mp4``.
         Discovered 2026-08-14 that blueprints don't currently store
         the render_path anywhere — 35/35 candidates hit no_video on
         first prod dry-run. Fallback works today; strategy 1 is
         forward-looking.

    Fail-open: returns None if neither finds a file. Caller skips."""
    # Strategy 1
    _extra = extra
    if isinstance(_extra, str):
        try:
            _extra = json.loads(_extra)
        except json.JSONDecodeError:
            _extra = None
    if isinstance(_extra, dict):
        media = _extra.get("media") or {}
        if isinstance(media, dict):
            for key in ("render_path", "final_render_path",
                        "rendered_path", "output_path"):
                c = media.get(key)
                if isinstance(c, str) and c.strip():
                    p = Path(c)
                    if p.exists():
                        return p

    # Strategy 2: glob disk by story_id convention
    # base_visual_render.py:198 uses {story_id}/{story_id[:16]}_reel.mp4
    if not story_id:
        return None
    tmp_root_str = os.environ.get("GENLAB_TMP", "").strip()
    tmp_root = Path(tmp_root_str) if tmp_root_str else Path.cwd() / ".tmp"
    runs_dir = tmp_root / "runs"
    if not runs_dir.exists():
        return None
    prefix = story_id[:16]
    filename = f"{prefix}_reel.mp4"
    matches = sorted(
        runs_dir.glob(f"*/visuals/{story_id}/{filename}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _already_scored(conn, blueprint_id: str, video_hash: str) -> bool:
    """Return True if we've already scored this exact
    (blueprint, hash) pair. Fail-open: on query error, return
    False so worst case we re-score (INSERT will hit the UNIQUE
    constraint + ON CONFLICT DO NOTHING)."""
    try:
        row = conn.execute(
            """
            SELECT 1 FROM content_quality_scores
            WHERE blueprint_id = %s AND video_hash = %s
            LIMIT 1
            """,
            (blueprint_id, video_hash),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _persist_score(conn, blueprint_id: str, niche_id: str, score) -> bool:
    """Insert one JointQualityScore row. Idempotent via ON CONFLICT."""
    try:
        conn.execute(
            """
            INSERT INTO content_quality_scores (
                blueprint_id, niche_id, video_path, video_hash,
                color_palette_dominance, motion_energy, cut_frequency,
                brand_consistency,
                audio_energy_variance, dialogue_density,
                music_to_voice_ratio,
                visual_score, audio_score, joint_score,
                failed_extractors
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s
            )
            ON CONFLICT (blueprint_id, video_hash) DO NOTHING
            """,
            (
                blueprint_id, niche_id, score.video_path, score.video_hash,
                score.color_palette_dominance, score.motion_energy,
                score.cut_frequency, score.brand_consistency,
                score.audio_energy_variance, score.dialogue_density,
                score.music_to_voice_ratio,
                score.visual_score, score.audio_score, score.joint_score,
                list(score.failed_extractors),
            ),
        )
        return True
    except Exception as exc:
        logger.warning("[scorer] persist failed bp=%s: %s", blueprint_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _run_niche(conn, niche_id: str, lookback_hours: int,
               blueprint_id_filter: str | None, dry_run: bool) -> dict:
    from genlab_core.quality.joint_score import compute_joint_score

    counts = {
        "candidates": 0, "no_video": 0, "already_scored": 0,
        "scored": 0, "persist_failed": 0,
    }
    blueprints = _find_unscored_blueprints(
        conn, niche_id, lookback_hours, blueprint_id_filter,
    )
    counts["candidates"] = len(blueprints)
    if not blueprints:
        return counts

    brand = _BRAND_COLORS.get(niche_id, "#FFFFFF")
    print(f"\n{niche_id}: {len(blueprints)} candidate blueprints (brand={brand})")

    for bp in blueprints:
        video = _resolve_video_path(bp["extra"], story_id=bp.get("story_id"))
        if video is None:
            counts["no_video"] += 1
            continue

        # Cheap pre-check: skip if already scored for this hash
        from genlab_core.quality.joint_score import _hash_video
        vh = _hash_video(video)
        if _already_scored(conn, bp["id"], vh):
            counts["already_scored"] += 1
            continue

        if dry_run:
            print(f"  [DRY] {bp['id'][:8]} → {video.name}")
            counts["scored"] += 1
            continue

        try:
            score = compute_joint_score(video, brand)
        except Exception as exc:
            logger.warning("[scorer] compute crashed bp=%s: %s", bp["id"], exc)
            counts["persist_failed"] += 1
            continue

        print(
            f"  {bp['id'][:8]} visual={score.visual_score} "
            f"audio={score.audio_score} joint={score.joint_score} "
            f"failed={list(score.failed_extractors)}"
        )
        if _persist_score(conn, bp["id"], niche_id, score):
            counts["scored"] += 1
        else:
            counts["persist_failed"] += 1

    if not dry_run and counts["scored"] > 0:
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
        "candidates": 0, "no_video": 0, "already_scored": 0,
        "scored": 0, "persist_failed": 0,
    }
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(
                conn, niche_id, args.lookback_hours,
                args.blueprint_id, args.dry_run,
            )
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[scorer] totals: candidates=%d no_video=%d already=%d scored=%d failed=%d",
        totals["candidates"], totals["no_video"],
        totals["already_scored"], totals["scored"], totals["persist_failed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
