"""Pin the 2026-07-07 env var expansion on seasonal.py.

Live-fire on 2026-07-07 caught: the seasonal loader does NOT expand
``${AMAZON_*_TAG}`` env vars — unlike catalog_loader.py which was
patched for this class of bug in PR #272. If seasonal.yaml activates
without this fix, URLs ship as literals like
``https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}`` and Amazon
silently rejects the unknown tag — clicks earn zero commission.

Same failure mode as the dashboard-side loader bug that hit 135
historical clicks with $0 attribution.

If this pin regresses, activating seasonal boosts (Prime Day,
Diwali, Black Friday) would silently burn commissionable clicks.
"""

from __future__ import annotations


class TestExpandEnvVars:
    """`_expand_env_vars_in_seasonal` walks the events tree."""

    def test_amazon_tag_placeholders_get_expanded(self, monkeypatch):
        """The canonical case: seasonal URL with ``${AMAZON_US_AFFILIATE_TAG}``
        becomes ``tag=aspirehub06-20`` (or whatever env value is set)."""
        from genlab_core.monetization.seasonal import _expand_env_vars_in_seasonal

        monkeypatch.setenv("AMAZON_US_AFFILIATE_TAG", "aspirehub06-20")
        monkeypatch.setenv("AMAZON_IN_AFFILIATE_TAG", "aspirehub-21")

        config = {
            "events": [
                {
                    "name": "Prime Day",
                    "products": [
                        {
                            "name": "PS5",
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}"
                                },
                                "amazon": {
                                    "url": "https://amazon.in/dp/Y?tag=${AMAZON_IN_AFFILIATE_TAG}"
                                },
                            },
                        }
                    ],
                }
            ]
        }

        result = _expand_env_vars_in_seasonal(config)

        us_url = result["events"][0]["products"][0]["networks"]["amazon_us"]["url"]
        in_url = result["events"][0]["products"][0]["networks"]["amazon"]["url"]
        assert us_url == "https://amazon.com/dp/X?tag=aspirehub06-20"
        assert in_url == "https://amazon.in/dp/Y?tag=aspirehub-21"

    def test_missing_env_var_leaves_placeholder_intact(self, monkeypatch):
        """Graceful degradation per catalog_loader convention: missing var
        leaves the literal ``${VAR}`` so operators can spot it in the DB
        and trace back to a missing env config."""
        from genlab_core.monetization.seasonal import _expand_env_vars_in_seasonal

        monkeypatch.delenv("AMAZON_US_AFFILIATE_TAG", raising=False)

        config = {
            "events": [
                {
                    "products": [
                        {
                            "networks": {
                                "amazon_us": {
                                    "url": "https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}"
                                }
                            }
                        }
                    ]
                }
            ]
        }

        result = _expand_env_vars_in_seasonal(config)
        # Env var absent → literal preserved
        assert (
            result["events"][0]["products"][0]["networks"]["amazon_us"]["url"]
            == "https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}"
        )

    def test_empty_config_returns_unchanged(self):
        from genlab_core.monetization.seasonal import _expand_env_vars_in_seasonal

        assert _expand_env_vars_in_seasonal({}) == {}
        assert _expand_env_vars_in_seasonal({"events": []}) == {"events": []}
        assert _expand_env_vars_in_seasonal({"events": None}) == {"events": None}

    def test_product_with_no_networks_skipped(self):
        from genlab_core.monetization.seasonal import _expand_env_vars_in_seasonal

        config = {"events": [{"products": [{"name": "X"}]}]}
        # No crash, no side effects
        _expand_env_vars_in_seasonal(config)

    def test_url_without_placeholder_passes_through(self, monkeypatch):
        """Perf micro-opt: URLs without ``${`` skip expandvars."""
        from genlab_core.monetization.seasonal import _expand_env_vars_in_seasonal

        monkeypatch.setenv("AMAZON_US_AFFILIATE_TAG", "should-not-appear")
        config = {
            "events": [
                {
                    "products": [
                        {
                            "networks": {
                                "amazon_us": {"url": "https://amazon.com/dp/X?tag=hardcoded-tag"}
                            }
                        }
                    ]
                }
            ]
        }
        result = _expand_env_vars_in_seasonal(config)
        # Untouched
        assert (
            result["events"][0]["products"][0]["networks"]["amazon_us"]["url"]
            == "https://amazon.com/dp/X?tag=hardcoded-tag"
        )


class TestLoadSeasonalConfigWithExpansion:
    """`load_seasonal_config` calls `_expand_env_vars_in_seasonal` at load time."""

    def test_load_from_yaml_expands_env_vars(self, tmp_path, monkeypatch):
        from genlab_core.monetization.seasonal import load_seasonal_config

        monkeypatch.setenv("AMAZON_US_AFFILIATE_TAG", "test-tag-42")

        yaml_content = (
            "events:\n"
            "  - name: Test Event\n"
            "    start: '2026-01-01'\n"
            "    end: '2026-12-31'\n"
            "    products:\n"
            "      - name: TestProduct\n"
            "        networks:\n"
            "          amazon_us:\n"
            "            url: 'https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}'\n"
            "            commission_pct: 3.0\n"
        )
        config_path = tmp_path / "affiliate_seasonal.yaml"
        config_path.write_text(yaml_content)

        result = load_seasonal_config(config_path)
        url = result["events"][0]["products"][0]["networks"]["amazon_us"]["url"]
        assert url == "https://amazon.com/dp/X?tag=test-tag-42"

    def test_missing_file_returns_empty_dict(self, tmp_path):
        from genlab_core.monetization.seasonal import load_seasonal_config

        assert load_seasonal_config(tmp_path / "nonexistent.yaml") == {}
