"""BB content research strategy — wraps fetch_ai_creators + parse_extract."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from genlab_core.strategies import ContentResearchStrategy

BB_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class BBContentResearchStrategy(ContentResearchStrategy):
    """Fetch and parse AI/tech news from configured sources."""

    def __init__(self) -> None:
        pass

    def execute(self, context: Any) -> Any:
        from genlab_core.cache.disk_cache import Cache
        from genlab_core.intel.rss_parser import parse_fetch_log

        from bb_strategies._fetch import fetch_all_sources, load_sources

        run_id = context.get("run_id", "")
        run_dir = BB_ROOT / ".tmp" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # --- Fetch ---
        sources = load_sources()
        if not sources:
            logger.error("[ai_creators] No sources loaded")
            context.setdefault("run_stats", {})["fetch"] = {"total_entries": 0}
            return context

        cache = Cache()
        overall_start = time.time()
        results = fetch_all_sources(sources, cache=cache)
        overall_elapsed = time.time() - overall_start

        total_entries = sum(r.get("entry_count", 0) for r in results)
        errors = [r for r in results if "error" in r]
        cache_hits = sum(1 for r in results if r.get("cache_hit"))

        fetch_log = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources_attempted": len(sources),
            "sources_succeeded": len(results) - len(errors),
            "sources_failed": len(errors),
            "total_entries": total_entries,
            "cache_hits": cache_hits,
            "cache_misses": len(results) - cache_hits,
            "results": results,
        }

        fetch_log_path = run_dir / "fetch_log.json"
        with open(fetch_log_path, "w") as f:
            json.dump(fetch_log, f, indent=2)

        logger.info(
            "[ai_creators] Fetch: %d entries from %d sources in %.1fs",
            total_entries, len(results), overall_elapsed,
        )

        # --- Parse ---
        items = parse_fetch_log(fetch_log)
        flagged = [i for i in items if i.get("injection_flags")]

        output = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "item_count": len(items),
            "flagged_count": len(flagged),
            "items": items,
        }

        parsed_path = run_dir / "parsed_items.json"
        with open(parsed_path, "w") as f:
            json.dump(output, f, indent=2)

        logger.info("[ai_creators] Parsed %d items (%d flagged)", len(items), len(flagged))

        # Bridge: populate context["stories"] so shared genlab-core stages
        # (DownloadTopVideos, VideoGate, PushToBacklog, RunReport) can process them
        existing_stories = context.get("stories", [])
        for item in items:
            existing_stories.append({
                "story_id": item.get("story_id", ""),
                "title": item.get("title", ""),
                "source": item.get("source_name", "rss"),
                "source_url": item.get("url", item.get("canonical_url", "")),
                "published_at": item.get("published_at", ""),
                "fetched_at": item.get("fetched_at", datetime.now(timezone.utc).isoformat()),
                "summary": (item.get("summary") or "")[:300],
                "tags": item.get("tags", []),
                "niche_id": "ai_creators",
            })
        context["stories"] = existing_stories

        context.setdefault("run_stats", {})["fetch"] = {
            "total_entries": total_entries,
            "sources_attempted": len(sources),
            "sources_succeeded": len(results) - len(errors),
            "cache_hits": cache_hits,
            "parsed_items": len(items),
            "rss_count": len(items),
        }
        return context
