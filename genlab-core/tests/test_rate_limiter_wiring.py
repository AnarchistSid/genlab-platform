"""Tests that CW, SR, FD content research strategies have API rate limiters wired."""
import importlib


class TestRateLimiterWiring:
    """All content research strategies should have a module-level _api_limiter."""

    def test_clutchwire_has_rate_limiter(self):
        mod = importlib.import_module("cw_strategies.content_research")
        assert hasattr(mod, "_api_limiter"), (
            "ClutchWire content_research missing _api_limiter TokenBucket"
        )
        from genlab_core.ratelimit.token_bucket import TokenBucket
        assert isinstance(mod._api_limiter, TokenBucket)

    def test_splicereel_has_rate_limiter(self):
        mod = importlib.import_module("sr_strategies.content_research")
        assert hasattr(mod, "_api_limiter"), (
            "SpliceReel content_research missing _api_limiter TokenBucket"
        )
        from genlab_core.ratelimit.token_bucket import TokenBucket
        assert isinstance(mod._api_limiter, TokenBucket)

    def test_framedrift_has_rate_limiter(self):
        mod = importlib.import_module("fd_strategies.content_research")
        assert hasattr(mod, "_api_limiter"), (
            "FrameDrift content_research missing _api_limiter TokenBucket"
        )
        from genlab_core.ratelimit.token_bucket import TokenBucket
        assert isinstance(mod._api_limiter, TokenBucket)

    def test_rate_limiter_has_expected_rate(self):
        """All niche limiters should use 5 req/s rate."""
        from cw_strategies.content_research import _api_limiter as cw
        from sr_strategies.content_research import _api_limiter as sr
        from fd_strategies.content_research import _api_limiter as fd
        assert cw.rate == 5.0
        assert sr.rate == 5.0
        assert fd.rate == 5.0
