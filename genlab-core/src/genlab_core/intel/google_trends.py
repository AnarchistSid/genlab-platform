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

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Google Trends category IDs
TRENDS_CATEGORIES = {
    "gaming": 8,       # Video Games
    "sports": 20,      # Sports
    "movies": 34,      # Movies
    "anime": 0,        # No direct category — use keyword filtering
    "ai_news": 5,      # Computers & Electronics
}

NICHE_SEED_KEYWORDS = {
    "gaming": ["gaming", "video games", "esports"],
    "sports": ["sports", "NBA", "NFL", "soccer"],
    "movies": ["movies", "film", "cinema", "trailer"],
    "anime": ["anime", "manga", "crunchyroll"],
    "ai_news": ["artificial intelligence", "AI", "machine learning"],
}


class GoogleTrendsIntel:
    """Fetches trending topics from Google Trends per niche."""

    def __init__(self, geo: str = "US", tz: int = 330):
        self.geo = geo
        self.tz = tz
        self._pytrends = None

    def _get_client(self):
        if self._pytrends is None:
            from pytrends.request import TrendReq
            self._pytrends = TrendReq(hl="en-US", tz=self.tz)
        return self._pytrends

    def get_trending_topics(
        self,
        niche_id: str,
        top_n: int = 10,
    ) -> list[str]:
        """Get top trending topics for a niche right now.

        Falls back gracefully: real-time → daily → seed keywords.
        """
        try:
            realtime = self._get_realtime_trending(niche_id)
            if realtime:
                logger.info("[%s] Google Trends real-time: %s", niche_id, realtime[:3])
                return realtime[:top_n]
        except Exception as e:
            logger.warning("[%s] Real-time trends failed: %s", niche_id, e)

        try:
            daily = self._get_daily_trending(niche_id)
            if daily:
                logger.info("[%s] Google Trends daily: %s", niche_id, daily[:3])
                return daily[:top_n]
        except Exception as e:
            logger.warning("[%s] Daily trends failed: %s", niche_id, e)

        logger.warning("[%s] Google Trends unavailable — using seed keywords", niche_id)
        return NICHE_SEED_KEYWORDS.get(niche_id, ["trending", niche_id])

    def _get_realtime_trending(self, niche_id: str) -> list[str]:
        """Get today's real-time trending searches."""
        pt = self._get_client()
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
        except Exception:
            return 1.0
