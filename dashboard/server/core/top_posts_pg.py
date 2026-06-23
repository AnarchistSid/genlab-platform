"""Postgres reader for top-performing posts per niche.

PR CC (2026-06-23): powers the media-kit's top-posts section.
Brands evaluating sponsorship deals do two passes — read numbers,
then click into actual content to judge production quality + brand
fit. Without top-post links, the second pass forces them to
manually navigate each platform and scroll the channel feed. With
this query, the kit becomes a self-contained prequalification gate.

## What "top" means

Composite engagement = views + likes*5 + comments*10. The weights
mirror the project's broader composite-reward convention (audit
2026-06-20) — likes are 5× more meaningful than views as
engagement signal; comments are 2× likes.

## Lookback window

30 days. Long enough to absorb a typical posting cadence (~1
post/day → 30 posts to pick from), short enough that the kit
reads as "recent activity" rather than "best-ever from 2 years
ago." Brands care about momentum, not legacy highlights.

## URL derivation per platform

Mirrors the dispatch pattern from cross_post_teaser._SOURCE_ROUTES
(PR #486). PR FF (2026-06-23) added Threads + TikTok via the
``_NICHE_HANDLES_*`` mappings below — both platforms have stable
per-handle URLs, handles change ~never, no YAML loader needed.

  youtube      → https://youtube.com/shorts/{post_id}
  facebook     → https://www.facebook.com/{post_id}
  x_twitter    → https://twitter.com/i/web/status/{post_id}
  threads      → https://www.threads.net/@{handle}/post/{post_id}
                 (handle from _NICHE_HANDLES_THREADS; PR FF)
  tiktok       → https://www.tiktok.com/@{handle}/video/{post_id}
                 (handle from _NICHE_HANDLES_TIKTOK; PR FF)
  instagram    → https://www.instagram.com/p/{shortcode}
                 (shortcode != post_id; needs Graph API lookup; v2)

Only platforms with a derivable URL are included in the top_posts
list. IG posts still count for engagement ranking but get filtered
at URL-derivation so the kit doesn't render dead links.

The hardcoded handles mirror MediaKit.tsx's frontend
``NICHE_HANDLES`` constant — one source of truth per layer rather
than a runtime YAML loader (handles change ~never; brand identity
not configuration). Frontend + backend diverge only on the rare
rebrand event, which the editor catches because both files appear
in the same PR diff.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from genlab_core.storage.tenant_context import pg_connect

logger = logging.getLogger(__name__)

# PR FF (2026-06-23): per-niche handles for Threads + TikTok URL
# derivation. Mirrors MediaKit.tsx's NICHE_HANDLES (frontend) —
# one source of truth per layer. Handles change ~never; rebrand
# events catch both layers in the same PR diff.
_NICHE_HANDLES_THREADS: dict[str, str] = {
    "ai_creators": "blackbox.brief",
    "gaming": "criticalrush",
    "sports": "clutchwire",
    "movies": "splicereel",
    "anime": "framedrift",
}
_NICHE_HANDLES_TIKTOK: dict[str, str] = {
    "ai_creators": "blackbox.brief",
    "gaming": "criticalrush",
    "sports": "clutchwire",
    "movies": "splicereel",
    "anime": "framedrift",
}


def _derive_thumbnail_url(platform: str, post_id: str) -> str | None:
    """Return a stable public thumbnail URL for (platform, post_id), or None.

    PR GG (2026-06-23): only YouTube has a thumbnail URL derivable
    from ``post_id`` alone — ``i.ytimg.com/vi/{video_id}/maxresdefault.jpg``
    is YouTube's documented thumbnail-by-id pattern. FB/IG/Threads/
    TikTok/X all need Graph API or platform-specific lookups, so
    they return None and the frontend falls back to its text-row
    rendering for those platforms.

    Asymmetric coverage is intentional — YT-only thumbnails deliver
    80% of the visual-prequalification value at 5% of the effort
    vs building per-platform thumbnail fetchers.
    """
    if not post_id:
        return None
    # Strip the PR #486 W3.2 ``{platform}:{id}`` prefix if present.
    if ":" in post_id:
        post_id = post_id.split(":", 1)[1]
    if not post_id:
        return None
    if platform == "youtube":
        return f"https://i.ytimg.com/vi/{post_id}/maxresdefault.jpg"
    return None


def _derive_post_url(platform: str, post_id: str, niche_id: str | None = None) -> str | None:
    """Return the public URL for a (platform, post_id) pair, or None.

    None signals "no stable public URL derivable from post_id alone"
    — the kit will exclude this post from the top-posts list rather
    than render a dead/awkward link. See module docstring for the
    per-platform table.
    """
    if not post_id:
        return None
    # PR #486 W3.2 normalization: post_id may carry a "{platform}:{id}"
    # prefix from the analytics writer. Strip it so the URL builder
    # sees the bare id.
    if ":" in post_id:
        post_id = post_id.split(":", 1)[1]
    if not post_id:
        return None
    if platform == "youtube":
        return f"https://youtube.com/shorts/{post_id}"
    if platform == "facebook":
        return f"https://www.facebook.com/{post_id}"
    if platform in ("x_twitter", "twitter"):
        return f"https://twitter.com/i/web/status/{post_id}"
    if platform == "threads":
        # PR FF: handle-based URL. Returns None when niche_id isn't
        # supplied (cold-start path) or isn't in the handle map.
        handle = _NICHE_HANDLES_THREADS.get(niche_id or "")
        if not handle:
            return None
        return f"https://www.threads.net/@{handle}/post/{post_id}"
    if platform == "tiktok":
        # PR FF: handle-based URL. Same fallback shape as threads.
        handle = _NICHE_HANDLES_TIKTOK.get(niche_id or "")
        if not handle:
            return None
        return f"https://www.tiktok.com/@{handle}/video/{post_id}"
    # instagram: needs shortcode (not post_id) — Graph API lookup.
    # Deferred to v2.
    return None


def fetch_top_posts(
    niche_id: str,
    *,
    limit: int = 3,
    lookback_days: int = 30,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` top-performing posts for the niche over
    the lookback window.

    Composite engagement = views + likes*5 + comments*10.

    Posts on platforms that don't have a derivable public URL
    (instagram / threads / tiktok at v1) are EXCLUDED from the
    returned list — they still count for the SQL ranking but their
    rows are filtered post-query.

    Returns:
        List of dicts with: platform, post_id, post_url,
        published_at (ISO), views, likes, comments, score.
        Empty list when DSN is missing, query fails, or no qualifying
        posts exist in the window.
    """
    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn:
        return []

    # Fetch more than ``limit`` from SQL since URL-derivation may
    # filter some rows out. 3x is a safe overprovision — even if 2/3
    # of posts are IG-only, we still surface 3 YT/FB/X posts.
    sql_limit = max(limit * 3, 10)

    try:
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      platform,
                      post_id,
                      published_at,
                      COALESCE(views, 0) AS views,
                      COALESCE(likes, 0) AS likes,
                      COALESCE(comments, 0) AS comments,
                      (COALESCE(views, 0)
                       + COALESCE(likes, 0) * 5
                       + COALESCE(comments, 0) * 10) AS score
                    FROM publishing_analytics
                    WHERE niche_id = %s
                      AND status = 'PUBLISHED'
                      AND published_at IS NOT NULL
                      AND published_at >= NOW() - (%s::int * INTERVAL '1 day')
                      AND post_id IS NOT NULL
                      AND post_id <> ''
                    ORDER BY score DESC, published_at DESC
                    LIMIT %s
                    """,
                    (niche_id, lookback_days, sql_limit),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.debug("[top_posts_pg] query failed for niche=%s: %s", niche_id, exc)
        return []

    # Derive URLs + filter to platforms with derivable URLs.
    # PR FF: pass niche_id so Threads/TikTok URLs get their handle.
    out: list[dict[str, Any]] = []
    for row in rows:
        platform, post_id, published_at, views, likes, comments, score = row
        url = _derive_post_url(platform, post_id or "", niche_id=niche_id)
        if url is None:
            continue
        out.append(
            {
                "platform": platform,
                "post_id": post_id,
                "post_url": url,
                # PR GG: thumbnail_url is None for non-YT platforms
                # (Graph API required). Frontend renders thumbnail when
                # present, text-row fallback when None.
                "thumbnail_url": _derive_thumbnail_url(platform, post_id or ""),
                "published_at": published_at.isoformat() if published_at else None,
                "views": int(views or 0),
                "likes": int(likes or 0),
                "comments": int(comments or 0),
                "score": int(score or 0),
            }
        )
        if len(out) >= limit:
            break

    return out
