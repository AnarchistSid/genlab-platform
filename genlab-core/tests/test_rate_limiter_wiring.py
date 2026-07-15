"""Tests that content research strategies have API rate limiters wired.

After Wave 2 refactor, the shared _api_limiter lives in the base class module
(genlab_core.strategies.base_content_research) and is used by all niche
strategies via inheritance.  CW, SR, FD strategies inherit from
BaseContentResearchStrategy which calls _api_limiter.acquire() in execute().

Note (2026-07-15): the CW/SR/FD inheritance tests require the respective
niche packages to be installed in the venv. ``uv sync --package genlab-core``
only installs genlab-core + its dependencies — the niche packages
(``cw_strategies``, ``sr_strategies``, ``fd_strategies``) are separate
workspace members that aren't genlab-core dependencies. Use ``uv sync
--all-packages`` if you want these cross-workspace pins to actually run;
otherwise they skip cleanly (documented via importorskip below).
"""

import importlib

import pytest


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
        pytest.importorskip(
            "cw_strategies",
            reason="Requires ClutchWire installed — run `uv sync --all-packages`",
        )
        from genlab_core.strategies.base_content_research import (
            BaseContentResearchStrategy,
        )

        mod = importlib.import_module("cw_strategies.content_research")
        cls = mod.SportContentResearchStrategy
        assert issubclass(cls, BaseContentResearchStrategy)

    def test_splicereel_inherits_base(self):
        pytest.importorskip(
            "sr_strategies",
            reason="Requires SpliceReel installed — run `uv sync --all-packages`",
        )
        from genlab_core.strategies.base_content_research import (
            BaseContentResearchStrategy,
        )

        mod = importlib.import_module("sr_strategies.content_research")
        cls = mod.MovieContentResearchStrategy
        assert issubclass(cls, BaseContentResearchStrategy)

    def test_framedrift_inherits_base(self):
        pytest.importorskip(
            "fd_strategies",
            reason="Requires FrameDrift installed — run `uv sync --all-packages`",
        )
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
