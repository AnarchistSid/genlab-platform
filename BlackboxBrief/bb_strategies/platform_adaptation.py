"""BB platform adaptation strategy.

Migrated to BasePlatformAdaptationStrategy (Sprint 69). Inherits full
8-rule platform enforcement for all 6 platforms.

Previous version (61 lines) shelled out to adapt_for_platforms.py via
subprocess, which is preserved at execution/adapt_for_platforms.py
but no longer called from the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from genlab_core.strategies import BasePlatformAdaptationStrategy

BB_ROOT = Path(__file__).resolve().parent.parent


class BBPlatformAdaptationStrategy(BasePlatformAdaptationStrategy):
    """Adapt AI creator content for platform-specific requirements."""

    def __init__(self) -> None:
        super().__init__(niche_id="ai_creators", niche_root=BB_ROOT)
