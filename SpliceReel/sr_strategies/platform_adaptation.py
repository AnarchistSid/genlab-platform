"""SpliceReel platform adaptation strategy.

Inherits full platform rule enforcement from BasePlatformAdaptationStrategy.
"""

from __future__ import annotations

from pathlib import Path

from genlab_core.strategies import BasePlatformAdaptationStrategy

_NICHE_ROOT = Path(__file__).resolve().parent.parent


class MoviePlatformAdaptationStrategy(BasePlatformAdaptationStrategy):
    """Adapt movie content for platform-specific requirements."""

    def __init__(self) -> None:
        super().__init__(niche_id="movies", niche_root=_NICHE_ROOT)
