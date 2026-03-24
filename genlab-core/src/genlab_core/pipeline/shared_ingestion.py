"""SharedIngestionPipeline -- fetch, classify, route content to the shared pool.

Unified ingestion pipeline that:
  1. Loads shared_sources.yaml
  2. Fetches YouTube trending for configured categories (YouTube Data API)
  3. Fetches YouTube channel RSS (feedparser, zero quota)
  4. Fetches Reddit RSS feeds (feedparser)
  5. Fetches general RSS feeds (feedparser)
  6. Deduplicates by URL hash
  7. Classifies each story using NicheClassifier
  8. Writes to content_pool table (psycopg3 upsert)
  9. Expires entries older than 48h
 10. Returns a routing report

Run as:
    python -m genlab_core.pipeline.shared_ingestion
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import psycopg
import requests
import yaml

from genlab_core.intelligence.niche_classifier import NicheClassifier

logger = logging.getLogger(__name__)

# Config path relative to genlab-core/
_THIS_DIR = Path(__file__).resolve().parent
_GENLAB_CORE_ROOT = _THIS_DIR.parent.parent.parent  # genlab-core/
_SHARED_SOURCES_PATH = _GENLAB_CORE_ROOT / "config" / "shared_sources.yaml"

# YouTube API
_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# feedparser user agent to avoid 403s
_USER_AGENT = "GenLab/1.0 SharedIngestionPipeline (+https://aspirehub.ai)"

# Request timeout for RSS fetches
_FETCH_TIMEOUT = 15


def _content_hash(url: str) -> str:
    """Compute dedup hash for a source URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


