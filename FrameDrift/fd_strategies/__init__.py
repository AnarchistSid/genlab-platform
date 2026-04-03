"""FrameDrift (anime) strategy implementations."""

from .content_research import AnimeContentResearchStrategy
from .hooks import AnimeHookStrategy
from .platform_adaptation import AnimePlatformAdaptationStrategy
from .scoring import AnimeScoringStrategy
from .visual_render import AnimeVisualRenderStrategy
from .writing import AnimeWritingStrategy

__all__ = [
    "AnimeContentResearchStrategy",
    "AnimeScoringStrategy",
    "AnimeWritingStrategy",
    "AnimeHookStrategy",
    "AnimeVisualRenderStrategy",
    "AnimePlatformAdaptationStrategy",
]
