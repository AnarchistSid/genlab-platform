"""Strategy interfaces and base implementations for Gen Lab niche pipelines.

Re-exports all abstract interfaces from ``interfaces`` for backward compatibility,
plus concrete base classes that implement shared logic across channels.

Usage (unchanged)::

    from genlab_core.strategies import PlatformAdaptationStrategy  # abstract
    from genlab_core.strategies import BasePlatformAdaptationStrategy  # shared impl

"""

from __future__ import annotations

# --- Abstract interfaces (backward-compatible re-exports) ------------------
from .interfaces import (
    ContentResearchStrategy,
    HookStrategy,
    PlatformAdaptationStrategy,
    PerformanceLearner,
    ScoringStrategy,
    VisualRenderStrategy,
    WritingStrategy,
)

# --- Base implementations with shared logic --------------------------------
from .base_platform_adaptation import BasePlatformAdaptationStrategy
from .base_writing import BaseWritingStrategy
from .base_hooks import BaseHookStrategy
from .base_content_research import BaseContentResearchStrategy

__all__ = [
    # Abstract interfaces
    "ContentResearchStrategy",
    "HookStrategy",
    "PlatformAdaptationStrategy",
    "PerformanceLearner",
    "ScoringStrategy",
    "VisualRenderStrategy",
    "WritingStrategy",
    # Base implementations
    "BasePlatformAdaptationStrategy",
    "BaseWritingStrategy",
    "BaseHookStrategy",
    "BaseContentResearchStrategy",
]
