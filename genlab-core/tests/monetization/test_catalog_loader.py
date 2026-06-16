"""Tests for the shared affiliate-catalog loader.

This module is the canonical replacement for two previously-divergent
``_load_catalog`` implementations (affiliate_matcher + dashboard's
links). The pins here lock down the contract; the per-caller wrappers
have their own thin pins that verify the delegation works end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from genlab_core.monetization.catalog_loader import (
    expand_env_vars,
    load_catalog,
)


class TestExpandEnvVars:
    def test_expands_amazon_us_tag(self):
        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "Gaming Mouse",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/B0XXX?tag=${TEST_TAG_2026}",
                                }
                            },
                        }
                    ]
                }
            }
        }
        with patch.dict(os.environ, {"TEST_TAG_2026": "myaffiliate-20"}):
            result = expand_env_vars(catalog)
        url = result["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
        assert url == "https://amazon.com/dp/B0XXX?tag=myaffiliate-20"
        assert "${" not in url

    def test_missing_env_var_leaves_placeholder_intact(self):
        """Graceful degradation: an operator with a broken env still
        sees the placeholder string in affiliate_clicks rather than
        a 500 on every click. The placeholder is then easy to spot
        in monitoring."""
        catalog = {
            "niches": {
                "anime": {
                    "products": [
                        {
                            "name": "X",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/Z?tag=${MISSING_VAR_99999}",
                                }
                            },
                        }
                    ]
                }
            }
        }
        os.environ.pop("MISSING_VAR_99999", None)
        result = expand_env_vars(catalog)
        url = result["niches"]["anime"]["products"][0]["networks"]["amazon_us"]["url"]
        assert "${MISSING_VAR_99999}" in url

    def test_url_without_placeholder_unchanged(self):
        """No ${...} → no expansion attempted (perf micro-opt + no
        unwanted mutation)."""
        catalog = {
            "niches": {
                "movies": {
                    "products": [
                        {
                            "name": "Y",
                            "networks": {
                                "earnkaro": {
                                    "url": "https://earnkaro.com/api?deal=raw",
                                }
                            },
                        }
                    ]
                }
            }
        }
        result = expand_env_vars(catalog)
        url = result["niches"]["movies"]["products"][0]["networks"]["earnkaro"]["url"]
        assert url == "https://earnkaro.com/api?deal=raw"

    def test_empty_catalog(self):
        assert expand_env_vars({}) == {}
        assert expand_env_vars({"niches": {}}) == {"niches": {}}
        assert expand_env_vars({"niches": None}) == {"niches": None}

    def test_product_with_no_networks(self):
        """Edge case from prod YAML: TODO/placeholder products may have
        no networks. Must not crash."""
        catalog = {
            "niches": {
                "sports": {
                    "products": [
                        {"name": "TODO", "networks": None},
                        {"name": "TODO2"},
                    ]
                }
            }
        }
        result = expand_env_vars(catalog)
        assert len(result["niches"]["sports"]["products"]) == 2

    def test_mutates_in_place(self):
        """Contract: expand_env_vars mutates AND returns. Pin both
        so callers depending on either get a stable result."""
        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "X",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://x.com/?tag=${TEST_INPLACE}",
                                }
                            },
                        }
                    ]
                }
            }
        }
        with patch.dict(os.environ, {"TEST_INPLACE": "abc"}):
            returned = expand_env_vars(catalog)
        # Same object (mutation), AND expansion applied
        assert returned is catalog
        assert (
            catalog["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
            == "https://x.com/?tag=abc"
        )

    def test_non_url_fields_untouched(self):
        catalog = {
            "niches": {
                "ai_creators": {
                    "products": [
                        {
                            "name": "Coding Keyboard",
                            "category": "hardware",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/X?tag=${TAG}",
                                    "commission_pct": 4.0,
                                    "currency": "USD",
                                }
                            },
                        }
                    ]
                }
            }
        }
        with patch.dict(os.environ, {"TAG": "test-20"}):
            result = expand_env_vars(catalog)
        product = result["niches"]["ai_creators"]["products"][0]
        assert product["name"] == "Coding Keyboard"
        assert product["category"] == "hardware"
        net = product["networks"]["amazon_us"]
        assert net["commission_pct"] == 4.0
        assert net["currency"] == "USD"
        assert net["url"] == "https://amazon.com/dp/X?tag=test-20"


class TestLoadCatalog:
    def test_reads_yaml_and_expands(self, tmp_path):
        yaml_text = """
niches:
  gaming:
    products:
      - name: Gaming Mouse
        networks:
          amazon_us:
            url: "https://amazon.com/dp/X?tag=${TEST_LOAD_TAG}"
            commission_pct: 4.0
"""
        cat_file = tmp_path / "affiliate_catalog.yaml"
        cat_file.write_text(yaml_text)

        with patch.dict(os.environ, {"TEST_LOAD_TAG": "myaff-20"}):
            loaded = load_catalog(cat_file)

        url = loaded["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
        assert "tag=myaff-20" in url
        assert "${" not in url

    def test_handles_empty_yaml(self, tmp_path):
        cat_file = tmp_path / "empty.yaml"
        cat_file.write_text("")
        loaded = load_catalog(cat_file)
        assert loaded == {}

    def test_missing_file_raises(self, tmp_path):
        """Callers handle FileNotFoundError per their own policy
        (dashboard logs WARN + serves stale; matcher returns empty).
        The loader propagates; this pin documents that contract."""
        import pytest

        with pytest.raises(FileNotFoundError):
            load_catalog(tmp_path / "does_not_exist.yaml")

    def test_malformed_yaml_raises(self, tmp_path):
        """Same propagation policy as missing-file."""
        import pytest
        import yaml

        cat_file = tmp_path / "malformed.yaml"
        cat_file.write_text("niches:\n  gaming\n      - invalid: ::")
        with pytest.raises(yaml.YAMLError):
            load_catalog(cat_file)

    def test_real_catalog_loads(self):
        """Smoke pin: the actual repo catalog YAML loads + expands
        without crashing. Catches schema drift between the
        canonical YAML and the loader assumptions."""
        # Find the canonical catalog relative to this test file
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        catalog_path = repo_root / "genlab-core" / "config" / "affiliate_catalog.yaml"
        if not catalog_path.exists():
            import pytest

            pytest.skip(f"Canonical catalog not at {catalog_path}")
        loaded = load_catalog(catalog_path)
        # Pin the shape we depend on downstream
        assert "niches" in loaded
        assert len(loaded["niches"]) >= 1
        # Every product with networks must have non-${...} URLs after expansion
        # (only if the env var is set — when running on CI without
        # AMAZON_US_AFFILIATE_TAG, the placeholder survives, which is
        # CORRECT behavior. So we only check the expansion happened
        # FOR URLs whose vars are actually set.)
        for niche_data in loaded["niches"].values():
            for product in niche_data.get("products") or []:
                for _net, info in (product.get("networks") or {}).items():
                    url = info.get("url", "")
                    # If the URL has a placeholder AND that var IS set,
                    # the placeholder MUST be gone.
                    if "${" in url:
                        # Extract the var name and see if it's actually set
                        import re

                        for var_match in re.findall(r"\$\{([^}]+)\}", url):
                            if os.environ.get(var_match):
                                # Var is set but placeholder survived — bug
                                raise AssertionError(
                                    f"URL {url!r} has ${{{var_match}}} placeholder but "
                                    f"env var {var_match!r} IS set; expansion failed."
                                )
