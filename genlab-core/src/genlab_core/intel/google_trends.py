"""Google Trends integration for trending topic discovery.

Uses pytrends (unofficial Python wrapper for Google Trends) to discover
what topics are trending right now. Results are used to:
1. Inform which YouTube search keywords to prioritize
2. Score content relevance (trending topics get higher priority)
3. Generate trend-aware hooks ("everyone is talking about X right now")

Usage:
    intel = GoogleTrendsIntel()
    trending = intel.get_trending_topics("gaming", top_n=10)
    # Returns: ["Marvel Rivals", "GTA 6 release", "Elden Ring DLC", ...]
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Google Trends category IDs
TRENDS_CATEGORIES = {
    "gaming": 8,       # Video Games
    "sports": 20,      # Sports
    "movies": 34,      # Movies
    "anime": 0,        # No direct category — use keyword filtering
    "ai_creators": 5,  # Computers & Electronics
}

NICHE_SEED_KEYWORDS = {
    "gaming": ["gaming", "video games", "esports", "playstation", "xbox",
               "nintendo", "steam", "twitch", "fortnite", "valorant",
               "minecraft", "call of duty", "gta", "elden ring"],
    "sports": ["sports", "NBA", "NFL", "soccer", "premier league",
               "MLB", "NHL", "UFC", "tennis", "cricket", "march madness",
               "champions league", "world cup"],
    "movies": ["movies", "film", "cinema", "trailer", "box office",
               "oscar", "marvel", "disney", "netflix", "streaming",
               "director", "actor", "sequel"],
    "anime": ["anime", "manga", "crunchyroll", "one piece", "dragon ball",
              "naruto", "jujutsu kaisen", "demon slayer", "my hero academia",
              "studio ghibli", "isekai"],
    "ai_creators": ["artificial intelligence", "AI", "machine learning",
                    "chatgpt", "openai", "claude", "gemini", "llm",
                    "deep learning", "neural network", "midjourney", "sora"],
}


_CACHE_DIR = Path(os.environ.get("GENLAB_PROJECT_ROOT", ".")) / ".tmp" / "cache"
_CACHE_TTL_HOURS = 6


def _read_cache(niche_id: str) -> list[str] | None:
    """Read cached trends if fresh (< TTL hours old)."""
    cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
    if not cache_file.exists():
        return None
    try:
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours > _CACHE_TTL_HOURS:
            return None
        data = json.loads(cache_file.read_text())
        return data if isinstance(data, list) and data else None
    except Exception as exc:  # noqa: logging below
        return None


def _write_cache(niche_id: str, topics: list[str]) -> None:
    """Write trends to cache file."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
        cache_file.write_text(json.dumps(topics))
    except Exception as exc:
        logger.debug("[trends] Cache write failed: %s", exc)


def _read_stale_cache(niche_id: str) -> list[str] | None:
    """Read cached trends even if expired — better than seed keywords."""
    cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        return data if isinstance(data, list) and data else None
    except Exception as exc:  # noqa: logging below
        return None


