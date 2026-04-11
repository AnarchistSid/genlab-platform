#!/usr/bin/env python3
"""Fetch AI content from configured RSS, API, Playwright, and connector sources.

Reads source definitions from config/sources.yaml, fetches each enabled feed
using feedparser (RSS), requests (API), or HTML scraping (fallback),
applies caching, and writes raw results to .tmp/runs/<run_id>/fetch_log.json.

Fetch strategy per source type:
  - rss: feedparser → HTML scraper fallback if feed fails
  - api: requests JSON
  - html: direct HTML scraping (for sources without RSS)
  - connector: platform-specific connector registry dispatch
"""

import argparse
import hashlib
import json
import logging
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import requests
except ImportError:
    requests = None

from genlab_core.cache.disk_cache import Cache
from genlab_core.ratelimit.domain_limiter import get_limiter

from execution.scrapers.gallery_scrapers import fetch_gallery
from execution.sources.registry import classify_fetch_status, fetch_via_connector
from execution.utils.config_loader import load_config
from execution.utils.error_events import emit_error_event
from execution.utils.html_scraper import fetch_html_source
from execution.utils.playwright_fetcher import (
    fetch_js_rendered_source,
    is_playwright_available,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.yaml"
DEFAULT_CACHE_TTL_HOURS = 6  # Matches CLAUDE.md spec: "TTL: 6 hours for news fetches"
MAX_RETRIES = 2
RETRY_BACKOFF = 2  # seconds
REQUEST_ERRORS = (requests.RequestException,) if requests is not None else tuple()
URL_PARSE_ERRORS = (ValueError, AttributeError)
DATE_PARSE_ERRORS = (TypeError, ValueError, OverflowError, IndexError)
FEED_FETCH_ERRORS = (RuntimeError, OSError, ValueError, TypeError, AttributeError)
API_FETCH_ERRORS = REQUEST_ERRORS + (ValueError, TypeError, AttributeError)
FETCH_WORKER_ERRORS = REQUEST_ERRORS + (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    ImportError,
    LookupError,
)

# Keywords for filtering general feeds (e.g., The Verge, Wired) to AI content
AI_KEYWORDS = re.compile(
    r"\b(ai|artificial intelligence|machine learning|deep learning|llm|gpt|"
    r"claude|gemini|openai|anthropic|neural|chatbot|generative|diffusion|"
    r"transformer|language model|copilot|midjourney|stable diffusion|"
    r"reinforcement learning|computer vision|nlp|agi|superintelligence|"
    r"foundation model|fine.?tun|embedding|rag|agent|reasoning)\b",
    re.IGNORECASE,
)

# Creator-showcase heuristics for "content_type: showcase" sources.
# Goal: keep creator posts with actual media or clear creation intent;
# drop subreddit discussion/help/question threads.
_CREATOR_SHOWCASE_KEYWORDS = re.compile(
    r"\b("
    r"made|created|generated|rendered|animated|using|with|prompt|showcase|"
    r"ai art|ai video|midjourney|kling|sora|seedance|veo|runway|flux|hailuo|dreamcore"
    r")\b",
    re.IGNORECASE,
)

_CREATOR_DISCUSSION_PATTERNS = re.compile(
    r"^\s*\[(?:discussion|question|help|request|feedback|meta)\]|"
    r"^(?:is|are|can|does|do|should|would|why|how|what|when|where|who)\b"
    r"(?!.*\b(?:made|created|generated|rendered|animated|prompt)\b)|"
    r"\b(?:is there a way|anyone else|need help|help me|question)\b",
    re.IGNORECASE,
)

_CREATOR_MEDIA_PATTERNS = re.compile(
    r"<(?:img|video)\b|preview\.redd\.it|i\.redd\.it|v\.redd\.it|"
    r"youtube\.com|youtu\.be|vimeo\.com|tiktok\.com|threads\.net|"
    r"instagram\.com/(?:reel|p)/|x\.com/.+/status/|twitter\.com/.+/status/|"
    r"civitai\.com|image\.civitai\.com|hf\.space|huggingface\.co/spaces|"
    r"\.(?:mp4|webm|mov|gif|jpg|jpeg|png|webp)\b",
    re.IGNORECASE,
)

_CREATOR_VIDEO_PATTERNS = re.compile(
    r"v\.redd\.it|reddit_video|<video\b|youtube\.com|youtu\.be|"
    r"vimeo\.com|tiktok\.com/.+/video/|instagram\.com/reel|/reel/|"
    r"x\.com/.+/status/|twitter\.com/.+/status/|"
    r"\.(?:mp4|webm|mov)\b",
    re.IGNORECASE,
)


def _is_channel_or_profile_url(link: str) -> bool:
    """Return True when a URL is a profile/channel/listing page, not a concrete post."""
    if not link:
        return False

    try:
        parsed = urlparse(link)
    except URL_PARSE_ERRORS:
        return False

    host = (parsed.netloc or "").lower().removeprefix("www.")
    if not host:
        return False

    path = (parsed.path or "").rstrip("/")
    segments = [seg for seg in path.split("/") if seg]

    # YouTube profile/channel/listing pages.
    if host in {"youtube.com", "m.youtube.com"}:
        if not segments:
            return True
        first = segments[0].lower()
        if first.startswith("@"):
            return len(segments) <= 2 and (
                len(segments) == 1
                or segments[1].lower() in {
                    "featured",
                    "videos",
                    "shorts",
                    "streams",
                    "playlists",
                    "community",
                }
            )
        if first in {"channel", "c", "user"}:
            return len(segments) <= 2
        if first in {"feed", "results", "playlist"}:
            return True
        if first == "watch":
            qs = parse_qs(parsed.query or "")
            return not bool((qs.get("v") or [""])[0])
        if first == "shorts":
            return len(segments) <= 1

    # TikTok profile pages.
    if host.endswith("tiktok.com"):
        if not segments:
            return True
        if len(segments) == 1 and segments[0].startswith("@"):
            return True

    # X/Twitter profile pages.
    if host in {"x.com", "twitter.com"}:
        if not segments:
            return True
        if "status" in (seg.lower() for seg in segments):
            return False
        if len(segments) <= 2:
            return True

    # Instagram profile pages.
    if host.endswith("instagram.com"):
        if not segments:
            return True
        first = segments[0].lower()
        if first in {"reel", "reels", "p", "tv"}:
            return len(segments) <= 1
        if len(segments) == 1:
            return True
        if len(segments) == 2 and segments[1].lower() in {"reels", "tagged", "guides"}:
            return True

    # Threads profile pages.
    if host.endswith("threads.net"):
        if not segments:
            return True
        first = segments[0].lower()
        if first.startswith("@"):
            if len(segments) == 1:
                return True
            if len(segments) == 2 and segments[1].lower() in {"replies", "media"}:
                return True

    # Reddit subreddit/user listing pages (not specific post permalinks).
    if host.endswith("reddit.com"):
        if not segments:
            return True
        first = segments[0].lower()
        if first in {"u", "user"}:
            return True
        if first == "r":
            if len(segments) >= 3 and segments[2].lower() == "comments":
                return False
            return len(segments) <= 3

    # Vimeo non-video channel/profile pages.
    if host.endswith("vimeo.com"):
        if not segments:
            return True
        if segments[-1].isdigit():
            return False
        if segments[0].lower() in {"channels", "groups", "ondemand", "showcase"}:
            return True
        if len(segments) == 1 and not segments[0].isdigit():
            return True

    return False


def load_sources(config_path: Path = CONFIG_PATH) -> List[Dict[str, Any]]:
    """Load and return enabled sources from the YAML config."""
    cfg = load_config(
        str(config_path) if config_path != CONFIG_PATH else "sources.yaml",
        required=False,
    )
    if not cfg:
        logger.warning("Sources config not found at %s", config_path)
        return []
    sources = cfg.get("sources", [])
    enabled = [s for s in sources if s.get("enabled", True)]

    creator_only_mode = bool(cfg.get("creator_only_mode", False))
    creator_video_only_mode = bool(cfg.get("creator_video_only_mode", False))
    creator_media_only_mode = bool(cfg.get("creator_media_only_mode", False))
    creator_direct_only_mode = bool(cfg.get("creator_direct_only_mode", False))
    creator_lookback_days = int(cfg.get("creator_lookback_days", 0) or 0)
    if creator_only_mode:
        showcase_sources = [
            s for s in enabled
            if (s.get("content_type", "") or "").strip().lower() == "showcase"
        ]
        creator_sources = showcase_sources
        if creator_direct_only_mode:
            creator_sources = [
                s for s in creator_sources
                if bool(s.get("creator_source", False))
            ]
        if creator_lookback_days > 0:
            creator_sources = [
                dict(s, creator_lookback_days=creator_lookback_days)
                for s in creator_sources
            ]
        if creator_video_only_mode:
            creator_sources = [dict(s, video_only_mode=True) for s in creator_sources]
        if creator_media_only_mode:
            creator_sources = [dict(s, media_only_mode=True) for s in creator_sources]
        logger.info(
            (
                "Creator-only mode enabled: %d/%d enabled sources retained "
                "(showcase=%d, creator_direct_only=%s, video_only=%s, media_only=%s, "
                "lookback_days=%s)"
            ),
            len(creator_sources),
            len(enabled),
            len(showcase_sources),
            creator_direct_only_mode,
            creator_video_only_mode,
            creator_media_only_mode,
            creator_lookback_days if creator_lookback_days > 0 else "off",
        )
        return creator_sources

    logger.info("Loaded %d enabled sources from %s", len(enabled), config_path)
    return enabled


def _cache_key(url: str) -> str:
    from datetime import date
    key_input = url + date.today().isoformat()
    return hashlib.sha256(key_input.encode()).hexdigest()[:16]


def _derive_source_id(src: Dict[str, Any]) -> str:
    """Return an explicit source_id or a stable fallback generated from URL."""
    explicit = (src.get("source_id", "") or "").strip()
    if explicit:
        return explicit
    return f"SRC_{_cache_key(src.get('url', ''))}"


def _is_ai_related(entry: Dict[str, Any]) -> bool:
    """Check if an entry is AI-related based on title + summary."""
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return bool(AI_KEYWORDS.search(text))


def _has_creator_media(entry: Dict[str, Any]) -> bool:
    """Detect whether an entry has inline/linked media evidence."""
    summary = entry.get("summary", "") or ""
    link = entry.get("link", "") or ""
    return bool(
        _CREATOR_MEDIA_PATTERNS.search(summary)
        or _CREATOR_MEDIA_PATTERNS.search(link)
    )


def _has_creator_video(entry: Dict[str, Any]) -> bool:
    """Detect whether an entry has concrete video evidence."""
    summary = entry.get("summary", "") or ""
    link = entry.get("link", "") or ""
    return bool(
        _CREATOR_VIDEO_PATTERNS.search(summary)
        or _CREATOR_VIDEO_PATTERNS.search(link)
    )


def _is_creator_showcase_entry(entry: Dict[str, Any], require_video: bool = False) -> bool:
    """Heuristic gate for creator showcase entries.

    Keeps posts that look like creator output showcases (media-backed or with
    explicit creation cues) and filters discussion/help style threads.
    """
    title = (entry.get("title", "") or "").strip()
    summary = entry.get("summary", "") or ""
    link = entry.get("link", "") or ""
    if not title:
        return False

    text = f"{title} {summary}"
    has_media = _has_creator_media(entry)
    has_video = _has_creator_video(entry)
    has_showcase_keywords = bool(_CREATOR_SHOWCASE_KEYWORDS.search(text))
    looks_discussion = bool(_CREATOR_DISCUSSION_PATTERNS.search(title))
    is_reddit_entry = "reddit.com/r/" in link or "old.reddit.com/r/" in link

    if require_video and not has_video:
        return False

    # Reddit comment threads are noisy; require concrete media evidence.
    if is_reddit_entry and not has_media:
        return False

    # Drop discussion/help threads unless they clearly look like showcase posts.
    if looks_discussion and not has_showcase_keywords:
        return False

    # Standalone questions are usually discussion, not creator showcases.
    if title.rstrip().endswith("?") and not has_showcase_keywords:
        return False

    # Require either media evidence or explicit showcase language.
    if not has_media and not has_showcase_keywords:
        return False

    return True


def _parse_entry_published_at(entry: Dict[str, Any]) -> Optional[datetime]:
    """Parse an entry's published timestamp into a timezone-aware UTC datetime."""
    raw = (entry.get("published", "") or "").strip()
    if not raw:
        return None

    # RSS common path
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except DATE_PARSE_ERRORS:
        pass

    # ISO-ish fallbacks
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    for item in candidates:
        try:
            dt = datetime.fromisoformat(item)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return None


def _is_entry_within_lookback(entry: Dict[str, Any], lookback_days: int) -> bool:
    """Return True if entry published date is within lookback window."""
    if lookback_days <= 0:
        return True

    published_at = _parse_entry_published_at(entry)
    if published_at is None:
        return False

    now = datetime.now(timezone.utc)
    if published_at > now + timedelta(days=1):
        return False

    return (now - published_at) <= timedelta(days=lookback_days)


def _apply_entry_filters(src: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Apply source-specific entry filters in place (AI + showcase filters)."""
    entries = result.get("entries") or []
    result["items_count_raw"] = int(result.get("items_count_raw", len(entries)) or len(entries))
    if not entries:
        result["entries"] = []
        result["entry_count"] = 0
        result["items_count_filtered"] = 0
        return

    url = src.get("url", result.get("url", ""))
    ai_filter = bool(src.get("ai_filter", False))
    content_type = (src.get("content_type", "") or "").strip().lower()
    video_only_mode = bool(src.get("video_only_mode", False))
    media_only_mode = bool(src.get("media_only_mode", False))
    media_required = bool(src.get("media_required", True))
    lookback_days = int(src.get("creator_lookback_days", 0) or 0)

    # Apply AI keyword filter for general feeds.
    if ai_filter:
        pre_count = len(entries)
        entries = [e for e in entries if _is_ai_related(e)]
        if pre_count != len(entries):
            logger.info(
                "AI filter: %d/%d entries kept for %s",
                len(entries), pre_count, url,
            )

    # For showcase feeds, keep only creator-style showcase entries.
    if content_type == "showcase":
        pre_count = len(entries)
        entries = [
            e for e in entries
            if not _is_channel_or_profile_url((e.get("link", "") or "").strip())
        ]
        if pre_count != len(entries):
            logger.info(
                "Creator URL gate: %d/%d entries kept for %s (dropped channel/profile links)",
                len(entries), pre_count, url,
            )

        pre_count = len(entries)
        entries = [
            e for e in entries
            if _is_creator_showcase_entry(e, require_video=video_only_mode)
        ]
        if pre_count != len(entries):
            logger.info(
                "Creator showcase filter: %d/%d entries kept for %s (video_only=%s)",
                len(entries), pre_count, url, video_only_mode,
            )

        if media_only_mode and media_required:
            pre_count = len(entries)
            media_gate = _has_creator_video if video_only_mode else _has_creator_media
            entries = [e for e in entries if media_gate(e)]
            if pre_count != len(entries):
                logger.info(
                    "Creator media-only filter: %d/%d entries kept for %s",
                    len(entries), pre_count, url,
                )

        if lookback_days > 0:
            pre_count = len(entries)
            entries = [e for e in entries if _is_entry_within_lookback(e, lookback_days)]
            if pre_count != len(entries):
                logger.info(
                    "Creator recency filter: %d/%d entries kept for %s (lookback_days=%d)",
                    len(entries), pre_count, url, lookback_days,
                )

    result["entries"] = entries
    result["entry_count"] = len(entries)
    result["items_count_filtered"] = len(entries)


def fetch_rss(url: str, retries: int = MAX_RETRIES) -> Dict[str, Any]:
    """Fetch and parse an RSS feed with retry logic."""
    if feedparser is None:
        raise ImportError("feedparser is required: pip install feedparser")

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            # P2.1: feedparser's internal urllib gets blocked by Reddit/Cloudflare.
            # Use requests for the HTTP fetch, then parse the response text.
            import requests as _req
            try:
                resp = _req.get(url, headers={"User-Agent": "GenLab-Fetcher/1.0"}, timeout=30)
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
            except _req.RequestException:
                # Fall back to feedparser's built-in fetcher
                old_timeout = socket.getdefaulttimeout()
                try:
                    socket.setdefaulttimeout(30)
                    feed = feedparser.parse(url, request_headers={"User-Agent": "GenLab-Fetcher/1.0"})
                finally:
                    socket.setdefaulttimeout(old_timeout)
            if feed.bozo and not feed.entries:
                raise RuntimeError(f"Feed parse error: {feed.bozo_exception}")
            if feed.bozo and feed.entries:
                logger.info("Feed %s had parse warnings but got %d entries (proceeding)",
                           url, len(feed.entries))
            entries = []
            for entry in feed.entries:
                entries.append({
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "summary": getattr(entry, "summary", ""),
                    "published": getattr(entry, "published", ""),
                    "author": getattr(entry, "get", lambda k, d="": d)("author", ""),
                })
            return {
                "url": url,
                "feed_title": getattr(feed.feed, "title", url),
                "entry_count": len(entries),
                "entries": entries,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "fetch_method": "rss",
            }
        except FEED_FETCH_ERRORS as exc:
            last_err = exc
            emit_error_event(
                logger,
                code="FETCH_RSS_RETRYABLE_ERROR",
                message="RSS fetch attempt failed",
                component="fetch_ai_creators",
                severity="warning",
                details={"url": url, "attempt": attempt, "retries": retries},
                exc=exc,
            )
            logger.warning("RSS fetch attempt %d/%d failed for %s: %s",
                           attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(RETRY_BACKOFF * attempt)

    return {
        "url": url,
        "error": str(last_err),
        "entry_count": 0,
        "entries": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "rss_failed",
    }


# Module-level session for connection pooling across API fetches
_session = None
_session_lock = threading.Lock()

def _get_session():
    """Get or create a reusable requests.Session for connection pooling."""
    global _session
    with _session_lock:
        if _session is None and requests is not None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": "GenLab-ContentScraper/1.0"})
        return _session


def fetch_api(url: str, retries: int = MAX_RETRIES) -> Dict[str, Any]:
    """Fetch from an API endpoint with retry logic."""
    if requests is None:
        raise ImportError("requests is required: pip install requests")

    session = _get_session()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", data.get("results", []))
            entries = []
            for article in articles:
                entries.append({
                    "title": article.get("title", ""),
                    "link": article.get("url", article.get("link", "")),
                    "summary": article.get("description", article.get("summary", "")),
                    "published": article.get("publishedAt", article.get("published", "")),
                    "author": article.get("author", ""),
                })
            return {
                "url": url,
                "entry_count": len(entries),
                "entries": entries,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "fetch_method": "api",
            }
        except API_FETCH_ERRORS as exc:
            last_err = exc
            emit_error_event(
                logger,
                code="FETCH_API_RETRYABLE_ERROR",
                message="API fetch attempt failed",
                component="fetch_ai_creators",
                severity="warning",
                details={"url": url, "attempt": attempt, "retries": retries},
                exc=exc,
            )
            logger.warning("API fetch attempt %d/%d failed for %s: %s",
                           attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(RETRY_BACKOFF * attempt)

    return {
        "url": url,
        "error": str(last_err),
        "entry_count": 0,
        "entries": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "api_failed",
    }


# ── Parallelization settings ─────────────────────────────────
DEFAULT_WORKERS = 5          # ThreadPoolExecutor workers
_cache_lock = threading.Lock()  # Thread-safe cache access


def _fetch_single_source(
    src: Dict[str, Any],
    cache: Optional[Cache],
    cache_ttl: int,
    limiter,
) -> Dict[str, Any]:
    """Fetch a single source (thread-safe). Called by ThreadPoolExecutor.

    Handles cache lookup, rate limiting, fetch dispatch, AI filtering,
    and cache storage for one source.
    """
    url = src["url"]
    src_type = src.get("type", "rss")
    connector_name = (src.get("connector", "") or "").strip().lower()
    key = _cache_key(url)
    source_id = _derive_source_id(src)
    source_platform = (src.get("platform", "") or "").strip().lower()
    start = time.time()

    # Try cache first (thread-safe)
    if cache:
        with _cache_lock:
            cached = cache.get(key, ttl_hours=cache_ttl)
        if cached is not None:
            # Copy before mutating so cache payload stays pristine.
            result = dict(cached)
            result["entries"] = list(cached.get("entries", []))
            _apply_entry_filters(src, result)
            elapsed = time.time() - start
            logger.info("Cache hit for %s (%.1fms)", url, elapsed * 1000)
            result["source_priority"] = src.get(
                "priority", result.get("source_priority", 0.5)
            )
            result["source_id"] = source_id
            result["platform"] = source_platform
            result["cache_hit"] = True
            result["fetch_time_ms"] = round(elapsed * 1000, 1)
            if result.get("error"):
                result["fetch_status"] = classify_fetch_status(str(result.get("error", "")))
            else:
                result["fetch_status"] = result.get("fetch_status", "success")
            return result

    # Rate limit before fetching
    wait_time = limiter.wait(url)
    if wait_time > 0:
        logger.info("Rate limited %s (waited %.1fs)", url, wait_time)

    logger.info("Fetching %s source: %s", src_type, url)

    if connector_name:
        logger.info("Fetching connector source: %s (%s)", connector_name, url)
        result = fetch_via_connector(src)
    elif src_type == "api":
        result = fetch_api(url)
    elif src_type == "html":
        result = fetch_html_source(url)
    elif src_type == "playwright" and src.get("gallery_type"):
        gallery_type = src["gallery_type"]
        logger.info("Fetching gallery: %s (%s)", gallery_type, url)
        result = fetch_gallery(url, gallery_type)
    elif src_type == "playwright":
        if is_playwright_available():
            result = fetch_js_rendered_source(url)
        else:
            logger.warning("Playwright not available, skipping JS source: %s", url)
            result = {
                "url": url,
                "error": "Playwright not installed",
                "entry_count": 0,
                "entries": [],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "fetch_method": "playwright_unavailable",
            }
    else:
        # RSS with HTML fallback, then Playwright fallback
        result = fetch_rss(url)
        if result.get("error") or result.get("entry_count", 0) == 0:
            logger.info("RSS failed/empty for %s, trying HTML scraper fallback...", url)
            limiter.wait(url)
            html_result = fetch_html_source(url)
            if html_result.get("entry_count", 0) > 0:
                result = html_result
                result["fallback_from"] = "rss"
            elif is_playwright_available():
                logger.info("HTML scraper failed too, trying Playwright for %s...", url)
                limiter.wait(url)
                pw_result = fetch_js_rendered_source(url)
                if pw_result.get("entry_count", 0) > 0:
                    result = pw_result
                    result["fallback_from"] = "rss+html"

    elapsed = time.time() - start
    result["source_priority"] = src.get("priority", 0.5)
    result["source_id"] = source_id
    result["platform"] = source_platform
    result["cache_hit"] = False
    result["fetch_time_ms"] = round(elapsed * 1000, 1)

    _apply_entry_filters(src, result)

    # Store in cache (thread-safe)
    if cache and "error" not in result:
        with _cache_lock:
            cache.set(key, result)

    if result.get("error"):
        result["fetch_status"] = classify_fetch_status(str(result.get("error", "")))
    else:
        result["fetch_status"] = result.get("fetch_status", "success")

    logger.info("  ✓ %s: %d entries in %.1fs", url[:50], result.get("entry_count", 0), elapsed)
    return result


def fetch_all_sources(
    sources: List[Dict[str, Any]],
    cache: Optional[Cache] = None,
    cache_ttl: int = DEFAULT_CACHE_TTL_HOURS,
    max_workers: int = DEFAULT_WORKERS,
) -> List[Dict[str, Any]]:
    """Fetch all sources in parallel using ThreadPoolExecutor.

    Applies per-domain rate limiting and caching (both thread-safe).
    Logs per-source timing + total wall-clock time vs sequential estimate.

    Args:
        sources:     List of source configs from sources.yaml.
        cache:       Optional file-based cache.
        cache_ttl:   Cache TTL in hours.
        max_workers: Thread pool size (default: 5).

    Returns:
        List of fetch results (same format as before).
    """
    limiter = get_limiter()
    wall_start = time.time()

    if max_workers <= 1 or len(sources) <= 1:
        # Sequential fallback (same as before)
        results = []
        for src in sources:
            result = _fetch_single_source(src, cache, cache_ttl, limiter)
            results.append(result)
        wall_elapsed = time.time() - wall_start
        logger.info("Sequential fetch: %d sources in %.1fs", len(sources), wall_elapsed)
        return results

    # ── Parallel fetch ────────────────────────────────────
    results = [None] * len(sources)  # Preserve order

    logger.info(
        "Parallel fetch: %d sources with %d workers",
        len(sources), max_workers,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, src in enumerate(sources):
            future = executor.submit(
                _fetch_single_source, src, cache, cache_ttl, limiter,
            )
            future_to_idx[future] = i

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except FETCH_WORKER_ERRORS as exc:
                src = sources[idx]
                url = src.get("url", "?")
                emit_error_event(
                    logger,
                    code="FETCH_SOURCE_WORKER_ERROR",
                    message="Source fetch worker failed",
                    component="fetch_ai_creators",
                    severity="error",
                    details={"url": url, "index": idx},
                    exc=exc,
                )
                logger.error("Source fetch crashed for %s: %s", url, exc)
                results[idx] = {
                    "url": url,
                    "source_id": _derive_source_id(src),
                    "platform": (src.get("platform", "") or "").strip().lower(),
                    "error": str(exc),
                    "fetch_status": classify_fetch_status(str(exc)),
                    "entry_count": 0,
                    "items_count_raw": 0,
                    "items_count_filtered": 0,
                    "entries": [],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "fetch_method": "thread_error",
                    "cache_hit": False,
                    "fetch_time_ms": 0,
                }

    wall_elapsed = time.time() - wall_start

    # Calculate sequential estimate for comparison
    sequential_estimate = sum(
        r.get("fetch_time_ms", 0) for r in results if r
    ) / 1000

    speedup = sequential_estimate / wall_elapsed if wall_elapsed > 0 else 1.0

    logger.info(
        "Parallel fetch complete: %d sources in %.1fs "
        "(sequential estimate: %.1fs, speedup: %.1fx)",
        len(sources), wall_elapsed, sequential_estimate, speedup,
    )

    return [r for r in results if r is not None]


def main():
    parser = argparse.ArgumentParser(description="Fetch AI news from configured sources")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH),
                        help="Path to sources.yaml config")
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL_HOURS,
                        help="Cache TTL in hours")
    parser.add_argument("--no-cache", action="store_true", help="Disable caching")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Parallel fetch workers (default: {DEFAULT_WORKERS}, set 1 for sequential)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    run_dir = PROJECT_ROOT / ".tmp" / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sources = load_sources(Path(args.config))
    if not sources:
        logger.error("No sources loaded. Exiting.")
        sys.exit(1)

    cache = None if args.no_cache else Cache()

    # ── Timed fetch (parallel by default) ─────────────────
    overall_start = time.time()
    results = fetch_all_sources(
        sources, cache=cache, cache_ttl=args.cache_ttl,
        max_workers=args.workers,
    )
    overall_elapsed = time.time() - overall_start

    total_entries = sum(r.get("entry_count", 0) for r in results)
    errors = [r for r in results if "error" in r]
    methods = {}
    for r in results:
        m = r.get("fetch_method", "unknown")
        methods[m] = methods.get(m, 0) + 1

    # Per-source timing
    per_source_timing = []
    for r in results:
        per_source_timing.append({
            "url": r.get("url", "")[:60],
            "entries": r.get("entry_count", 0),
            "time_ms": r.get("fetch_time_ms", 0),
            "cache_hit": r.get("cache_hit", False),
            "method": r.get("fetch_method", "unknown"),
        })
    per_source_timing.sort(key=lambda x: x["time_ms"], reverse=True)

    # Sequential estimate
    sequential_ms = sum(r.get("fetch_time_ms", 0) for r in results)
    speedup = (sequential_ms / 1000) / overall_elapsed if overall_elapsed > 0 else 1.0

    # Structured error summary
    error_details = []
    for r in errors:
        error_details.append({
            "url": r.get("url", ""),
            "error": r.get("error", "unknown"),
            "method": r.get("fetch_method", "unknown"),
        })

    cache_hits = sum(1 for r in results if r.get("cache_hit"))

    fetch_log = {
        "run_id": args.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources_attempted": len(sources),
        "sources_succeeded": len(results) - len(errors),
        "sources_failed": len(errors),
        "total_entries": total_entries,
        "cache_hits": cache_hits,
        "cache_misses": len(results) - cache_hits,
        "fetch_methods": methods,
        "error_details": error_details,
        "cache_stats": cache.stats() if cache else {},
        "timing": {
            "wall_clock_seconds": round(overall_elapsed, 1),
            "sequential_estimate_seconds": round(sequential_ms / 1000, 1),
            "speedup": round(speedup, 1),
            "workers": args.workers,
            "per_source": per_source_timing,
        },
        "results": results,
    }

    out_path = run_dir / "fetch_log.json"
    with open(out_path, "w") as f:
        json.dump(fetch_log, f, indent=2)

    # ── Timing report ─────────────────────────────────────
    logger.info(
        "Fetch complete: %d entries from %d sources (%d cache hits) in %.1fs → %s",
        total_entries, len(results), cache_hits, overall_elapsed, out_path,
    )
    logger.info(
        "Timing: %.1fs wall (sequential: %.1fs) → %.1fx speedup with %d workers",
        overall_elapsed, sequential_ms / 1000, speedup, args.workers,
    )
    logger.info("Methods used: %s", methods)

    # Log slowest 5 sources
    logger.info("Slowest sources:")
    for t in per_source_timing[:5]:
        logger.info(
            "  %.1fs %s (%d entries, %s)",
            t["time_ms"] / 1000, t["url"],
            t["entries"], "cached" if t["cache_hit"] else t["method"],
        )

    if errors:
        logger.warning("%d source(s) had errors:", len(errors))
        for ed in error_details:
            logger.warning("  %s: %s", ed["url"], ed["error"])


if __name__ == "__main__":
    main()
