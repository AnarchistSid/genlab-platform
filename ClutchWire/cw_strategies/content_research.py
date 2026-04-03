"""ClutchWire content research strategy.

Inherits shared fetch/filter/dedup logic from BaseContentResearchStrategy.
Provides sports-specific:
- ``_fetch_stories()`` — delegates to ``fetch_all_sports_news()``
- ``_build_fetch_stats()`` — adds ESPN/RSS source breakdowns
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genlab_core.strategies import BaseContentResearchStrategy

from .fetch_sports_news import fetch_all_sports_news

NICHE_ROOT = Path(__file__).resolve().parent.parent


class SportContentResearchStrategy(BaseContentResearchStrategy):
    """Fetch and parse raw sports content from tiered sources."""

    def __init__(self) -> None:
        super().__init__(niche_id="sports", niche_root=NICHE_ROOT)

    def _fetch_stories(self, sources_config: dict) -> list[dict]:
        return fetch_all_sports_news(sources_config)

    def _build_fetch_stats(
        self,
        stories: list[dict],
        raw_count: int,
        dedup_result: Any,
        total_after_merge: int,
    ) -> dict:
        espn_count = sum(1 for s in stories if s.get("source", "").startswith("espn"))
        rss_count = sum(1 for s in stories if s.get("source", "").startswith("rss"))
        return {
            "espn_count": espn_count,
            "rss_count": rss_count,
            "total_fetched": raw_count,
            "after_dedup": len(stories),
            "dedup_pass1": dedup_result.pass1_removed,
            "dedup_pass2": dedup_result.pass2_removed,
            "dedup_pass3": dedup_result.pass3_removed,
            "after_merge": total_after_merge,
        }