class GoogleTrendsIntel:
    """Fetches trending topics from Google Trends per niche.

    Results are cached to .tmp/cache/trends_{niche}.json with a 6-hour TTL.
    On failure, stale cache is used as a fallback (better than seed keywords).
    """

    def __init__(self, geo: str = "US", tz: int = 330):
        self.geo = geo
        self.tz = tz
        self._pytrends = None

    def _get_client(self):
        if self._pytrends is None:
            try:
                from pytrends.request import TrendReq
                self._pytrends = TrendReq(hl="en-US", tz=self.tz)
            except ImportError:
                pass
        return self._pytrends

    def get_trending_topics(
        self,
        niche_id: str,
        top_n: int = 10,
    ) -> list[str]:
        """Get top trending topics for a niche right now.

        Falls back gracefully: cache → RSS → pytrends → stale cache → seeds.
        Results are cached for 6 hours to avoid repeated failures.
        """
        # Tier 0: Fresh cache (< 6 hours old)
        cached = _read_cache(niche_id)
        if cached:
            logger.info("[%s] Google Trends (cached): %s", niche_id, cached[:3])
            return cached[:top_n]

        # Tier 1: Google Trends RSS (zero cost, no auth)
        try:
            rss_topics = self._get_rss_trending(niche_id)
            if rss_topics:
                _write_cache(niche_id, rss_topics)
                logger.info("[%s] Google Trends RSS: %s", niche_id, rss_topics[:3])
                return rss_topics[:top_n]
        except Exception as e:
            logger.warning("[%s] Trends RSS failed: %s", niche_id, e)

        # Tier 2: pytrends real-time (often rate-limited)
        try:
            realtime = self._get_realtime_trending(niche_id)
            realtime = [t for t in realtime if t and t.strip()] if realtime else []
            if realtime:
                _write_cache(niche_id, realtime)
                logger.info("[%s] Google Trends real-time: %s", niche_id, realtime[:3])
                return realtime[:top_n]
        except Exception as e:
            logger.warning("[%s] Real-time trends failed: %s", niche_id, e)

        # Tier 3: pytrends daily (also often rate-limited)
        try:
            daily = self._get_daily_trending(niche_id)
            daily = [t for t in daily if t and t.strip()] if daily else []
            if daily:
                _write_cache(niche_id, daily)
                logger.info("[%s] Google Trends daily: %s", niche_id, daily[:3])
                return daily[:top_n]
        except Exception as e:
            logger.warning("[%s] Daily trends failed: %s", niche_id, e)

        # Tier 4: Stale cache (expired but better than nothing)
        stale = _read_stale_cache(niche_id)
        if stale:
            logger.warning("[%s] Google Trends unavailable — using stale cache (%d topics)", niche_id, len(stale))
            return stale[:top_n]

        seeds = NICHE_SEED_KEYWORDS.get(niche_id, ["trending", niche_id])
        logger.warning("[%s] Google Trends unavailable — using seed keywords: %s", niche_id, seeds)
        return seeds

    def _get_rss_trending(self, niche_id: str) -> list[str]:
        """Fetch daily trending Google searches via official RSS feed.

        Zero cost, no auth, no rate limiting. Returns top 20 topics.
        Uses Google Trends category parameter for niche-specific results
        when available, then falls back to general trends with keyword filtering.
        """
        import urllib.request
        import xml.etree.ElementTree as ET

        # Google Trends RSS category IDs (different from YouTube categories)
        _RSS_CATEGORIES = {
            "gaming": "8",       # Games
            "sports": "20",      # Sports
            "movies": "34",      # Movies
            "ai_creators": "5",  # Computers & Electronics
            # anime: no direct category — use general + keyword filter
        }

        topics: list[str] = []

        # Try niche-specific RSS first
        cat_id = _RSS_CATEGORIES.get(niche_id)
        if cat_id:
            try:
                cat_url = f"https://trends.google.com/trending/rss?geo={self.geo}&cat={cat_id}"
                req = urllib.request.Request(cat_url, headers={"User-Agent": "GenLab/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                root = ET.fromstring(data)
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        topics.append(title_el.text.strip())
                if topics:
                    logger.info("[%s] Trends RSS category %s: %d topics", niche_id, cat_id, len(topics))
                    return topics[:20]
            except Exception as e:
                logger.debug("[%s] Category RSS failed (cat=%s): %s", niche_id, cat_id, e)

        # Fall back to general RSS with keyword filtering
        rss_url = f"https://trends.google.com/trending/rss?geo={self.geo}"
        req = urllib.request.Request(
            rss_url,
            headers={"User-Agent": "GenLab/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()

        root = ET.fromstring(data)
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                topics.append(title_el.text.strip())

        if not topics:
            return []

        # Filter for niche relevance
        niche_keywords = NICHE_SEED_KEYWORDS.get(niche_id, [])
        niche_terms = []
        general_terms = []
        for term in topics:
            term_lower = term.lower()
            if any(kw.lower() in term_lower for kw in niche_keywords):
                niche_terms.append(term)
            else:
                general_terms.append(term)

        return (niche_terms + general_terms)[:20]

    def _get_realtime_trending(self, niche_id: str) -> list[str]:
        """Get today's real-time trending searches via pytrends."""
        pt = self._get_client()
        if pt is None:
            return []
        trending_df = pt.trending_searches(pn="united_states")
        topics = trending_df[0].tolist()

        niche_keywords = NICHE_SEED_KEYWORDS.get(niche_id, [])
        niche_terms = []
        general_terms = []

        for term in topics:
            term_str = str(term)
            term_lower = term_str.lower()
            if any(kw.lower() in term_lower for kw in niche_keywords):
                niche_terms.append(term_str)
            else:
                general_terms.append(term_str)

        return (niche_terms + general_terms)[:20]

    def _get_daily_trending(self, niche_id: str) -> list[str]:
        """Get related queries from Google Trends for seed keywords."""
        pt = self._get_client()
        if pt is None:
            return []
        seeds = NICHE_SEED_KEYWORDS.get(niche_id, [niche_id])[:3]

        pt.build_payload(
            seeds,
            cat=TRENDS_CATEGORIES.get(niche_id, 0),
            timeframe="now 1-d",
            geo=self.geo,
        )
        related = pt.related_queries()

        trending_terms = []
        for seed in seeds:
            if seed in related and related[seed].get("rising") is not None:
                rising = related[seed]["rising"]
                if rising is not None and not rising.empty:
                    trending_terms.extend(rising["query"].tolist()[:5])

        return trending_terms if trending_terms else seeds

    def get_trending_score_multiplier(
        self,
        topic: str,
        niche_id: str,
    ) -> float:
        """Return a score multiplier (1.0–3.0) based on how trending a topic is."""
        try:
            trending = self.get_trending_topics(niche_id, top_n=20)
            topic_lower = topic.lower()

            for i, trend in enumerate(trending):
                if trend.lower() in topic_lower or topic_lower in trend.lower():
                    if i < 5:
                        return 3.0
                    elif i < 10:
                        return 2.0
                    else:
                        return 1.5

            return 1.0
        except Exception as exc:  # noqa: logging below
            return 1.0
