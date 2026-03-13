"""Abstract strategy interfaces for Gen Lab niche pipelines.

Every niche (ai_news, gaming, sports, anime, movies, etc.) implements these
six strategies. The pipeline runner calls them polymorphically — it doesn't
know or care which niche is active, only that the strategies conform to
these contracts.

Usage:
    from genlab_core.strategies import ContentResearchStrategy

    class GamingResearchStrategy(ContentResearchStrategy):
        def execute(self, context):
            ...  # gaming-specific research logic
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContentResearchStrategy(ABC):
    """Fetch and parse raw content from niche-specific sources.

    Responsible for: source discovery, HTTP fetching, RSS/API ingestion,
    raw content extraction, and caching. Outputs a list of story dicts
    ready for deduplication and scoring.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class ScoringStrategy(ABC):
    """Score and rank content items for a specific niche.

    Responsible for: applying niche-specific scoring weights (virality,
    recency, novelty, authority), clustering related stories, deduplication,
    and producing a ranked list of candidates.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class WritingStrategy(ABC):
    """Generate written content tailored to a niche's voice and style.

    Responsible for: LLM prompt construction, tone/voice enforcement,
    caption generation, hashtag selection, and producing platform-ready
    text content that matches the niche's audience expectations.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class HookStrategy(ABC):
    """Generate attention-grabbing hooks for content pieces.

    Responsible for: creating opening lines, scroll-stopping headlines,
    mid-content hooks, and CTAs tuned to the niche's audience psychology
    and platform conventions.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class VisualRenderStrategy(ABC):
    """Render visual assets (images, carousels, video overlays) for a niche.

    Responsible for: template selection, brand styling, layout decisions,
    text overlay rendering, and producing platform-spec-compliant visual
    assets (e.g., 1080x1350 carousel slides, 1080x1920 reel frames).
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class PlatformAdaptationStrategy(ABC):
    """Adapt content for specific publishing platforms.

    Responsible for: reformatting content for Instagram, YouTube, X/Twitter,
    etc. — adjusting length, tone, hashtags, mentions, and structure to
    match each platform's native conventions and API requirements.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...


class PerformanceLearner(ABC):
    """Observes post-publish engagement data and updates the agent's internal
    model (e.g. Thompson Sampling bandit posteriors) to improve future
    decisions.

    Runs as a separate scheduled job, not inline with the publishing
    pipeline, because platform analytics have 24-48 hour delays.
    """

    @abstractmethod
    def execute(self, context: Any) -> Any:
        ...
