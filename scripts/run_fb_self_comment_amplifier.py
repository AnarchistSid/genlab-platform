#!/usr/bin/env python3
"""Phase 3.E — Facebook self-comment amplifier runner.

Fires every 4h via ``genlab-fb-self-comment.timer``. For each niche
where ``cross_post.facebook_self_comment.enabled`` is true:

  1. Find FB posts from the last 48h with views >= min_reach that
     haven't been self-commented yet (idempotency check via
     ``extra->>'fb_self_comment_posted_at'``).
  2. For each: find the sibling YT post (same blueprint_id + niche).
  3. Post the self-comment via the amplify module.
  4. Persist the timestamp to prevent duplicate comments.

## Why post-hoc (not inline in publish)

Reach data isn't available at publish time — the metric collector
needs 6-24h to populate ``publishing_analytics.views``. This runner
polls periodically once reach is known.

## Idempotency

Two-layer:
  * DB check — ``extra->>'fb_self_comment_posted_at'`` set means
    this FB post already got a self-comment. Skip.
  * post_id UNIQUE on publishing_analytics — race condition would
    require two runners firing simultaneously. Systemd timer +
    oneshot Type prevents this.

## Usage

    uv run python scripts/run_fb_self_comment_amplifier.py
    uv run python scripts/run_fb_self_comment_amplifier.py --dry-run
    uv run python scripts/run_fb_self_comment_amplifier.py --niche gaming

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

logger = logging.getLogger("run_fb_self_comment_amplifier")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None, help="Limit to one niche")
    return ap.parse_args(argv)


def _find_eligible_fb_posts(conn, niche_id: str, min_reach: int):
    """Return FB posts eligible for self-comment: high-reach, in
    the 1h-to-48h window (>1h so reach data has settled), no prior
    self-comment. Fail-open to empty list."""
    try:
        rows = conn.execute(
            """
            SELECT pa.post_id AS fb_post_id,
                   pa.views AS fb_reach,
                   pa.blueprint_id
            FROM publishing_analytics pa
            WHERE pa.niche_id = %s
              AND pa.platform = 'facebook'
              AND pa.status = 'SUCCESS'
              AND pa.views IS NOT NULL
              AND pa.views >= %s
              AND pa.published_at BETWEEN NOW() - INTERVAL '48 hours'
                                       AND NOW() - INTERVAL '1 hour'
              AND (pa.extra->>'fb_self_comment_posted_at') IS NULL
            ORDER BY pa.views DESC
            LIMIT 25
            """,
            (niche_id, min_reach),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "[fb_amplifier] eligible-post query failed niche=%s: %s",
            niche_id, exc,
        )
        return []
    return [
        {
            "fb_post_id": r.get("fb_post_id") if hasattr(r, "get") else r[0],
            "fb_reach": int(r.get("fb_reach") if hasattr(r, "get") else r[1]),
            "blueprint_id": r.get("blueprint_id") if hasattr(r, "get") else r[2],
        }
        for r in rows or []
    ]


def _find_yt_sibling(conn, blueprint_id, niche_id: str) -> str | None:
    """Sibling YT publish for the same blueprint. Returns post_url
    or None."""
    if not blueprint_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT extra->>'post_url' AS post_url
            FROM publishing_analytics
            WHERE blueprint_id = %s
              AND niche_id = %s
              AND platform = 'youtube'
              AND status = 'SUCCESS'
            LIMIT 1
            """,
            (blueprint_id, niche_id),
        ).fetchone()
        if row is None:
            return None
        url = row.get("post_url") if hasattr(row, "get") else row[0]
        return url or None
    except Exception as exc:
        logger.warning(
            "[fb_amplifier] yt-sibling query failed bp=%s: %s",
            blueprint_id, exc,
        )
        return None


def _mark_commented(conn, fb_post_id: str, niche_id: str) -> bool:
    """Persist the timestamp so the row is skipped next run."""
    try:
        conn.execute(
            """
            UPDATE publishing_analytics
            SET extra = COALESCE(extra, '{}'::jsonb) ||
                        jsonb_build_object(
                          'fb_self_comment_posted_at',
                          %s::text
                        )
            WHERE post_id = %s AND niche_id = %s
              AND platform = 'facebook'
            """,
            (datetime.now(UTC).isoformat(), fb_post_id, niche_id),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[fb_amplifier] mark_commented failed fb_post=%s: %s",
            fb_post_id, exc,
        )
        return False


def _run_niche(conn, niche_id: str, dry_run: bool) -> dict[str, int]:
    from genlab_core.publishing.cross_post_amplify import (
        _fb_min_reach_threshold,
        _route_enabled,
        post_facebook_self_comment,
    )

    counts = {"eligible": 0, "no_sibling": 0, "commented": 0, "failed": 0}
    if not _route_enabled(niche_id, "facebook_self_comment"):
        return counts
    min_reach = _fb_min_reach_threshold(niche_id)
    eligible = _find_eligible_fb_posts(conn, niche_id, min_reach)
    counts["eligible"] = len(eligible)
    if not eligible:
        return counts

    print(f"\n{niche_id}: {len(eligible)} eligible FB posts (min_reach={min_reach})")
    for post in eligible:
        yt_url = _find_yt_sibling(conn, post["blueprint_id"], niche_id)
        if not yt_url:
            counts["no_sibling"] += 1
            print(f"  skip fb={post['fb_post_id'][:12]} — no YT sibling for bp={post['blueprint_id']}")
            continue
        if dry_run:
            print(f"  [DRY] fb={post['fb_post_id'][:12]} reach={post['fb_reach']} → YT: {yt_url[:50]}")
            counts["commented"] += 1
            continue
        ok = post_facebook_self_comment(
            source_platform="facebook",
            fb_post_id=post["fb_post_id"],
            fb_reach=post["fb_reach"],
            yt_post_url=yt_url,
            niche_id=niche_id,
        )
        if ok:
            counts["commented"] += 1
            _mark_commented(conn, post["fb_post_id"], niche_id)
        else:
            counts["failed"] += 1
    if not dry_run and counts["commented"] > 0:
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

    totals = {"eligible": 0, "no_sibling": 0, "commented": 0, "failed": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(conn, niche_id, args.dry_run)
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[fb_amplifier] totals: eligible=%d no_sibling=%d commented=%d failed=%d",
        totals["eligible"], totals["no_sibling"],
        totals["commented"], totals["failed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
