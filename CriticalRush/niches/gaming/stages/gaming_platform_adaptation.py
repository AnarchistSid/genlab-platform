"""Gaming platform adaptation — shared BasePlatformAdaptationStrategy subclass.

Migrated from AdaptGamingContent (289 lines) to the shared path (Sprint 69).
The base class now handles all 8 platform rules. This subclass adds
gaming-specific extras (safe-zone flag for 9:16 video).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genlab_core.strategies import BasePlatformAdaptationStrategy

_NICHE_ROOT = Path(__file__).resolve().parent.parent


class GamingPlatformAdaptationStrategy(BasePlatformAdaptationStrategy):
    """Adapt gaming content with platform rules + gaming-specific extras."""

    def __init__(self) -> None:
        super().__init__(niche_id="gaming", niche_root=_NICHE_ROOT)

    def _adapt_story(self, content: dict[str, Any], story: dict[str, Any]) -> None:
        """Apply all platform rules + gaming safe-zone flag."""
        super()._adapt_story(content, story)

        # Gaming-specific: set safe-zone flag for 9:16 video crop
        tw = content.get("x_twitter", {})
        media = story.get("media", {})
        aspect = media.get("aspect_ratio", "")
        if aspect in ("9:16", "portrait"):
            tw["safe_zone_enforced"] = True
            tw["safe_zone_padding_pct"] = 0.08
