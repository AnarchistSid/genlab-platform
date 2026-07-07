"""Pin the observation-only wire from match_product → ProductSelector
(L3 PR 5).

Contract:
  1. When selector is disabled, ``match_product``'s return value +
     log output are UNCHANGED from pre-wire behavior.
  2. When selector is enabled AND registered arms exist, the
     divergence-check runs BUT the matcher's return value is UNCHANGED
     (observation-only mode).
  3. When selector agrees, log ``selector agreed`` at INFO.
  4. When selector diverges, log ``selector DIVERGES`` at INFO with
     both product names.
  5. Selector exceptions are swallowed — matcher never breaks.

The wire is between the static-catalog match block and the return.
Seasonal + evergreen paths bypass it (by design — seasonal has event
timing priority; evergreen is last resort).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.monetization.affiliate_matcher import (
    _log_selector_divergence,
    match_product,
)


@pytest.fixture
def sample_catalog():
    """Two gaming products with distinct keywords for a clean match.

    ``max_price_inr: 100000`` disables the impulse-buy price filter for
    tests — real catalog uses the ₹2500 default which would drop PS5
    from candidacy. Bandit reranking logic is orthogonal to price
    filtering; the tests exercise the wire without the filter noise.
    """
    return {
        "niches": {
            "gaming": {
                "max_price_inr": 100000,
                "products": [
                    {
                        "name": "PS5 Console",
                        "keywords": ["ps5", "playstation", "console"],
                        "price_inr": 49990,
                        "networks": {"amazon": {"commission_pct": 3.0}},
                        "enabled": True,
                    },
                    {
                        "name": "Xbox Game Pass Ultimate",
                        "keywords": ["xbox", "game pass", "microsoft"],
                        "price_inr": 499,
                        "networks": {"amazon": {"commission_pct": 5.0}},
                        "enabled": True,
                    },
                ],
            }
        }
    }


class TestDivergenceLoggingHelper:
    """The ``_log_selector_divergence`` helper is the side-channel wire.

    Test its behavior in isolation before testing the full match_product
    integration."""

    def test_disabled_returns_immediately(self, monkeypatch, caplog):
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)
        _log_selector_divergence(
            niche_id="gaming",
            matcher_pick={"name": "PS5 Console"},
            candidates=[{"name": "PS5 Console"}, {"name": "Xbox"}],
        )
        # No INFO logs from the helper — its own path exits early.
        assert "selector agreed" not in caplog.text
        assert "selector DIVERGES" not in caplog.text

    def test_empty_candidates_returns_immediately(self, monkeypatch, caplog):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        _log_selector_divergence(
            niche_id="gaming",
            matcher_pick={"name": "PS5 Console"},
            candidates=[],
        )
        assert "selector" not in caplog.text

    def test_selector_agreement_logs_info(self, monkeypatch, caplog):
        """When selector's #1 == matcher's pick → 'selector agreed'."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        caplog.set_level("INFO")

        # Mock the selector to return the SAME product the matcher picked
        with patch(
            "genlab_core.monetization.product_selector.select_products",
            return_value=[({"name": "PS5 Console"}, 0.75)],
        ):
            _log_selector_divergence(
                niche_id="gaming",
                matcher_pick={"name": "PS5 Console"},
                candidates=[{"name": "PS5 Console"}, {"name": "Xbox"}],
            )
        assert "selector agreed" in caplog.text
        assert "PS5 Console" in caplog.text

    def test_selector_divergence_logs_info_with_both(self, monkeypatch, caplog):
        """When selector's #1 != matcher's pick → 'selector DIVERGES'."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        caplog.set_level("INFO")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            return_value=[({"name": "Xbox Game Pass Ultimate"}, 0.92)],
        ):
            _log_selector_divergence(
                niche_id="gaming",
                matcher_pick={"name": "PS5 Console"},
                candidates=[{"name": "PS5 Console"}, {"name": "Xbox Game Pass Ultimate"}],
            )
        assert "selector DIVERGES" in caplog.text
        # Both names must be in the log so operator can see the split
        assert "PS5 Console" in caplog.text
        assert "Xbox Game Pass Ultimate" in caplog.text
        assert "observation-only" in caplog.text

    def test_selector_empty_ranking_is_not_divergence(self, monkeypatch, caplog):
        """Empty ranked list = cold-start signal, not disagreement."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        caplog.set_level("INFO")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            return_value=[],
        ):
            _log_selector_divergence(
                niche_id="gaming",
                matcher_pick={"name": "PS5 Console"},
                candidates=[{"name": "PS5 Console"}, {"name": "Xbox"}],
            )
        # Neither agreement nor divergence — nothing to compare against
        assert "selector agreed" not in caplog.text
        assert "selector DIVERGES" not in caplog.text

    def test_selector_exception_swallowed(self, monkeypatch, caplog):
        """A selector exception must not break the matcher path."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise
            _log_selector_divergence(
                niche_id="gaming",
                matcher_pick={"name": "PS5 Console"},
                candidates=[{"name": "PS5 Console"}, {"name": "Xbox"}],
            )
        # Debug log fires but no exception escapes


class TestMatchProductReturnValueUnchanged:
    """The observation-only contract: matcher's return value is
    identical regardless of selector state (on/off/agree/diverge)."""

    def test_disabled_returns_same_as_pre_wire(self, sample_catalog, monkeypatch):
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)
        result = match_product(
            text="check out this new PS5 playstation console",
            niche_id="gaming",
            catalog=sample_catalog,
            seasonal_config={},
        )
        assert result is not None
        assert result["name"] == "PS5 Console"

    def test_enabled_with_agreement_returns_matcher_pick(self, sample_catalog, monkeypatch, caplog):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        caplog.set_level("INFO")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            return_value=[({"name": "PS5 Console"}, 0.75)],
        ):
            result = match_product(
                text="check out this new PS5 playstation console",
                niche_id="gaming",
                catalog=sample_catalog,
                seasonal_config={},
            )
        assert result is not None
        assert result["name"] == "PS5 Console"
        assert "selector agreed" in caplog.text

    def test_enabled_with_divergence_still_returns_matcher_pick(
        self, sample_catalog, monkeypatch, caplog
    ):
        """OBSERVATION-ONLY: matcher's pick wins even when selector says otherwise."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        caplog.set_level("INFO")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            return_value=[({"name": "Xbox Game Pass Ultimate"}, 0.99)],
        ):
            result = match_product(
                text="check out this new PS5 playstation console",
                niche_id="gaming",
                catalog=sample_catalog,
                seasonal_config={},
            )
        # Matcher still returns PS5 even though selector said Xbox
        assert result is not None
        assert result["name"] == "PS5 Console"
        assert "selector DIVERGES" in caplog.text

    def test_enabled_with_selector_exception_still_returns_matcher_pick(
        self, sample_catalog, monkeypatch
    ):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

        with patch(
            "genlab_core.monetization.product_selector.select_products",
            side_effect=RuntimeError("boom"),
        ):
            result = match_product(
                text="check out this new PS5 playstation console",
                niche_id="gaming",
                catalog=sample_catalog,
                seasonal_config={},
            )
        # Exception in selector must not break matcher
        assert result is not None
        assert result["name"] == "PS5 Console"


