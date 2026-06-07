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

import yaml

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class RelevanceGate:
    """Filter stories by niche-specific keyword relevance."""

    def execute(self, context: StageContext) -> StageContext:
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
        niche_id = context.get("niche_id", "unknown")
        if not filter_cfg:
            # No content_filter configured — relevance filtering is intentionally
            # off for this niche (e.g. it filters at fetch time). Legitimate pass-through.
            logger.debug("[RelevanceGate] No content_filter for %s — skipping", niche_id)
            return context
        if not filter_cfg.get("positive_keywords"):
            # R-12: content_filter IS configured but has no positive_keywords — a
            # misconfiguration that previously caused a SILENT fail-open (off-niche
            # stories shipped unfiltered). Surface it LOUDLY (the health monitor /
            # alerting picks up the ERROR + the run_stats error) rather than fail
            # closed by dropping every story, which would dark the niche on a
            # config typo (and is already caught by R-65/R-01 if it happened).
            logger.error(
                "[RelevanceGate] content_filter for %s has no positive_keywords — "
                "relevance filtering is DISABLED, off-niche content may ship. Fix sources.yaml.",
                niche_id,
            )
            context.setdefault("run_stats", {})["relevance_gate"] = {
                "input_count": len(stories),
                "kept_count": len(stories),
                "dropped_count": 0,
                "error": "content_filter present but positive_keywords empty — filtering disabled",
            }
            return context

        from genlab_core.media.relevance_filter import RelevanceFilter

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
                len(kept),
                before,
                filter_cfg.get("relevance_threshold", 0.3),
            )

        return context
