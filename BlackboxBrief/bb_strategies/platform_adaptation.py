"""BB platform adaptation strategy.

Migrated to BasePlatformAdaptationStrategy (Sprint 69). Inherits full
8-rule platform enforcement for all 6 platforms.

Adds (OPS #2 + #6, 2026-06-30) the source-tool affiliate disclosure
hook — after the standard per-platform adaptations run, look at the
SOURCE video's metadata and attach a "Made with <Tool> — <link>"
disclosure when the creator explicitly used a known AI tool
(Runway, Sora, Midjourney to start). See
``bb_strategies.affiliate_match`` for the detection logic + safety
rules (only fires when no existing affiliate is attached and only
for ai_creators).

Previous version (61 lines) shelled out to adapt_for_platforms.py via
subprocess, which is preserved at execution/adapt_for_platforms.py
but no longer called from the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from genlab_core.strategies import BasePlatformAdaptationStrategy

from bb_strategies.affiliate_match import apply_source_tool_disclosure

logger = logging.getLogger(__name__)

BB_ROOT = Path(__file__).resolve().parent.parent


class BBPlatformAdaptationStrategy(BasePlatformAdaptationStrategy):
    """Adapt AI creator content for platform-specific requirements."""

    def __init__(self) -> None:
        super().__init__(niche_id="ai_creators", niche_root=BB_ROOT)

    def _adapt_story(self, content: dict[str, Any], story: dict[str, Any]) -> None:
        """Run the base 6-platform adaptation, then layer source-tool
        affiliate disclosure on top.

        The base call is preserved verbatim so all upstream rules
        (URL stripping, hashtag enforcement, CTA fallback,
        truncation) still fire. The source-tool disclosure is
        appended AFTER, so it lands in the final caption and is
        immune to the URL-stripping pass (the platform-rule URL
        scrub already ran before this point).

        Failure-mode contract: the source-tool path is purely
        additive. If detection raises, the base adaptation has
        ALREADY completed and we log + swallow — the post still
        ships, just without the disclosure.
        """
        super()._adapt_story(content, story)
        try:
            apply_source_tool_disclosure(content, story)
        except Exception as exc:  # noqa: BLE001 — additive layer
            logger.warning(
                "[%s] source-tool disclosure failed for story %s: %s",
                self._niche_id,
                str(story.get("title", ""))[:80],
                exc,
            )
