"""BB content research strategy — wraps fetch_ai_creators + parse_extract."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from genlab_core.strategies import ContentResearchStrategy

BB_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


# ── Thin-context filter (fetcher-side quality gate) ──────────────
#
# 2026-08-05: fetcher-side fix for the "flat ai_creators hooks" pattern
# found in .audit/GENERATION_CAPABILITY_EVAL.md. RSS items reach the
# parsed_items stage with summaries in three shapes:
#
#   1. Empty (Reddit v.redd.it video posts — image tags stripped to nothing)
#   2. URL-wrapper only: "Watch: https://... Thumbnail: https://..."
#      (YouTube creators who put minimal descriptions on their videos)
#   3. Real prose descriptions
#
# Shapes 1 + 2 pass into the writer, which then EITHER (a) triggers the
# _has_writable_context skip → template-fallback → hook=title verbatim,
# or (b) runs the LLM on garbage input which produces flat title-restated
# output. Neither shape can generate a real hook. Filtering them at the
# fetcher boundary means fewer blueprints per day, but the ones that DO
# reach the writer have real context to work with.
#
# The floor matches base_writing._MIN_WRITABLE_CONTEXT_CHARS = 40 so this
# filter and the writer's own skip use the same threshold. Difference:
# writer's skip checks raw length; this filter checks length AFTER
# stripping URLs, hashtags, and RSS-wrapper prefixes so YouTube's
# "Watch: URL Thumbnail: URL" pattern is correctly identified as thin.

_URL_PATTERN = re.compile(r"https?://\S+")
_HASHTAG_PATTERN = re.compile(r"#\w+")
_WRAPPER_PREFIXES = re.compile(
    r"\b(Watch|Thumbnail|ThumbnailBackup|Source|Link|via|Video|Media):\s*",
    re.IGNORECASE,
)
_MIN_MEANINGFUL_CHARS = 40


def _is_thin_context(text: str | None) -> bool:
    """True if the summary field has no meaningful prose for the LLM to write about.

    Detects three failure shapes seen in production RSS output:
      * empty / whitespace-only
      * shorter than the writer's 40-char floor (identical to
        ``base_writing._MIN_WRITABLE_CONTEXT_CHARS``)
      * URL-wrapper only — the YouTube "Watch: URL Thumbnail: URL" case
        where the raw text is above the floor but strips to nothing
        meaningful once URLs + hashtags + wrapper prefixes are removed
    """
    if not text or not isinstance(text, str):
        return True
    stripped = text.strip()
    if len(stripped) < _MIN_MEANINGFUL_CHARS:
        return True
    residual = _URL_PATTERN.sub("", stripped)
    residual = _HASHTAG_PATTERN.sub("", residual)
    residual = _WRAPPER_PREFIXES.sub("", residual)
    residual = residual.strip()
    return len(residual) < _MIN_MEANINGFUL_CHARS


def _filter_thin_context_items(items: list[dict]) -> list[dict]:
    """Drop items whose summary is thin context. Preserves order.

    Pure function; safe to test without mocks. Called after
    ``parse_fetch_log`` so items reaching ``context["stories"]``
    already have real prose for the LLM to react to.
    """
    return [item for item in items if not _is_thin_context(item.get("summary", ""))]


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
            total_entries,
            len(results),
            overall_elapsed,
        )

        # --- Parse ---
        items = parse_fetch_log(fetch_log)
        flagged = [i for i in items if i.get("injection_flags")]

        # --- Thin-context filter (2026-08-05) ---
        # Drop items whose summary is empty or URL-wrapper only. Both shapes
        # would otherwise reach the writer, trigger either _has_writable_context
        # skip → template-fallback hook=title OR a Haiku call on garbage
        # input → flat title-restated hook. Filtering at the fetcher boundary
        # cuts fewer-but-higher-signal blueprints. See
        # .audit/GENERATION_CAPABILITY_EVAL.md for the trace.
        pre_filter = len(items)
        items = _filter_thin_context_items(items)
        dropped_thin = pre_filter - len(items)

        output = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "item_count": len(items),
            "flagged_count": len(flagged),
            "dropped_thin_context": dropped_thin,
            "items": items,
        }

        parsed_path = run_dir / "parsed_items.json"
        with open(parsed_path, "w") as f:
            json.dump(output, f, indent=2)

        logger.info(
            "[ai_creators] Parsed %d items (%d flagged, %d thin-context dropped)",
            len(items), len(flagged), dropped_thin,
        )

        # Bridge: populate context["stories"] so shared genlab-core stages
        # (DownloadTopVideos, VideoGate, PushToBacklog, RunReport) can process them
        existing_stories = context.get("stories", [])
        for item in items:
            existing_stories.append(
                {
                    "story_id": item.get("story_id", ""),
                    "title": item.get("title", ""),
                    "source": item.get("source_name", "rss"),
                    "source_url": item.get("url", item.get("canonical_url", "")),
                    "published_at": item.get("published_at", ""),
                    "fetched_at": item.get("fetched_at", datetime.now(timezone.utc).isoformat()),
                    "summary": (item.get("summary") or "")[:300],
                    "tags": item.get("tags", []),
                    "niche_id": "ai_creators",
                }
            )
        context["stories"] = existing_stories

        context.setdefault("run_stats", {})["fetch"] = {
            "total_entries": total_entries,
            "sources_attempted": len(sources),
            "sources_succeeded": len(results) - len(errors),
            "cache_hits": cache_hits,
            "parsed_items": len(items),
            "rss_count": len(items),
            "dropped_thin_context": dropped_thin,
        }
        return context
