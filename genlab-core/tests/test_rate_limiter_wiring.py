"""Tests that content research strategies have API rate limiters wired.

After Wave 2 refactor, the shared _api_limiter lives in the base class module
(genlab_core.strategies.base_content_research) and is used by all niche
strategies via inheritance.  CW, SR, FD strategies inherit from
BaseContentResearchStrategy which calls _api_limiter.acquire() in execute().
"""

import importlib


class TestRateLimiterWiring:
    """The shared _api_limiter should be a TokenBucket in the base module."""

    def test_base_content_research_has_rate_limiter(self):
        mod = importlib.import_module("genlab_core.strategies.base_content_research")
        assert hasattr(mod, "_api_limiter"), (
            "base_content_research missing _api_limiter TokenBucket"
        )
        from genlab_core.ratelimit.token_bucket import TokenBucket

        assert isinstance(mod._api_limiter, TokenBucket)

    def test_clutchwire_inherits_base(self):
        from genlab_core.strategies.base_content_research import (
            BaseContentResearchStrategy,
        )

        mod = importlib.import_module("cw_strategies.content_research")
        cls = mod.SportContentResearchStrategy
        assert issubclass(cls, BaseContentResearchStrategy)

    def test_splicereel_inherits_base(self):
        from genlab_core.strategies.base_content_research import (
            BaseContentResearchStrategy,
        )

        mod = importlib.import_module("sr_strategies.content_research")
        cls = mod.MovieContentResearchStrategy
        assert issubclass(cls, BaseContentResearchStrategy)

    def test_framedrift_inherits_base(self):
        from genlab_core.strategies.base_content_research import (
            BaseContentResearchStrategy,
        )

        mod = importlib.import_module("fd_strategies.content_research")
        cls = mod.AnimeContentResearchStrategy
        assert issubclass(cls, BaseContentResearchStrategy)

    def test_rate_limiter_has_expected_rate(self):
        """The shared limiter should use 5 req/s rate."""
        from genlab_core.strategies.base_content_research import (
            _api_limiter,
        )

        assert _api_limiter.rate == 5.0
