"""Pipeline stage: Fetch football/soccer highlights from ScoreBat.

ScoreBat returns match metadata and embed codes — NOT downloadable MP4s.
Use for story discovery: pair with YouTube channel clips for actual footage.
No auth required. Free API.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _fetch_highlights(max_items: int = 10, competitions: list[str] | None = None) -> list[dict]:
    """Fetch recent football/soccer highlights from ScoreBat."""
    try:
        r = requests.get(
            "https://www.scorebat.com/video-api/v1/",
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for item in r.json()[:max_items * 2]:  # Fetch extra, filter by competition
            comp = item.get("competition", {}).get("name", "")
            if competitions and not any(c.lower() in comp.lower() for c in competitions):
                continue
            results.append({
                "title": item.get("title", ""),
                "competition": comp,
                "url": item.get("url", ""),
                "embed": item.get("embed", ""),
                "source": "scorebat",
                # ScoreBat provides embeds, not MP4s. VideoGate will skip
                # unless a matching YouTube clip is found downstream.
                "clip_url": None,
            })
            if len(results) >= max_items:
                break
        return results
    except Exception as e:
        logger.warning("[ScoreBat] Fetch failed: %s", e)
        return []


class FetchScoreBatHighlights:
    """Pipeline stage: fetch football highlight story angles from ScoreBat.

    ScoreBat results provide match context (teams, competition, score)
    that helps the pipeline match with YouTube highlight clips from
    subscribed sports channels (via RSS/playlist feeds).

    clip_url is None — VideoGate handles this correctly.
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")
        if niche_id != "sports":
            return context

        sources_config = context.get("sources_config", {})
        scorebat_cfg = sources_config.get("scorebat", {})
        if scorebat_cfg.get("enabled") is False:
            return context

        max_items = scorebat_cfg.get("max_highlights", 10)
        competitions = scorebat_cfg.get("competitions")

        highlights = _fetch_highlights(max_items, competitions)
        logger.info("[ScoreBat] %d football highlights (story discovery only)", len(highlights))

        if highlights:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for h in highlights:
                sid = generate_story_id(h["url"] or h["title"], now_iso)
                new_stories.append({
                    "story_id": sid,
                    "title": h["title"],
                    "source": "scorebat",
                    "source_url": h.get("url", ""),
                    "canonical_url": h.get("url", ""),
                    "published_at": now_iso,
                    "fetched_at": now_iso,
                    "summary": f"{h.get('competition', '')} highlight",
                    "niche_id": niche_id,
                    "video_source": "scorebat",
                    # No clip_url — VideoGate will gate this unless matched
                    "source_mention_count": 1,
                })

            existing = context.get("stories", [])
            existing_urls = {s.get("source_url") for s in existing}
            new_stories = [s for s in new_stories if s.get("source_url") and s["source_url"] not in existing_urls]
            context["stories"] = existing + new_stories

        run_stats = context.setdefault("run_stats", {})
        run_stats["scorebat_highlights_found"] = len(highlights)
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
