"""FrameDrift anime news fetcher.

Primary tier: 7 anime RSS feeds with cross-mention deduplication.
Enrichment: Google Trends stub (Sprint 15) — confirms but doesn't originate.

Cross-mention velocity algorithm:
  1. Fetch all RSS items from tiered sources
  2. Group items by normalized title (word overlap within 6h window)
  3. Items appearing across 2+ sources = high signal
  4. source_mention_count propagates to scoring for TrendCycleStage detection
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml

from .trend_cycle import TrendCycleStage, detect_trend_cycle

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent

# Story type detection keywords
NEW_RELEASE_KEYWORDS = frozenset(
    {
        "premiere",
        "season",
        "episode",
        "simulcast",
        "dub",
        "sub",
        "trailer",
        "teaser",
        "debut",
        "finale",
        "ova",
        "ona",
    }
)
CREATOR_TRIGGER_WORDS = frozenset(
    {
        "directed",
        "starring",
        "voicing",
        "cast",
        "animation by",
    }
)
KNOWN_CREATORS = frozenset(
    {
        "hayao miyazaki",
        "makoto shinkai",
        "mamoru hosoda",
        "satoshi kon",
        "hideaki anno",
        "masashi kishimoto",
        "eiichiro oda",
        "akira toriyama",
        "tatsuki fujimoto",
        "koyoharu gotouge",
        "gege akutami",
        "hajime isayama",
        "hiroyuki sawano",
        "yuki kajiura",
        "linked horizon",
        "mappa",
        "ufotable",
        "wit studio",
        "studio trigger",
        "bones",
        "madhouse",
        "toei animation",
        "cloverworks",
        "a-1 pictures",
    }
)
EVENT_KEYWORDS = frozenset(
    {
        "convention",
        "expo",
        "festival",
        "comiket",
        "anime expo",
        "crunchyroll expo",
        "anime fest",
        "panel",
        "award ceremony",
    }
)
COLLAB_KEYWORDS = frozenset(
    {
        "collab",
        "collaboration",
        "crossover",
        "partnership",
    }
)
# Tier-1 sources are early movers — fresh single-source items are EMERGING
TIER1_SOURCES = frozenset({"rss_ann", "rss_crunchyroll"})


@dataclass
class AnimeStoryItem:
    """Normalized anime story from any source."""

    title: str
    source: str
    source_url: str = ""
    published_at: str = ""
    fetched_at: str = ""
    summary: str = ""
    trend_name: str = ""
    source_mention_count: int = 1
    first_seen_at: str = ""
    trend_cycle_stage: str = "unknown"
    is_new_release: bool = False
    is_creator_spotlight: bool = False
    is_event_coverage: bool = False
    is_collab: bool = False
    studio_mentioned: str = ""
    creator_name: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_title(title: str) -> str:
    """Normalize a title for cross-source deduplication."""
    normalized = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _is_creator_spotlight(title: str) -> bool:
    """True if a known anime creator/studio AND trigger word are present."""
    lower = title.lower()
    has_creator = any(name in lower for name in KNOWN_CREATORS)
    has_trigger = any(w in lower for w in CREATOR_TRIGGER_WORDS)
    return has_creator and has_trigger


def _detect_story_type(title: str) -> dict[str, bool | str]:
    """Detect anime story type from title keywords."""
    lower = title.lower()
    return {
        "is_new_release": any(kw in lower for kw in NEW_RELEASE_KEYWORDS),
        "is_creator_spotlight": _is_creator_spotlight(title),
        "is_event_coverage": any(kw in lower for kw in EVENT_KEYWORDS),
        "is_collab": any(kw in lower for kw in COLLAB_KEYWORDS),
    }


class AnimeRSSFetcher:
    """Primary anime news fetcher — RSS feeds with cross-mention grouping."""

    MAX_AGE_HOURS = 72

    def __init__(self, config: dict) -> None:
        self._tiers = {
            "tier_1": config.get("tier_1", {}),
            "tier_2": config.get("tier_2", {}),
            "tier_3": config.get("tier_3", {}),
        }
        self._cross_mention_window = config.get("cross_mention", {}).get("window_hours", 6)

    def fetch_all(self) -> list[AnimeStoryItem]:
        raw_items: list[AnimeStoryItem] = []
        now_iso = datetime.now(UTC).isoformat()

        for tier_name, tier_config in self._tiers.items():
            for source in tier_config.get("sources", []):
                if source.get("type") != "rss":
                    continue
                url = source.get("url", "")
                name = source.get("name", "")
                if not url:
                    continue
                try:
                    items = self._fetch_feed(url, name, tier_name, now_iso)
                    raw_items.extend(items)
                except Exception:
                    logger.exception("[rss] Failed to fetch %s", name)

        logger.info("[rss] Fetched %d raw items from anime feeds", len(raw_items))

        # Apply cross-mention velocity grouping
        return self._apply_cross_mention_grouping(raw_items)

    @staticmethod
    def _fetch_feed(
        url: str,
        name: str,
        tier: str,
        now_iso: str,
    ) -> list[AnimeStoryItem]:
        feed = feedparser.parse(url)
        items: list[AnimeStoryItem] = []

        for entry in feed.get("entries", [])[:12]:
            title = entry.get("title", "")
            if not title:
                continue

            published = ""
            if hasattr(entry, "published"):
                published = entry.published

            story_type = _detect_story_type(title)
            "anime_" + hashlib.sha256((name + title).encode()).hexdigest()[:16]

            items.append(
                AnimeStoryItem(
                    title=title,
                    source=f"rss_{name.lower().replace(' ', '_')}",
                    source_url=entry.get("link", ""),
                    published_at=published or now_iso,
                    fetched_at=now_iso,
                    summary=entry.get("summary", ""),
                    trend_name=_normalize_title(title)[:60],
                    **story_type,
                    extra={"tier": tier},
                )
            )

        return items

    @staticmethod
    def _make_group_key(trend_name: str) -> str:
        """Generate a grouping key from normalized trend name.

        Uses first 2-3 words of 4+ chars to group anime headlines
        that describe the same trend with different phrasing.
        """
        words = [w for w in trend_name.split() if len(w) >= 4]
        if len(words) >= 2:
            return " ".join(words[:3])
        elif words:
            return words[0]
        return trend_name[:20]

    def _apply_cross_mention_grouping(
        self,
        items: list[AnimeStoryItem],
    ) -> list[AnimeStoryItem]:
        """Group items covering same trend across sources.

        Items with overlapping significant words are grouped. The earliest
        item in each group becomes representative with source_mention_count
        set to the group size.
        """
        groups: dict[str, list[AnimeStoryItem]] = defaultdict(list)

        for item in items:
            key = self._make_group_key(item.trend_name)
            groups[key].append(item)

        result: list[AnimeStoryItem] = []
        datetime.now(UTC).isoformat()

        for group_items in groups.values():
            group_items.sort(key=lambda x: x.published_at)
            representative = group_items[0]
            mention_count = len(group_items)

            # Detect trend cycle from mention count and age
            first_seen: datetime | None = None
            if representative.published_at:
                try:
                    first_seen = datetime.fromisoformat(representative.published_at)
                    if first_seen.tzinfo is None:
                        first_seen = first_seen.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    pass

            cycle = detect_trend_cycle(
                first_seen_at=first_seen,
                source_mention_count=mention_count,
            )

            # Tier-1 fallback: fresh single-source Tier-1 items → EMERGING
            if (
                cycle == TrendCycleStage.UNKNOWN
                and mention_count == 1
                and representative.source in TIER1_SOURCES
                and first_seen is not None
            ):
                age_hours = (datetime.now(UTC) - first_seen).total_seconds() / 3600
                if age_hours <= 12:
                    cycle = TrendCycleStage.EMERGING

            representative.source_mention_count = mention_count
            representative.first_seen_at = representative.published_at
            representative.trend_cycle_stage = cycle.value

            result.append(representative)

        # Sort: EMERGING first (highest multiplier), then by mention count
        stage_priority = {
            "emerging": 0,
            "peak": 1,
            "declining": 2,
            "unknown": 3,
        }
        result.sort(
            key=lambda x: (
                stage_priority.get(x.trend_cycle_stage, 3),
                -x.source_mention_count,
            )
        )

        emerging = sum(1 for i in result if i.trend_cycle_stage == "emerging")
        peak = sum(1 for i in result if i.trend_cycle_stage == "peak")
        declining = sum(1 for i in result if i.trend_cycle_stage == "declining")
        logger.info(
            "[fetch] FrameDrift: %d grouped trends from %d raw items "
            "(EMERGING: %d, PEAK: %d, DECLINING: %d)",
            len(result),
            len(items),
            emerging,
            peak,
            declining,
        )
        return result


def _google_trends_stub(config: dict, titles: list[str]) -> list[dict]:
    """Google Trends enrichment stub — logs what would be queried.

    Returns empty list (no enrichment).
    """
    gt_config = config.get("google_trends", {})
    if not gt_config.get("enabled", False):
        return []

    keywords = [t[:40] for t in titles[:5]]
    logger.info(
        "[trends] Google Trends stub — would query: %s. Set stub_mode=false to enable.",
        keywords,
    )
    return []


def _fetch_google_trends(config: dict, titles: list[str]) -> list[dict]:
    """Fetch Google Trends interest for title keywords.

    Returns list of dicts: [{"title": str, "trend_score": int}, ...]
    where trend_score is the max interest value (0-100) from the last 7 days.
    """
    from pytrends.request import TrendReq

    gt_config = config.get("google_trends", {})
    if not gt_config.get("enabled", False):
        return []

    timeframe = gt_config.get("timeframe", "now 7-d")

    pytrends = TrendReq(hl="en-US", tz=330)  # IST offset
    results: list[dict] = []

    # Process in batches of 5 (Google Trends limit)
    for i in range(0, len(titles), 5):
        batch = titles[i : i + 5]
        keywords = [t[:100] for t in batch]  # Truncate long titles
        try:
            pytrends.build_payload(keywords, timeframe=timeframe)
            interest = pytrends.interest_over_time()
            for kw in keywords:
                if not interest.empty and kw in interest.columns:
                    score = int(interest[kw].max())
                else:
                    score = 0
                results.append({"title": kw, "trend_score": score})
        except Exception as e:
            logger.warning("[google_trends] Failed for batch: %s", e)
            for kw in keywords:
                results.append({"title": kw, "trend_score": 0})

    logger.info(
        "[google_trends] Fetched trend scores for %d titles (%d with score > 0)",
        len(results),
        sum(1 for r in results if r["trend_score"] > 0),
    )
    return results


def _get_google_trends(config: dict, titles: list[str]) -> list[dict]:
    """Route to stub or real Google Trends based on config.

    When ``google_trends.stub_mode`` is true (default), uses the lightweight
    stub that only logs.  When false, calls pytrends for real data.
    """
    gt_config = config.get("google_trends", {})
    if not gt_config.get("enabled", False):
        return []

    if gt_config.get("stub_mode", True):
        return _google_trends_stub(config, titles)

    return _fetch_google_trends(config, titles)


def fetch_all_anime_news(config: dict | None = None) -> list[dict[str, Any]]:
    """Entry point: fetch from RSS feeds, return normalized dicts."""
    if config is None:
        with open(NICHE_ROOT / "config" / "sources.yaml") as f:
            config = yaml.safe_load(f) or {}

    fetcher = AnimeRSSFetcher(config)
    items = fetcher.fetch_all()

    # Google Trends enrichment (stub or real based on config)
    trend_scores = _get_google_trends(config, [i.title for i in items])

    # Merge trend scores into items
    if trend_scores:
        score_map = {r["title"][:100]: r["trend_score"] for r in trend_scores}
        for item in items:
            score = score_map.get(item.title[:100], 0)
            if score > 0:
                item.extra["google_trend_score"] = score

    logger.info("[fetch] FrameDrift: %d total anime stories", len(items))
    return [item.to_dict() for item in items]
