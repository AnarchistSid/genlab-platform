"""Pipeline stage: Post-fetch relevance filtering.

Applies the RelevanceFilter from ``content_filter`` in the niche's
``sources.yaml`` to ``context["stories"]``. Stories below the relevance
threshold are dropped BEFORE download/render, saving both time and cost.

For niches where FetchTrendingVideos already applies the filter
internally (sports, movies, anime), this stage is a no-op — the
stories already passed. For niches like BB where the content research
strategy has its own fetch path and doesn't run the filter, this stage
closes the gap.

Reads:  context["stories"], context["niche_config"], context["niche_root"]
Writes: context["stories"] (filtered), context["run_stats"]["relevance_gate"]

Non-fatal: if the filter config is missing or the stage errors, all
stories pass through and downstream QC catches irrelevant content.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class RelevanceGate:
    """Filter stories by niche-specific keyword relevance."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            return context

        niche_root = context.get("niche_root")
        if not niche_root:
            return context

        sources_path = Path(niche_root) / "config" / "sources.yaml"
        if not sources_path.exists():
            return context

        try:
            with open(sources_path) as f:
                sources_cfg = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("[RelevanceGate] Failed to load sources.yaml: %s", exc)
            return context

        filter_cfg = sources_cfg.get("content_filter")
        if not filter_cfg or not filter_cfg.get("positive_keywords"):
            return context

        from genlab_core.media.relevance_filter import RelevanceFilter

        niche_id = context.get("niche_id", "unknown")
        rf = RelevanceFilter(niche_id, filter_cfg)
        before = len(stories)
        kept = rf.filter(stories)
        dropped = before - len(kept)

        context["stories"] = kept
        context.setdefault("run_stats", {})["relevance_gate"] = {
            "input_count": before,
            "kept_count": len(kept),
            "dropped_count": dropped,
            "threshold": filter_cfg.get("relevance_threshold", 0.3),
        }

        if dropped:
            logger.info(
                "[RelevanceGate] %d/%d stories passed relevance filter (threshold=%.2f)",
                len(kept), before, filter_cfg.get("relevance_threshold", 0.3),
            )

        return context
