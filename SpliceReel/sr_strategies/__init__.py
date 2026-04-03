"""SpliceReel (movies) strategy implementations."""

from .content_research import MovieContentResearchStrategy
from .hooks import MovieHookStrategy
from .platform_adaptation import MoviePlatformAdaptationStrategy
from .scoring import MovieScoringStrategy
from .visual_render import MovieVisualRenderStrategy
from .writing import MovieWritingStrategy

__all__ = [
    "MovieContentResearchStrategy",
    "MovieScoringStrategy",
    "MovieWritingStrategy",
    "MovieHookStrategy",
    "MovieVisualRenderStrategy",
    "MoviePlatformAdaptationStrategy",
]
