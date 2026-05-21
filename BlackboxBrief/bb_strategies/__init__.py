"""Blackbox Brief (ai_creators) strategy wrappers for CriticalRush pipeline.

The pipeline runner imports each strategy class by name (e.g.
``BBHookStrategy``); without the re-exports below the runner would
fail with ImportError when resolving niche=ai_creators stages.
2026-05-21 audit found this package was a no-op while the other 3
niche packages (cw_strategies, sr_strategies, fd_strategies) all
re-exported their 6 strategy classes correctly.
"""

from .content_research import BBContentResearchStrategy
from .hooks import BBHookStrategy
from .platform_adaptation import BBPlatformAdaptationStrategy
from .scoring import BBScoringStrategy
from .visual_render import BBVisualRenderStrategy
from .writing import BBWritingStrategy

__all__ = [
    "BBContentResearchStrategy",
    "BBHookStrategy",
    "BBPlatformAdaptationStrategy",
    "BBScoringStrategy",
    "BBVisualRenderStrategy",
    "BBWritingStrategy",
]
