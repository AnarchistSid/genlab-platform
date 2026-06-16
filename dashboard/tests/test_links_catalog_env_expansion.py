"""Test that the affiliate catalog loader expands ${ENV_VAR} placeholders.

Regression pin for the 2026-06-17 bug where dashboard's `_load_catalog`
left ``${AMAZON_US_AFFILIATE_TAG}`` unsubstituted in affiliate URLs.
The literal string flowed all the way to Amazon, which rejected it as
an unknown tag → 135 clicks landed in `affiliate_clicks` with broken
URLs and earned **zero** commission attribution.

This file pins the contract: after loading the catalog, all
``${VAR}`` placeholders MUST be expanded against ``os.environ``.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch


def _reload_links_module():
    """Reload the module so the in-memory catalog cache resets between tests."""
    from server.api import links

    importlib.reload(links)
    return links


class TestExpandCatalogEnvVars:
    def test_expands_amazon_us_tag(self):
        links = _reload_links_module()
        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "Gaming Mouse",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/B086PJKVVT?tag=${TEST_AMAZON_TAG}",
                                },
                            },
                        },
                    ],
                },
            },
        }
        with patch.dict(os.environ, {"TEST_AMAZON_TAG": "myaffiliate-20"}):
            result = links._expand_catalog_env_vars(catalog)
        url = result["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
        assert url == "https://amazon.com/dp/B086PJKVVT?tag=myaffiliate-20"
        assert "${" not in url, "placeholder should be fully expanded"

    def test_handles_missing_env_var_gracefully(self):
        """If the env var is missing, expandvars leaves the literal string.
        That's the symptom we're fixing AGAINST, but the helper shouldn't
        crash — it just propagates the unexpanded URL so an operator can
        diagnose. (Better than a 500 on every click.)"""
        links = _reload_links_module()
        catalog = {
            "niches": {
                "anime": {
                    "products": [
                        {
                            "name": "X",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/Z?tag=${MISSING_VAR_XYZ_2026}",
                                },
                            },
                        },
                    ],
                },
            },
        }
        # Ensure the env var really is missing
        os.environ.pop("MISSING_VAR_XYZ_2026", None)
        result = links._expand_catalog_env_vars(catalog)
        url = result["niches"]["anime"]["products"][0]["networks"]["amazon_us"]["url"]
        # expandvars leaves missing vars unchanged
        assert "${MISSING_VAR_XYZ_2026}" in url

    def test_skips_urls_without_placeholders(self):
        """No ${...} → no expansion attempted (perf micro-optimization)."""
        links = _reload_links_module()
        catalog = {
            "niches": {
                "movies": {
                    "products": [
                        {
                            "name": "Y",
                            "networks": {
                                "earnkaro": {
                                    "url": "https://earnkaro.com/api/converter?deal=https://amazon.in/dp/X",
                                },
                            },
                        },
                    ],
                },
            },
        }
        result = links._expand_catalog_env_vars(catalog)
        url = result["niches"]["movies"]["products"][0]["networks"]["earnkaro"]["url"]
        assert url == "https://earnkaro.com/api/converter?deal=https://amazon.in/dp/X"

    def test_handles_empty_catalog(self):
        links = _reload_links_module()
        assert links._expand_catalog_env_vars({}) == {}
        assert links._expand_catalog_env_vars({"niches": {}}) == {"niches": {}}
        assert links._expand_catalog_env_vars({"niches": None}) == {"niches": None}

    def test_handles_product_without_networks(self):
        """Edge case from the prod YAML: some products may have no networks
        configured (placeholder rows). The expander must not crash."""
        links = _reload_links_module()
        catalog = {
            "niches": {
                "sports": {
                    "products": [
                        {"name": "TODO", "networks": None},
                        {"name": "TODO2"},
                    ],
                },
            },
        }
        result = links._expand_catalog_env_vars(catalog)
        # No exception; products preserved
        assert len(result["niches"]["sports"]["products"]) == 2

    def test_load_catalog_returns_expanded_urls(self, tmp_path):
        """End-to-end pin: write a YAML to disk, point _CATALOG_PATH at it,
        load via _load_catalog, verify URL is expanded.

        This is the contract that was BROKEN before 2026-06-17 — the bug
        was that _load_catalog returned URLs WITH ${VAR} intact, which
        then flowed into the /links/go redirect handler and was stored
        verbatim in the affiliate_clicks table.
        """
        catalog_yaml = """
niches:
  gaming:
    products:
      - name: Gaming Mouse
        networks:
          amazon_us:
            url: "https://amazon.com/dp/B086PJKVVT?tag=${TEST_AFFILIATE_TAG_2026}"
            commission_pct: 4.0
"""
        cat_file = tmp_path / "affiliate_catalog.yaml"
        cat_file.write_text(catalog_yaml)

        links = _reload_links_module()
        with (
            patch.object(links, "_CATALOG_PATH", cat_file),
            patch.dict(os.environ, {"TEST_AFFILIATE_TAG_2026": "myaffiliate-20"}),
        ):
            # Reset cache so we definitely re-read
            links._catalog_cache = {}
            links._catalog_cache_ts = 0.0
            loaded = links._load_catalog()

        url = loaded["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
        assert "tag=myaffiliate-20" in url
        assert "${" not in url, (
            f"Loaded catalog still has unexpanded placeholder: {url} — "
            "this is exactly the bug that caused 0 affiliate attribution."
        )

    def test_yaml_parse_safety_invariant(self):
        """A valid catalog dict shape is required by downstream code.
        Pin that the loader doesn't transform structure beyond URL expansion."""
        links = _reload_links_module()
        catalog = {
            "niches": {
                "movies": {
                    "products": [
                        {
                            "name": "Popcorn Maker",
                            "category": "kitchen",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/X?tag=${TAG}",
                                    "commission_pct": 4.0,
                                    "currency": "USD",
                                },
                            },
                        },
                    ],
                },
            },
        }
        with patch.dict(os.environ, {"TAG": "test-20"}):
            result = links._expand_catalog_env_vars(catalog)
        # Structure preserved
        product = result["niches"]["movies"]["products"][0]
        assert product["name"] == "Popcorn Maker"
        assert product["category"] == "kitchen"
        # Non-URL fields untouched
        net = product["networks"]["amazon_us"]
        assert net["commission_pct"] == 4.0
        assert net["currency"] == "USD"
        # URL expanded
        assert net["url"] == "https://amazon.com/dp/X?tag=test-20"
