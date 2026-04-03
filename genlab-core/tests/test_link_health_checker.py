"""Tests for genlab-core/scripts/check_affiliate_links.py.

Covers parse_catalog_urls() — URL extraction and placeholder filtering.
No network calls are made.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the script directly (it lives in scripts/, not a package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_affiliate_links.py"

spec = importlib.util.spec_from_file_location("check_affiliate_links", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

parse_catalog_urls = _mod.parse_catalog_urls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CATALOG = {
    "niches": {
        "gaming": {
            "products": [
                {
                    "name": "PS5 Console",
                    "networks": {
                        "amazon": {
                            "url": "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21",
                            "commission_pct": 3.0,
                        },
                        "amazon_us": {
                            "url": "https://www.amazon.com/dp/B0DJHG2VVS?tag=test-tag-20",
                            "commission_pct": 3.0,
                        },
                        "earnkaro": {
                            "url": "https://example.com/affiliate/earnkaro/ps5",
                            "commission_pct": 6.5,
                        },
                    },
                }
            ]
        },
        "sports": {
            "products": [
                {
                    "name": "ESPN+ Subscription",
                    "networks": {
                        "cuelinks": {
                            "url": "https://example.com/affiliate/cuelinks/espn-plus",
                            "commission_pct": 10.0,
                        }
                    },
                },
                {
                    "name": "Nike Running Shoes",
                    "networks": {
                        "amazon": {
                            "url": "https://www.amazon.in/dp/B0GKY7N6VY?tag=test-tag-21",
                            "commission_pct": 5.0,
                        },
                        "cuelinks": {
                            "url": "https://linksredirect.com/?cid=000000&source=linkkit&url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0GKY7N6VY",
                            "commission_pct": 7.5,
                        },
                        "earnkaro": {
                            "url": "https://example.com/affiliate/earnkaro/nike-shoes",
                            "commission_pct": 9.0,
                        },
                    },
                },
            ]
        },
    }
}

# A catalog where every URL is a placeholder — expect zero results
ALL_PLACEHOLDER_CATALOG = {
    "niches": {
        "anime": {
            "products": [
                {
                    "name": "Crunchyroll Premium",
                    "networks": {
                        "cuelinks": {
                            "url": "https://example.com/affiliate/cuelinks/crunchyroll",
                            "commission_pct": 8.0,
                        },
                        "earnkaro": {
                            "url": "https://example.com/affiliate/earnkaro/crunchyroll",
                            "commission_pct": 6.0,
                        },
                    },
                }
            ]
        }
    }
}

# A catalog with no niches key
EMPTY_CATALOG: dict = {}


# ---------------------------------------------------------------------------
# Tests — parse_catalog_urls
# ---------------------------------------------------------------------------


class TestParseCatalogUrls:
    def test_extracts_real_urls_only(self) -> None:
        """Real URLs are returned; example.com placeholders are dropped."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        urls = [r["url"] for r in results]

        # Real Amazon URLs must be present
        assert "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21" in urls
        assert "https://www.amazon.com/dp/B0DJHG2VVS?tag=test-tag-20" in urls
        assert "https://www.amazon.in/dp/B0GKY7N6VY?tag=test-tag-21" in urls
        assert (
            "https://linksredirect.com/?cid=000000&source=linkkit&url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0GKY7N6VY"
            in urls
        )

    def test_skips_example_com_placeholders(self) -> None:
        """No URL containing 'example.com' should appear in results."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        urls = [r["url"] for r in results]

        for url in urls:
            assert "example.com" not in url, f"Placeholder URL leaked into results: {url}"

    def test_result_count(self) -> None:
        """Exact count of real URLs extracted from MINIMAL_CATALOG."""
        # gaming: amazon (real) + amazon_us (real) + earnkaro (placeholder) = 2 real
        # sports/espn+: cuelinks (placeholder) = 0 real
        # sports/nike: amazon (real) + cuelinks (real) + earnkaro (placeholder) = 2 real
        # Total real: 4
        results = parse_catalog_urls(MINIMAL_CATALOG)
        assert len(results) == 4

    def test_result_has_required_keys(self) -> None:
        """Every result dict must contain product, network, niche, url."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        required_keys = {"product", "network", "niche", "url"}
        for item in results:
            assert required_keys.issubset(item.keys()), (
                f"Result missing keys: {required_keys - item.keys()}"
            )

    def test_niche_field_correct(self) -> None:
        """The niche field matches the catalog niche key."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        niches_found = {r["niche"] for r in results}
        assert "gaming" in niches_found
        assert "sports" in niches_found

    def test_product_field_correct(self) -> None:
        """The product field matches the product name in the catalog."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        products_found = {r["product"] for r in results}
        assert "PS5 Console" in products_found
        assert "Nike Running Shoes" in products_found
        # ESPN+ has only placeholder URLs so it should not appear
        assert "ESPN+ Subscription" not in products_found

    def test_all_placeholders_returns_empty(self) -> None:
        """A catalog where every URL is example.com returns an empty list."""
        results = parse_catalog_urls(ALL_PLACEHOLDER_CATALOG)
        assert results == []

    def test_empty_catalog_returns_empty(self) -> None:
        """A catalog with no niches key returns an empty list."""
        results = parse_catalog_urls(EMPTY_CATALOG)
        assert results == []

    def test_network_field_correct(self) -> None:
        """The network field matches the network key in the catalog."""
        results = parse_catalog_urls(MINIMAL_CATALOG)
        ps5_results = [r for r in results if r["product"] == "PS5 Console"]
        networks = {r["network"] for r in ps5_results}
        assert networks == {"amazon", "amazon_us"}  # earnkaro is a placeholder

    def test_real_catalog_skips_placeholders(self) -> None:
        """Integration smoke: parse the real affiliate_catalog.yaml and verify no
        example.com URLs are included."""
        import yaml

        catalog_path = Path(__file__).parent.parent / "config" / "affiliate_catalog.yaml"
        with catalog_path.open("r", encoding="utf-8") as fh:
            real_catalog = yaml.safe_load(fh)

        results = parse_catalog_urls(real_catalog)

        # Must have found some real URLs
        assert len(results) > 0, "Expected at least one real URL in the catalog"

        # None must contain example.com
        for item in results:
            assert "example.com" not in item["url"], (
                f"Placeholder leaked from real catalog: {item['url']}"
            )
