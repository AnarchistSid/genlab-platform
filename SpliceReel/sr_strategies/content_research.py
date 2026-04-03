"""SpliceReel content research strategy.

Inherits shared fetch/filter/dedup logic from BaseContentResearchStrategy.
Provides film-specific:
- ``_fetch_stories()`` — delegates to ``fetch_all_film_news()``
- ``_build_fetch_stats()`` — adds TMDB/RSS source breakdowns
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genlab_core.strategies import BaseContentResearchStrategy

from .fetch_film_news import fetch_all_film_news

NICHE_ROOT = Path(__file__).resolve().parent.parent


class MovieContentResearchStrategy(BaseContentResearchStrategy):
    """Fetch and parse raw movie content from TMDB + RSS."""

    def __init__(self) -> None:
        super().__init__(niche_id="movies", niche_root=NICHE_ROOT)

    def _fetch_stories(self, sources_config: dict) -> list[dict]:
        return fetch_all_film_news(sources_config)

    def _build_fetch_stats(
        self,
        stories: list[dict],
        raw_count: int,
        dedup_result: Any,
        total_after_merge: int,
    ) -> dict:
        tmdb_count = sum(1 for s in stories if s.get("source", "").startswith("tmdb"))
        rss_count = sum(1 for s in stories if s.get("source", "").startswith("rss"))
        return {
            "tmdb_count": tmdb_count,
            "rss_count": rss_count,
            "total_fetched": raw_count,
            "after_dedup": len(stories),
            "dedup_pass1": dedup_result.pass1_removed,
            "dedup_pass2": dedup_result.pass2_removed,
            "dedup_pass3": dedup_result.pass3_removed,
            "after_merge": total_after_merge,
        }
