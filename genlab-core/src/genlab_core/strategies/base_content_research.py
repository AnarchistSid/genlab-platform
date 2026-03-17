"""Base content research strategy with shared fetch/filter/dedup logic.

All non-BB channels share the same research pattern:

1. Load ``sources.yaml`` from the niche config directory.
2. Call a niche-specific fetch function to pull raw stories.
3. Apply source-filter rejection patterns (title / topic).
4. Run 3-pass deduplication via ``DedupEngine``.
5. Merge into ``context["stories"]`` and populate ``run_stats["fetch"]``.

Subclasses must provide:
- ``niche_id``  — channel identifier
- ``niche_root`` — ``Path`` to the channel root directory
- ``_fetch_stories()`` — the actual niche-specific fetch call

Subclasses may override:
- ``_build_fetch_stats()`` — customize the ``run_stats["fetch"]`` dict
- ``_apply_source_filters()`` — customize rejection logic
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from genlab_core.intelligence.dedup_engine import DedupEngine
from genlab_core.ratelimit.token_bucket import TokenBucket

from .interfaces import ContentResearchStrategy

logger = logging.getLogger(__name__)

# Shared API rate limiter: 5 requests/second, burst up to 10.
_api_limiter = TokenBucket(rate=5.0, burst=10)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class BaseContentResearchStrategy(ContentResearchStrategy):
    """Shared content research logic for all video-first channels.

    Parameters
    ----------
    niche_id:
        Channel identifier (``"sports"``, ``"movies"``, ``"anime"``, etc.).
    niche_root:
        Path to the channel root directory containing ``config/``.
    """

    def __init__(self, niche_id: str, niche_root: Path) -> None:
        self._niche_id = niche_id
        self._niche_root = niche_root
        self._sources_config: dict | None = None
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _ensure_config(self) -> None:
        if self._sources_config is not None:
            return
        self._sources_config = _load_yaml(self._niche_root / "config" / "sources.yaml")

    def get_sources(self) -> dict:
        """Return the three-tier source list from sources.yaml."""
        self._ensure_config()
        assert self._sources_config is not None
        return {
            "tier_1": self._sources_config.get("tier_1", {}),
            "tier_2": self._sources_config.get("tier_2", {}),
            "tier_3": self._sources_config.get("tier_3", {}),
        }

    # ------------------------------------------------------------------
    # Abstract: subclasses implement this
    # ------------------------------------------------------------------

    def _fetch_stories(self, sources_config: dict) -> list[dict]:
        """Fetch raw stories from niche-specific sources.

        Must be overridden by each channel. Return a list of story dicts.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Source filters (shared)
    # ------------------------------------------------------------------

    def _apply_source_filters(self, stories: list, filters: dict) -> list:
        """Reject stories matching source_filters patterns."""
        if not filters:
            return stories

        reject_title = [p.lower() for p in filters.get("reject_title_patterns", [])]
        reject_topic = [p.lower() for p in filters.get("reject_topic_patterns", [])]

        filtered = []
        for story in stories:
            title = (story.get("title") or "").lower()
            source = (story.get("source") or "").lower()

            if any(p in title for p in reject_title):
                continue
            if any(p in source for p in reject_topic):
                continue
            filtered.append(story)

        rejected = len(stories) - len(filtered)
        if rejected:
            logger.info(
                "[%s] Source filters rejected %d/%d stories",
                self._niche_id,
                rejected,
                len(stories),
            )
        return filtered

    # ------------------------------------------------------------------
    # Fetch stats (override for niche-specific breakdowns)
    # ------------------------------------------------------------------

    def _build_fetch_stats(
        self,
        stories: list[dict],
        raw_count: int,
        dedup_result: Any,
        total_after_merge: int,
    ) -> dict:
        """Build the ``run_stats["fetch"]`` dict.

        Override this in subclasses to add niche-specific counters
        (e.g. ``espn_count``, ``tmdb_count``, ``emerging_count``).
        """
        rss_count = sum(
            1 for s in stories if (s.get("source") or "").startswith("rss")
        )
        return {
            "rss_count": rss_count,
            "total_fetched": raw_count,
            "after_dedup": len(stories),
            "dedup_pass1": dedup_result.pass1_removed,
            "dedup_pass2": dedup_result.pass2_removed,
            "dedup_pass3": dedup_result.pass3_removed,
            "after_merge": total_after_merge,
        }

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Fetch content, filter, dedup, and merge into context."""
        self._ensure_config()
        _api_limiter.acquire()

        assert self._sources_config is not None

        try:
            stories = self._fetch_stories(self._sources_config)
        except Exception:
            logger.exception("[%s] ContentResearch: fetch failed", self._niche_id)
            stories = []

        filters = self._sources_config.get("source_filters", {})
        stories = self._apply_source_filters(stories, filters)

        # 3-pass dedup via genlab-core DedupEngine
        raw_count = len(stories)
        niche_config = context.get("niche_config", {})
        dedup_cfg = niche_config.get("dedup", {})
        dedup = DedupEngine(
            jaccard_threshold=dedup_cfg.get("jaccard_threshold", 0.85),
            tfidf_threshold=dedup_cfg.get("tfidf_threshold", 0.80),
            url_field="source_url",
            text_field="title",
        )
        dedup_result = dedup.run(stories)
        stories = dedup_result.unique

        existing = context.get("stories", [])
        context["stories"] = existing + stories

        stats = self._build_fetch_stats(
            stories, raw_count, dedup_result, len(context["stories"])
        )
        context.setdefault("run_stats", {})["fetch"] = stats

        logger.info(
            "[%s] ContentResearch: fetched %d, after dedup %d",
            self._niche_id,
            raw_count,
            len(stories),
        )
        return context
