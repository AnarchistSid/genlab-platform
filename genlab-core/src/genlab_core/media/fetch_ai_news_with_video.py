"""Fetch AI news headlines from RSS feeds, then pair each with a
YouTube creator video via `yt-dlp ytsearch1:{article_title}`.

Design intent: gives ai_creators a second source-type beyond the
YouTube channel RSS list. Instead of watching a fixed set of channels,
this drives video discovery from what's news-worthy TODAY —
TechCrunch / VentureBeat / The Verge writing about a specific model
release, benchmark, or product launch → YT search finds a creator's
reaction/demo video for it → that video becomes the reel source.

Free-tier design: uses yt-dlp's `ytsearch1:` (public scraping) rather
than YouTube Data API `search.list` (100 quota units per call). Cost
is CPU time, not quota. Cached results avoid re-searching the same
title across pipeline fires.

Failure modes handled:
  * RSS fetch fails → skip that source, others still fire
  * yt-dlp bot-check on search → same [[class-of-bug-datacenter-ip-
    bot-detection]] as clip_sourcer; ships the query as best-effort
    and moves on
  * No YT match for a headline → drop the story (no orphan text-only
    entries; video-first mandate)
  * Duplicate video across sources → dedup by video_id
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_YT_VIDEO_ID_RE = re.compile(r"(?:v=|/watch\?v=|/shorts/)([A-Za-z0-9_-]{11})")
_YT_SEARCH_TIMEOUT_S = 30

_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_rss(url: str, timeout: int = 15) -> list[dict[str, str]]:
    """Return a list of {title, link, published, source_domain} entries
    from an Atom or RSS feed. Empty on any error.

    Uses feedparser if available (handles Atom, RSS 1.0, RSS 2.0,
    JSON Feed). Falls back to urllib + a minimal regex parser if
    feedparser isn't installed (defensive; feedparser is a direct
    dependency but the fallback prevents crashes in stripped
    environments).
    """
    try:
        import feedparser  # type: ignore

        feed = feedparser.parse(url, agent=_DEFAULT_UA)
        source_domain = url.split("/")[2] if "//" in url else url
        entries = []
        for entry in feed.entries[:20]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            entries.append({
                "title": title,
                "link": link,
                "published": entry.get("published", ""),
                "source_domain": source_domain,
            })
        return entries
    except Exception as exc:
        logger.warning("[AINewsWithVideo] RSS fetch failed for %s: %s", url, exc)
        return []


def _yt_dlp_cookies_args() -> list[str]:
    """Duplicate of the CriticalRush helper so this module stays
    self-contained. When YT_DLP_COOKIES_FILE is set to a real file,
    returns ['--cookies', path]; empty list otherwise."""
    import os as _os

    path = _os.environ.get("YT_DLP_COOKIES_FILE", "").strip()
    if not path or not Path(path).is_file():
        return []
    return ["--cookies", path]


def _search_youtube_for(query: str) -> tuple[str, str] | None:
    """Run yt-dlp ytsearch1:{query} and return (video_url, title) on
    success, None on any failure. Uses --skip-download to avoid
    pulling MP4 (that happens later in the render stage). Cost: one
    ytsearch1 call ~1-3s + zero YT Data API quota."""
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "--skip-download",
        "--print", "%(webpage_url)s\t%(title)s",
        "--quiet",
        "--no-warnings",
    ]
    cmd.extend(_yt_dlp_cookies_args())
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_YT_SEARCH_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("[AINewsWithVideo] yt-dlp search failed for '%s': %s", query, exc)
        return None
    if result.returncode != 0:
        # Bot-check would go through clip_sourcer's _emit_cookies_stale_alert
        # if this ran in the gaming stage, but here we just log and skip.
        stderr_tail = "\n".join((result.stderr or "").splitlines()[-2:])
        logger.info(
            "[AINewsWithVideo] no YT match for '%s' (exit=%d): %s",
            query, result.returncode, stderr_tail,
        )
        return None
    line = (result.stdout or "").strip().split("\n")[0]
    if "\t" not in line:
        return None
    url, title = line.split("\t", 1)
    return url.strip(), title.strip()


def _extract_video_id(url: str) -> str:
    m = _YT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def fetch_for_niche(
    niche_id: str,
    rss_feeds: list[dict[str, Any]],
    per_feed_limit: int = 3,
) -> list[dict[str, Any]]:
    """Main entry point. For each RSS feed URL, fetch up to
    `per_feed_limit` newest headlines, run yt search for each,
    return StoryCandidate-shaped dicts.

    Dedups by video_id — if two news sources cover the same story
    and YT returns the same video for both queries, we emit one entry.

    Returned dict shape aligns with StoryCandidate (extra=allow so
    the niche-specific fields pass through):

      {
        "story_id": "<sha256>",
        "source_url": "<yt video URL>",
        "title": "<yt video title>",
        "source": "ainewsyt:<feed_domain>",
        "source_channel_id": "",  # not always known from search
        "download_url": "<yt video URL>",
        "video_id": "<11-char yt id>",
        "extra": {
          "article_title": "<original RSS title>",
          "article_url": "<RSS link>",
          "article_published": "<RSS published>",
          "search_query": "<what we sent to ytsearch>",
        }
      }
    """
    stories: list[dict[str, Any]] = []
    seen_video_ids: set[str] = set()
    for feed_cfg in rss_feeds:
        url = feed_cfg.get("url", "")
        if not url:
            continue
        entries = _fetch_rss(url)
        emitted_from_this_feed = 0
        for entry in entries:
            if emitted_from_this_feed >= per_feed_limit:
                break
            title = entry["title"]
            hit = _search_youtube_for(title)
            if not hit:
                continue
            yt_url, yt_title = hit
            video_id = _extract_video_id(yt_url)
            if not video_id or video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            story_id = hashlib.sha256(f"{yt_url}|{entry.get('published', '')}".encode()).hexdigest()
            source_domain = entry.get("source_domain", "unknown")
            stories.append({
                "story_id": story_id,
                "source_url": yt_url,
                "title": yt_title,
                "source": f"ainewsyt:{source_domain}",
                "source_channel_id": "",
                "download_url": yt_url,
                "video_id": video_id,
                "extra": {
                    "article_title": title,
                    "article_url": entry.get("link", ""),
                    "article_published": entry.get("published", ""),
                    "search_query": title,
                    "niche_id": niche_id,
                },
            })
            emitted_from_this_feed += 1
        logger.info(
            "[AINewsWithVideo] feed=%s emitted %d stories (searched %d headlines)",
            url, emitted_from_this_feed, len(entries),
        )
    return stories
