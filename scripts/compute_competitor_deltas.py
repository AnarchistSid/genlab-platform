#!/usr/bin/env python3
"""Phase 3.A — competitor content deltas.

Compares top-tier competitor uploads (from A.2's per-niche artifacts)
against our own recent YouTube reach. Persists per-competitor-video
rows to ``competitor_content_deltas`` so the strategist + Mission
Control can surface: *"this competitor hook outperformed our
typical output by 8x — should we adapt?"*

## Data flow

    watch_top_creator_uploads.py      → .tmp/top_creators/{YYYYMMDD}-{niche}.json
      (existing 4x/day timer)           {creators: [{channel_id, uploads: [...]}]}

    compute_competitor_deltas.py      → competitor_content_deltas table
      (this script, daily)              one row per (competitor_video, our_ref)
                                        with delta_ratio = them / us_median

## Cost

Uses YouTube ``videos.list`` (part=statistics) — 1 unit per batch of
up to 50 video IDs. Fetching statistics for 5 niches × 3-5 creators ×
~5 uploads = ~5 batched calls = ~5 quota per run. Cheap.

## Usage

    uv run python scripts/compute_competitor_deltas.py
    uv run python scripts/compute_competitor_deltas.py --dry-run
    uv run python scripts/compute_competitor_deltas.py --niche gaming

## Exit codes

  * 0 — completed (rows written OR dry-run OR cold-start)
  * 1 — DATABASE_URL or YOUTUBE_API_KEY unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("compute_competitor_deltas")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None, help="Limit to one niche")
    ap.add_argument("--lookback-days", type=int, default=7,
                    help="Look back N days for competitor artifacts (default 7)")
    return ap.parse_args(argv)


def _artifact_dir() -> Path:
    """Match A.2 watcher: prefer GENLAB_TMP_ROOT, fallback to
    cwd/.tmp/top_creators/."""
    tmp = os.environ.get("GENLAB_TMP_ROOT")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "top_creators"


def _load_latest_artifact(niche_id: str, lookback_days: int):
    """Return the most recent per-niche artifact within lookback window.

    Handles the class-of-bug where a stale artifact (>7 days) could
    inflate deltas — we skip it and treat that niche as cold-start."""
    dir_ = _artifact_dir()
    if not dir_.exists():
        return None
    cutoff = date.today() - timedelta(days=lookback_days)
    candidates = []
    for path in dir_.glob(f"*-{niche_id}.json"):
        try:
            stamp = path.name.split("-", 1)[0]
            file_date = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        candidates.append((file_date, path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    latest_path = candidates[0][1]
    try:
        return json.loads(latest_path.read_text())
    except json.JSONDecodeError as exc:
        logger.warning("[deltas] artifact %s malformed: %s", latest_path, exc)
        return None


def _fetch_video_stats(video_ids: list[str], api_key: str) -> dict[str, dict]:
    """Batch ``videos.list`` for up-to-50 video IDs; returns
    ``{video_id: {view_count, like_count, comment_count}}`` on success,
    empty dict on any failure (fail-soft)."""
    if not video_ids:
        return {}
    try:
        import requests

        out: dict[str, dict] = {}
        # YouTube caps videos.list at 50 IDs per call
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "statistics",
                    "id": ",".join(batch),
                    "key": api_key,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[deltas] videos.list HTTP %d — body: %s",
                    resp.status_code, resp.text[:200],
                )
                continue
            for item in resp.json().get("items") or []:
                vid = item.get("id")
                stats = item.get("statistics") or {}
                if not vid:
                    continue
                out[vid] = {
                    "view_count": int(stats.get("viewCount") or 0),
                    "like_count": int(stats.get("likeCount") or 0),
                    "comment_count": int(stats.get("commentCount") or 0),
                }
        return out
    except Exception as exc:
        logger.warning("[deltas] videos.list crashed — no stats: %s",
                       exc, exc_info=True)
        return {}


def _our_median_youtube_views(conn, niche_id: str, lookback_days: int = 7) -> int:
    """Median YouTube views for our posts in this niche over the
    lookback window. Returns 0 on cold-start / any query error."""
    try:
        row = conn.execute(
            """
            SELECT COALESCE(
              percentile_cont(0.5) WITHIN GROUP (ORDER BY views), 0
            )::bigint AS median_views
            FROM publishing_analytics
            WHERE niche_id = %s
              AND platform = 'youtube'
              AND views IS NOT NULL
              AND published_at >= NOW() - INTERVAL '%s days'
            """ % ("%s", lookback_days),
            (niche_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row.get("median_views") if hasattr(row, "get") else row[0])
    except Exception as exc:
        logger.warning("[deltas] our_median query failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _compute_niche(conn, niche_id: str, lookback_days: int,
                   api_key: str, dry_run: bool) -> int:
    """Compute + persist deltas for one niche. Returns count written."""
    artifact = _load_latest_artifact(niche_id, lookback_days)
    if artifact is None:
        logger.info("[deltas] niche=%s: no artifact within %dd", niche_id, lookback_days)
        return 0

    # Collect all video IDs across creators for a single batched
    # videos.list call.
    creators = artifact.get("creators") or []
    all_video_ids: list[str] = []
    upload_index: dict[str, dict] = {}
    for creator in creators:
        cid = creator.get("channel_id") or ""
        label = creator.get("label") or ""
        for u in creator.get("uploads") or []:
            vid = u.get("video_id") or ""
            if not vid:
                continue
            all_video_ids.append(vid)
            upload_index[vid] = {
                "channel_id": cid,
                "channel_label": label,
                "title": u.get("title") or "",
                "published_at": u.get("published_at") or None,
            }

    stats = _fetch_video_stats(all_video_ids, api_key)
    our_median = _our_median_youtube_views(conn, niche_id, lookback_days)

    print(f"\n{niche_id}:")
    print(f"  competitor uploads: {len(all_video_ids)} | stats fetched: {len(stats)}")
    print(f"  our_median_yt_views (last {lookback_days}d): {our_median}")

    written = 0
    for vid, meta in upload_index.items():
        s = stats.get(vid, {})
        view_count = s.get("view_count", 0)
        like_count = s.get("like_count", 0)
        comment_count = s.get("comment_count", 0)
        delta_views = view_count - our_median if our_median > 0 else None
        delta_ratio = (
            view_count / our_median if our_median > 0 and view_count > 0 else None
        )

        print(f"  {vid[:11]} {meta['channel_label'][:20]:20} "
              f"views={view_count:>10,} ratio={delta_ratio or 0:>6.2f}x")

        if dry_run:
            continue

        try:
            conn.execute(
                """
                INSERT INTO competitor_content_deltas (
                    niche_id, competitor_channel_id, competitor_channel_label,
                    competitor_video_id, competitor_title,
                    competitor_published_at, competitor_view_count,
                    competitor_like_count, competitor_comment_count,
                    our_reference_view_count, delta_views, delta_ratio
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (competitor_video_id, our_reference_blueprint_id)
                DO UPDATE SET
                    competitor_view_count = EXCLUDED.competitor_view_count,
                    competitor_like_count = EXCLUDED.competitor_like_count,
                    competitor_comment_count = EXCLUDED.competitor_comment_count,
                    our_reference_view_count = EXCLUDED.our_reference_view_count,
                    delta_views = EXCLUDED.delta_views,
                    delta_ratio = EXCLUDED.delta_ratio,
                    computed_at = NOW()
                """,
                (
                    niche_id, meta["channel_id"], meta["channel_label"],
                    vid, meta["title"], meta["published_at"],
                    view_count, like_count, comment_count,
                    our_median, delta_views, delta_ratio,
                ),
            )
            written += 1
        except Exception as exc:
            logger.warning("[deltas] persist failed vid=%s: %s", vid, exc)
            try:
                conn.rollback()
            except Exception:
                pass

    if not dry_run and written > 0:
        conn.commit()
    return written


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
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        logger.error("YOUTUBE_API_KEY unset")
        return 1

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    total_written = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            total_written += _compute_niche(
                conn, niche_id, args.lookback_days, api_key, args.dry_run,
            )

    logger.info("competitor_deltas: wrote %d rows across %d niches",
                total_written, len(niches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
