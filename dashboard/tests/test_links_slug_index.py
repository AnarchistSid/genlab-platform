"""Regression tests for the /links/go/<slug> slug-index perf fix (PR #390).

Pins the contract that ``_load_catalog()`` rebuilds the
``_product_slug_index`` hash and ``_find_product_globally()`` uses it
for the hot redirect path.

The pre-fix code did an O(niches × products) linear scan + per-product
slugify on every click; the index lifts that to a single dict lookup.
If a future refactor accidentally:
  * drops the index rebuild in _load_catalog → the test that puts a
    product in the cached catalog + queries it would fail
  * drops the cached-catalog fast-path branch in _find_product_globally
    → behavior still correct (slow-path fallback) but tests still pass
  * lets the index drift from the catalog (e.g. cache update without
    rebuild) → the drift test catches it
"""

from __future__ import annotations

import importlib
import time
from unittest.mock import patch


def _reload_links_module():
    """Reload to reset module-level caches between tests."""
    from server.api import links

    importlib.reload(links)
    return links


def test_slug_index_built_on_catalog_load():
    """After _load_catalog() the slug index has every enabled product."""
    links = _reload_links_module()

    fake_catalog = {
        "niches": {
            "movies": {
                "products": [
                    {"name": "Test Product", "enabled": True, "networks": {}},
                    {"name": "Disabled Product", "enabled": False, "networks": {}},
                    {"name": "Another Real Product", "enabled": True, "networks": {}},
                ]
            },
            "gaming": {
                "products": [
                    {"name": "Gaming Mouse", "enabled": True, "networks": {}},
                ]
            },
        }
    }

    with patch("genlab_core.monetization.catalog_loader.load_catalog", return_value=fake_catalog):
        links._catalog_cache = {}
        links._catalog_cache_ts = 0.0
        links._product_slug_index = {}
        links._load_catalog()

    # Index should contain only ENABLED products
    assert "test-product" in links._product_slug_index
    assert "another-real-product" in links._product_slug_index
    assert "gaming-mouse" in links._product_slug_index
    assert "disabled-product" not in links._product_slug_index
    assert len(links._product_slug_index) == 3

    # Each index entry maps slug → (niche_id, product)
    assert links._product_slug_index["test-product"][0] == "movies"
    assert links._product_slug_index["gaming-mouse"][0] == "gaming"


def test_find_product_globally_uses_index_when_catalog_is_cached():
    """Production hot path: caller passes the cached catalog → O(1) lookup."""
    links = _reload_links_module()

    catalog = {
        "niches": {
            "movies": {
                "products": [
                    {
                        "name": "Indexed Product",
                        "enabled": True,
                        "networks": {
                            "amazon_us": {"url": "https://amazon.com/dp/X", "commission_pct": 4.0},
                        },
                    }
                ]
            }
        }
    }
    with patch("genlab_core.monetization.catalog_loader.load_catalog", return_value=catalog):
        links._catalog_cache = {}
        links._catalog_cache_ts = 0.0
        links._product_slug_index = {}
        cached = links._load_catalog()

    # Pass the CACHED catalog (production path)
    result = links._find_product_globally(cached, "indexed-product", country="")
    assert result is not None
    niche_id, network_name, product = result
    assert niche_id == "movies"
    assert network_name == "amazon_us"
    assert product["name"] == "Indexed Product"


def test_find_product_globally_falls_back_to_linear_scan_for_uncached_catalog():
    """Test fixtures pass hand-built catalogs that aren't the cached one —
    those should still work via the slow-path fallback, preserving
    legacy test contracts.
    """
    links = _reload_links_module()
    # Don't prime the index — pass a fresh dict that isn't _catalog_cache
    fresh_catalog = {
        "niches": {
            "anime": {
                "products": [
                    {
                        "name": "Hand Built Product",
                        "enabled": True,
                        "networks": {
                            "amazon_us": {"url": "https://amazon.com/dp/Y", "commission_pct": 4.0},
                        },
                    }
                ]
            }
        }
    }
    result = links._find_product_globally(fresh_catalog, "hand-built-product", country="")
    assert result is not None
    niche_id, network_name, _ = result
    assert niche_id == "anime"
    assert network_name == "amazon_us"


def test_duplicate_slug_across_niches_logs_warning_and_last_wins(caplog):
    """Operator should be told if two niches expose the same slug —
    second-wins behavior is documented but loud.
    """
    links = _reload_links_module()
    fake_catalog = {
        "niches": {
            "movies": {"products": [{"name": "Same Slug", "enabled": True, "networks": {}}]},
            "anime": {"products": [{"name": "Same Slug", "enabled": True, "networks": {}}]},
        }
    }
    with patch("genlab_core.monetization.catalog_loader.load_catalog", return_value=fake_catalog):
        links._catalog_cache = {}
        links._catalog_cache_ts = 0.0
        links._product_slug_index = {}
        with caplog.at_level("WARNING"):
            links._load_catalog()

    # Both products produce the same slug; index has one entry, log records the collision
    assert "same-slug" in links._product_slug_index
    # Operator-facing warning logged
    assert any("Duplicate product slug" in r.message for r in caplog.records)


def test_index_rebuilt_after_cache_expiry():
    """When the TTL expires and catalog is re-loaded, the index is rebuilt
    fresh — never goes stale relative to the catalog it indexes.
    """
    links = _reload_links_module()
    v1 = {
        "niches": {
            "movies": {"products": [{"name": "Old Product", "enabled": True, "networks": {}}]}
        }
    }
    v2 = {
        "niches": {
            "movies": {"products": [{"name": "New Product", "enabled": True, "networks": {}}]}
        }
    }

    with patch("genlab_core.monetization.catalog_loader.load_catalog", return_value=v1):
        links._catalog_cache = {}
        links._catalog_cache_ts = 0.0
        links._product_slug_index = {}
        links._load_catalog()
        assert "old-product" in links._product_slug_index
        assert "new-product" not in links._product_slug_index

    # Force cache expiry
    links._catalog_cache_ts = time.time() - links._CATALOG_TTL - 1
    with patch("genlab_core.monetization.catalog_loader.load_catalog", return_value=v2):
        links._load_catalog()

    assert "new-product" in links._product_slug_index
    assert "old-product" not in links._product_slug_index, "stale index entries"