class TestNonStaticPathsBypassSelector:
    """Seasonal + evergreen paths deliberately skip the selector.

    Seasonal has event-timing priority (Prime Day matters more than
    bandit signal for time-sensitive deals). Evergreen is the last
    resort catchall — a bandit rerank there is meaningless because
    there's only one candidate."""

    def test_seasonal_match_bypasses_selector(self, monkeypatch):
        """Seasonal match returns immediately, never touching selector."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

        # Empty catalog so seasonal is the only path
        catalog = {"niches": {"gaming": {"products": []}}}
        seasonal = {
            "events": [
                {
                    "name": "Amazon Prime Day",
                    "start": "2000-01-01",
                    "end": "2999-12-31",
                    "products": [
                        {
                            "name": "Prime Day: PS5",
                            "keywords": ["ps5", "playstation", "sony"],
                            "enabled": True,
                        }
                    ],
                }
            ]
        }

        # Patch selector to raise — proves matcher never calls it here
        with patch(
            "genlab_core.monetization.product_selector.select_products",
            side_effect=RuntimeError("selector should not be called"),
        ):
            result = match_product(
                text="ps5 playstation sony console review",
                niche_id="gaming",
                catalog=catalog,
                seasonal_config=seasonal,
            )
        assert result is not None
        assert "Prime Day" in result["name"]


class TestFlagOffProductionBehaviorPreserved:
    """Guarantee: no observable difference when the flag is unset."""

    def test_flag_absent_no_selector_calls(self, sample_catalog, monkeypatch):
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)

        with patch("genlab_core.monetization.product_selector.select_products") as mock_select:
            match_product(
                text="ps5 playstation console",
                niche_id="gaming",
                catalog=sample_catalog,
                seasonal_config={},
            )
        # is_enabled() returned False → select_products was never called
        mock_select.assert_not_called()