@dataclass
class PoolEntry:
    """A single content item ready for pool insertion."""

    content_hash: str
    title: str
    summary: str = ""
    source_url: str = ""
    source_name: str = ""
    source_platform: str = ""  # youtube, reddit, rss
    video_url: str | None = None
    video_id: str | None = None
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    view_velocity: float | None = None
    source_affinity: list[str] = field(default_factory=list)
    youtube_category_id: str | None = None
    niche_scores: dict[str, float] = field(default_factory=dict)
    routed_niches: list[str] = field(default_factory=list)
    routing_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class SharedIngestionPipeline:
    """Fetch from all sources, classify, and route to content_pool."""

    def __init__(
        self,
        config_path: Path | None = None,
        database_url: str | None = None,
        youtube_api_key: str | None = None,
    ) -> None:
        self._config_path = config_path or _SHARED_SOURCES_PATH
        self._db_url = database_url or os.environ.get("DATABASE_URL", "")
        self._yt_api_key = youtube_api_key or os.environ.get("YOUTUBE_API_KEY", "")
        self._config: dict[str, Any] = {}
        self._classifier = NicheClassifier()
        self._entries: list[PoolEntry] = []
        self._seen_hashes: set[str] = set()
        self._stats: dict[str, int] = {
            "yt_trending": 0,
            "yt_channels": 0,
            "reddit": 0,
            "rss": 0,
            "total_fetched": 0,
            "deduped": 0,
            "classified": 0,
            "routed": 0,
            "inserted": 0,
            "expired": 0,
            "errors": 0,
        }

    def _load_config(self) -> None:
        """Load shared_sources.yaml."""
        logger.info("[SharedIngestion] Loading config from %s", self._config_path)
        self._config = yaml.safe_load(self._config_path.read_text())

    def _add_entry(self, entry: PoolEntry) -> bool:
        """Add entry if not a duplicate. Returns True if added."""
        if entry.content_hash in self._seen_hashes:
            self._stats["deduped"] += 1
            return False
        self._seen_hashes.add(entry.content_hash)
        self._entries.append(entry)
        return True

    # ── YouTube Trending (API, 1 unit per category) ─────────────────

    def _fetch_youtube_trending(self) -> None:
        """Fetch YouTube mostPopular for each configured category."""
        if not self._yt_api_key:
            logger.warning("[SharedIngestion] No YOUTUBE_API_KEY, skipping trending")
            return

        categories = self._config.get("youtube_categories", [])
        for cat in categories:
            cat_id = cat["id"]
            affinity = cat.get("affinity", [])
            try:
                logger.info(
                    "[SharedIngestion] Fetching YouTube trending category %s (%s)",
                    cat_id,
                    cat.get("name", ""),
                )
                resp = requests.get(
                    f"{_YOUTUBE_API_BASE}/videos",
                    params={
                        "part": "snippet,statistics",
                        "chart": "mostPopular",
                        "regionCode": "US",
                        "videoCategoryId": cat_id,
                        "maxResults": 10,
                        "key": self._yt_api_key,
                    },
                    timeout=_FETCH_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("items", []):
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    video_id = item["id"]
                    url = f"https://www.youtube.com/watch?v={video_id}"

                    published_str = snippet.get("publishedAt", "")
                    published_at = None
                    if published_str:
                        try:
                            published_at = datetime.fromisoformat(
                                published_str.replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass

                    view_count = int(stats.get("viewCount", 0))
                    velocity = None
                    if published_at:
                        hours_old = max(
                            (datetime.now(UTC) - published_at).total_seconds() / 3600,
                            0.5,
                        )
                        velocity = view_count / hours_old

                    entry = PoolEntry(
                        content_hash=_content_hash(url),
                        title=snippet.get("title", ""),
                        summary=snippet.get("description", "")[:500],
                        source_url=url,
                        source_name=snippet.get("channelTitle", ""),
                        source_platform="youtube",
                        video_url=url,
                        video_id=video_id,
                        thumbnail_url=(
                            snippet.get("thumbnails", {})
                            .get("high", {})
                            .get("url", "")
                        ),
                        published_at=published_at,
                        view_count=view_count,
                        view_velocity=velocity,
                        source_affinity=affinity,
                        youtube_category_id=cat_id,
                    )
                    if self._add_entry(entry):
                        self._stats["yt_trending"] += 1

            except Exception as exc:
                logger.warning(
                    "[SharedIngestion] YouTube trending cat %s failed: %s",
                    cat_id,
                    exc,
                )
                self._stats["errors"] += 1

    # ── YouTube Channel RSS (feedparser, 0 quota) ───────────────────

    def _fetch_youtube_channels(self) -> None:
        """Fetch YouTube channel RSS feeds via feedparser."""
        channels = self._config.get("youtube_channels", [])
        logger.info(
            "[SharedIngestion] Fetching %d YouTube channel RSS feeds", len(channels)
        )

        for ch in channels:
            name = ch.get("name", "unknown")
            url = ch.get("url", "")
            affinity = ch.get("affinity", [])
            if not url:
                continue

            try:
                feed = feedparser.parse(url, agent=_USER_AGENT)
                for entry in feed.entries[:10]:  # limit per channel
                    link = entry.get("link", "")
                    if not link:
                        continue

                    # Extract video_id from YouTube URL
                    video_id = None
                    yt_link = entry.get("yt_videoid", "")
                    if yt_link:
                        video_id = yt_link
                    elif "watch?v=" in link:
                        video_id = link.split("watch?v=")[-1].split("&")[0]

                    published_at = None
                    if entry.get("published_parsed"):
                        try:
                            published_at = datetime(
                                *entry.published_parsed[:6], tzinfo=UTC
                            )
                        except (TypeError, ValueError):
                            pass

                    # Skip entries older than 48h
                    if published_at and (datetime.now(UTC) - published_at) > timedelta(
                        hours=48
                    ):
                        continue

                    thumbnail = ""
                    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                        thumbnail = entry.media_thumbnail[0].get("url", "")

                    pool_entry = PoolEntry(
                        content_hash=_content_hash(link),
                        title=entry.get("title", ""),
                        summary=entry.get("summary", "")[:500],
                        source_url=link,
                        source_name=name,
                        source_platform="youtube",
                        video_url=link,
                        video_id=video_id,
                        thumbnail_url=thumbnail,
                        published_at=published_at,
                        source_affinity=affinity,
                    )
                    if self._add_entry(pool_entry):
                        self._stats["yt_channels"] += 1

            except Exception as exc:
                logger.warning(
                    "[SharedIngestion] YouTube RSS %s failed: %s", name, exc
                )
                self._stats["errors"] += 1

    # ── Reddit RSS ──────────────────────────────────────────────────

    def _fetch_reddit_feeds(self) -> None:
        """Fetch Reddit RSS feeds via feedparser."""
        feeds = self._config.get("reddit_feeds", [])
        logger.info("[SharedIngestion] Fetching %d Reddit RSS feeds", len(feeds))

        for feed_cfg in feeds:
            name = feed_cfg.get("name", "unknown")
            url = feed_cfg.get("url", "")
            affinity = feed_cfg.get("affinity", [])
            if not url:
                continue

            try:
                feed = feedparser.parse(url, agent=_USER_AGENT)
                for entry in feed.entries[:15]:
                    link = entry.get("link", "")
                    if not link:
                        continue

                    published_at = None
                    if entry.get("published_parsed"):
                        try:
                            published_at = datetime(
                                *entry.published_parsed[:6], tzinfo=UTC
                            )
                        except (TypeError, ValueError):
                            pass

                    # Skip entries older than 48h
                    if published_at and (datetime.now(UTC) - published_at) > timedelta(
                        hours=48
                    ):
                        continue

                    pool_entry = PoolEntry(
                        content_hash=_content_hash(link),
                        title=entry.get("title", ""),
                        summary=entry.get("summary", "")[:500],
                        source_url=link,
                        source_name=name,
                        source_platform="reddit",
                        published_at=published_at,
                        source_affinity=affinity,
                    )
                    if self._add_entry(pool_entry):
                        self._stats["reddit"] += 1

            except Exception as exc:
                logger.warning(
                    "[SharedIngestion] Reddit RSS %s failed: %s", name, exc
                )
                self._stats["errors"] += 1

    # ── General RSS ─────────────────────────────────────────────────

    def _fetch_rss_feeds(self) -> None:
        """Fetch general RSS feeds via feedparser."""
        feeds = self._config.get("rss_feeds", [])
        logger.info("[SharedIngestion] Fetching %d general RSS feeds", len(feeds))

        for feed_cfg in feeds:
            name = feed_cfg.get("name", "unknown")
            url = feed_cfg.get("url", "")
            affinity = feed_cfg.get("affinity", [])
            if not url:
                continue

            try:
                feed = feedparser.parse(url, agent=_USER_AGENT)
                for entry in feed.entries[:10]:
                    link = entry.get("link", "")
                    if not link:
                        continue

                    published_at = None
                    if entry.get("published_parsed"):
                        try:
                            published_at = datetime(
                                *entry.published_parsed[:6], tzinfo=UTC
                            )
                        except (TypeError, ValueError):
                            pass

                    # Skip entries older than 48h
                    if published_at and (datetime.now(UTC) - published_at) > timedelta(
                        hours=48
                    ):
                        continue

                    pool_entry = PoolEntry(
                        content_hash=_content_hash(link),
                        title=entry.get("title", ""),
                        summary=entry.get("summary", "")[:500],
                        source_url=link,
                        source_name=name,
                        source_platform="rss",
                        published_at=published_at,
                        source_affinity=affinity,
                    )
                    if self._add_entry(pool_entry):
                        self._stats["rss"] += 1

            except Exception as exc:
                logger.warning(
                    "[SharedIngestion] RSS %s failed: %s", name, exc
                )
                self._stats["errors"] += 1

    # ── Classification ──────────────────────────────────────────────

    def _classify_all(self) -> None:
        """Run NicheClassifier on all entries and set routed_niches."""
        logger.info(
            "[SharedIngestion] Classifying %d entries", len(self._entries)
        )

        for entry in self._entries:
            scores, routed = self._classifier.classify_and_route(
                title=entry.title,
                description=entry.summary,
                source_affinity=entry.source_affinity,
                youtube_category=entry.youtube_category_id,
            )
            entry.niche_scores = scores
            entry.routed_niches = routed
            self._stats["classified"] += 1

            if routed:
                self._stats["routed"] += 1
                entry.routing_reason = (
                    f"Top scores: {', '.join(f'{n}={scores[n]:.2f}' for n in routed)}"
                )

    # ── Database ────────────────────────────────────────────────────

    def _write_to_pool(self) -> None:
        """Upsert classified entries into content_pool via psycopg3."""
        if not self._db_url:
            logger.warning(
                "[SharedIngestion] No DATABASE_URL, skipping DB write"
            )
            return

        routed_entries = [e for e in self._entries if e.routed_niches]
        if not routed_entries:
            logger.info("[SharedIngestion] No routed entries to write")
            return

        logger.info(
            "[SharedIngestion] Writing %d routed entries to content_pool",
            len(routed_entries),
        )

        upsert_sql = """
            INSERT INTO content_pool (
                content_hash, title, summary, source_url, source_name,
                source_platform, video_url, video_id, thumbnail_url,
                published_at, duration_seconds, view_count, view_velocity,
                source_affinity, youtube_category_id, niche_scores,
                routed_niches, routing_reason, extra
            ) VALUES (
                %(content_hash)s, %(title)s, %(summary)s, %(source_url)s,
                %(source_name)s, %(source_platform)s, %(video_url)s,
                %(video_id)s, %(thumbnail_url)s, %(published_at)s,
                %(duration_seconds)s, %(view_count)s, %(view_velocity)s,
                %(source_affinity)s, %(youtube_category_id)s,
                %(niche_scores)s, %(routed_niches)s, %(routing_reason)s,
                %(extra)s
            )
            ON CONFLICT (content_hash) DO UPDATE SET
                niche_scores = EXCLUDED.niche_scores,
                routed_niches = EXCLUDED.routed_niches,
                routing_reason = EXCLUDED.routing_reason,
                view_count = COALESCE(EXCLUDED.view_count, content_pool.view_count),
                view_velocity = COALESCE(EXCLUDED.view_velocity, content_pool.view_velocity)
        """

        try:
            with psycopg.connect(self._db_url) as conn:
                with conn.cursor() as cur:
                    for entry in routed_entries:
                        try:
                            # Savepoint so a single row failure doesn't
                            # abort the entire transaction.
                            cur.execute("SAVEPOINT sp_upsert")
                            cur.execute(
                                upsert_sql,
                                {
                                    "content_hash": entry.content_hash,
                                    "title": entry.title,
                                    "summary": entry.summary,
                                    "source_url": entry.source_url,
                                    "source_name": entry.source_name,
                                    "source_platform": entry.source_platform,
                                    "video_url": entry.video_url,
                                    "video_id": entry.video_id,
                                    "thumbnail_url": entry.thumbnail_url,
                                    "published_at": entry.published_at,
                                    "duration_seconds": entry.duration_seconds,
                                    "view_count": entry.view_count,
                                    "view_velocity": entry.view_velocity,
                                    "source_affinity": entry.source_affinity,
                                    "youtube_category_id": entry.youtube_category_id,
                                    "niche_scores": json.dumps(entry.niche_scores),
                                    "routed_niches": entry.routed_niches,
                                    "routing_reason": entry.routing_reason,
                                    "extra": json.dumps(entry.extra),
                                },
                            )
                            cur.execute("RELEASE SAVEPOINT sp_upsert")
                            self._stats["inserted"] += 1
                        except Exception as exc:
                            cur.execute("ROLLBACK TO SAVEPOINT sp_upsert")
                            logger.warning(
                                "[SharedIngestion] Failed to upsert %s: %s",
                                entry.content_hash[:8],
                                exc,
                            )
                            self._stats["errors"] += 1

                conn.commit()
                logger.info(
                    "[SharedIngestion] Committed %d entries to content_pool",
                    self._stats["inserted"],
                )

        except Exception as exc:
            logger.error("[SharedIngestion] Database connection failed: %s", exc)
            self._stats["errors"] += 1

    def _expire_old_entries(self) -> None:
        """Mark entries older than 48h as expired."""
        if not self._db_url:
            return

        try:
            with psycopg.connect(self._db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE content_pool
                        SET status = 'expired'
                        WHERE status = 'available'
                          AND expires_at < NOW()
                        """
                    )
                    expired = cur.rowcount
                    conn.commit()
                    self._stats["expired"] = expired
                    if expired:
                        logger.info(
                            "[SharedIngestion] Expired %d old entries", expired
                        )
        except Exception as exc:
            logger.warning("[SharedIngestion] Expire sweep failed: %s", exc)

    # ── Report ──────────────────────────────────────────────────────

    def _build_report(self) -> str:
        """Build a human-readable routing report."""
        self._stats["total_fetched"] = (
            self._stats["yt_trending"]
            + self._stats["yt_channels"]
            + self._stats["reddit"]
            + self._stats["rss"]
        )

        # Count per-niche routing
        niche_counts: dict[str, int] = {}
        for entry in self._entries:
            for niche in entry.routed_niches:
                niche_counts[niche] = niche_counts.get(niche, 0) + 1

        lines = [
            "",
            "=" * 60,
            "  SHARED INGESTION REPORT",
            "=" * 60,
            f"  YouTube trending : {self._stats['yt_trending']:>4}",
            f"  YouTube channels : {self._stats['yt_channels']:>4}",
            f"  Reddit feeds     : {self._stats['reddit']:>4}",
            f"  RSS feeds        : {self._stats['rss']:>4}",
            f"  Total fetched    : {self._stats['total_fetched']:>4}",
            f"  Duplicates       : {self._stats['deduped']:>4}",
            f"  Classified       : {self._stats['classified']:>4}",
            f"  Routed (>=1)     : {self._stats['routed']:>4}",
            f"  DB inserts       : {self._stats['inserted']:>4}",
            f"  Expired          : {self._stats['expired']:>4}",
            f"  Errors           : {self._stats['errors']:>4}",
            "-" * 60,
            "  ROUTING BREAKDOWN:",
        ]
        for niche_id in ["ai_creators", "gaming", "sports", "movies", "anime"]:
            count = niche_counts.get(niche_id, 0)
            lines.append(f"    {niche_id:<15}: {count:>4}")
        lines.append("=" * 60)
        lines.append("")

        return "\n".join(lines)

    # ── Main ────────────────────────────────────────────────────────

    def run(self) -> str:
        """Execute the full ingestion pipeline. Returns a routing report."""
        start = time.time()
        logger.info("[SharedIngestion] Starting shared ingestion pipeline")

        # 1. Load config
        self._load_config()

        # 2. Fetch from all sources
        self._fetch_youtube_trending()
        self._fetch_youtube_channels()
        self._fetch_reddit_feeds()
        self._fetch_rss_feeds()

        # 3. Classify all entries
        self._classify_all()

        # 4. Write to DB
        self._write_to_pool()

        # 5. Expire old entries
        self._expire_old_entries()

        # 6. Report
        elapsed = time.time() - start
        report = self._build_report()
        logger.info(
            "[SharedIngestion] Completed in %.1fs — %d fetched, %d routed, %d inserted",
            elapsed,
            self._stats["total_fetched"],
            self._stats["routed"],
            self._stats["inserted"],
        )

        return report


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    pipeline = SharedIngestionPipeline()
    report = pipeline.run()
    print(report)


if __name__ == "__main__":
    main()
