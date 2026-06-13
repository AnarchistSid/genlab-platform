"""Pins for #34c — Impact.com per-advertiser deep-link generator.

Key invariants:
  1. Empty advertisers config (the default state — no partnerships yet)
     produces "" for every advertiser_key lookup. No crash, no warning
     spam.
  2. ``IMPACT_ACCOUNT_SID`` env var is required — missing → "".
  3. Unknown advertiser_key → "" (graceful skip, matcher tries next).
  4. Known advertiser_key → templated URL with all 4 substitutions
     correct (subdomain, account_sid, advertiser_id, campaign_id) and
     query params URL-encoded.
  5. Per-row validation: missing required fields → row skipped with
     warning, other rows still load.
  6. Module cache invalidates when the resolved config path changes
     (tests can swap configs cleanly).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.monetization.impact_client import (
    _clear_cache_for_tests,
    build_deep_link,
    get_advertiser,
    load_advertisers,
)


@pytest.fixture(autouse=True)
def _isolate_module_cache():
    """Reset the impact_client module cache between tests so YAML files
    in tmp_path actually get re-read."""
    _clear_cache_for_tests()
    yield
    _clear_cache_for_tests()


@pytest.fixture
def empty_config(tmp_path: Path, monkeypatch) -> Path:
    """A config file that matches the shipped default — no advertisers."""
    cfg = tmp_path / "impact_advertisers.yaml"
    cfg.write_text("advertisers: {}\n")
    monkeypatch.setenv("IMPACT_ADVERTISERS_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def populated_config(tmp_path: Path, monkeypatch) -> Path:
    """A realistic config with two advertisers."""
    cfg = tmp_path / "impact_advertisers.yaml"
    cfg.write_text(
        "advertisers:\n"
        "  acme_corp:\n"
        "    subdomain: acme.sjv.io\n"
        '    advertiser_id: "12345"\n'
        '    campaign_id: "67890"\n'
        "  brand_b:\n"
        "    subdomain: brand-b.7eer.net\n"
        '    advertiser_id: "98765"\n'
        '    campaign_id: "54321"\n'
    )
    monkeypatch.setenv("IMPACT_ADVERTISERS_CONFIG", str(cfg))
    return cfg


class TestLoadAdvertisers:
    def test_empty_advertisers_block_returns_empty_dict(self, empty_config):
        assert load_advertisers() == {}

    def test_missing_config_file_returns_empty_dict(self, monkeypatch, tmp_path):
        """Operator hasn't created the file yet — that's fine, default state."""
        monkeypatch.setenv("IMPACT_ADVERTISERS_CONFIG", str(tmp_path / "does_not_exist.yaml"))
        assert load_advertisers() == {}

    def test_populated_config_loads_all_rows(self, populated_config):
        adv = load_advertisers()
        assert set(adv.keys()) == {"acme_corp", "brand_b"}
        assert adv["acme_corp"].subdomain == "acme.sjv.io"
        assert adv["acme_corp"].advertiser_id == "12345"
        assert adv["acme_corp"].campaign_id == "67890"

    def test_invalid_row_skipped_others_kept(self, tmp_path: Path, monkeypatch):
        """One broken row (missing field) doesn't disable the whole network."""
        cfg = tmp_path / "impact_advertisers.yaml"
        cfg.write_text(
            "advertisers:\n"
            "  good_one:\n"
            "    subdomain: good.sjv.io\n"
            '    advertiser_id: "111"\n'
            '    campaign_id: "222"\n'
            "  bad_missing_campaign:\n"
            "    subdomain: bad.sjv.io\n"
            '    advertiser_id: "333"\n'
            "    # missing campaign_id\n"
            "  bad_not_a_mapping:\n"
            "    - this is a list, not a dict\n"
        )
        monkeypatch.setenv("IMPACT_ADVERTISERS_CONFIG", str(cfg))
        adv = load_advertisers()
        assert "good_one" in adv
        assert "bad_missing_campaign" not in adv
        assert "bad_not_a_mapping" not in adv

    def test_malformed_yaml_returns_empty_no_raise(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "impact_advertisers.yaml"
        cfg.write_text(":::: not valid yaml at all\n  - [\n")
        monkeypatch.setenv("IMPACT_ADVERTISERS_CONFIG", str(cfg))
        # Must not raise
        assert load_advertisers() == {}


class TestGetAdvertiser:
    def test_returns_advertiser_for_known_key(self, populated_config):
        adv = get_advertiser("acme_corp")
        assert adv is not None
        assert adv.subdomain == "acme.sjv.io"

    def test_returns_none_for_unknown_key(self, populated_config):
        assert get_advertiser("never_partnered") is None

    def test_returns_none_for_empty_key(self, empty_config):
        assert get_advertiser("") is None


class TestBuildDeepLink:
    def test_builds_full_url_with_known_advertiser(self, populated_config, monkeypatch):
        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        url = build_deep_link(
            advertiser_key="acme_corp",
            target_url="https://acme.example.com/widget",
            sub_id="gaming_abc12345",
        )
        # Base path is exactly subdomain + account_sid + advertiser + campaign
        assert url.startswith("https://acme.sjv.io/c/ACC999/12345/67890?")
        # Target URL must be URL-encoded
        assert "u=https%3A%2F%2Facme.example.com%2Fwidget" in url
        # sub_id rides as subId1 (Impact's convention)
        assert "subId1=gaming_abc12345" in url

    def test_skips_when_account_sid_missing(self, populated_config, monkeypatch):
        monkeypatch.delenv("IMPACT_ACCOUNT_SID", raising=False)
        assert (
            build_deep_link(
                advertiser_key="acme_corp",
                target_url="https://acme.example.com/widget",
            )
            == ""
        )

    def test_skips_when_target_url_missing(self, populated_config, monkeypatch):
        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        assert (
            build_deep_link(
                advertiser_key="acme_corp",
                target_url="",
            )
            == ""
        )

    def test_skips_for_unknown_advertiser(self, populated_config, monkeypatch):
        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        assert (
            build_deep_link(
                advertiser_key="never_onboarded",
                target_url="https://merch.example.com/w",
            )
            == ""
        )

    def test_account_sid_kwarg_overrides_env(self, populated_config, monkeypatch):
        """For tests / per-niche SID overrides — kwarg wins over env."""
        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "FROM_ENV")
        url = build_deep_link(
            advertiser_key="acme_corp",
            target_url="https://merch.example.com/w",
            account_sid="FROM_KWARG",
        )
        assert "/c/FROM_KWARG/" in url

    def test_sub_id_omitted_when_empty(self, populated_config, monkeypatch):
        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        url = build_deep_link(
            advertiser_key="acme_corp",
            target_url="https://merch.example.com/w",
            sub_id="",
        )
        # subId1 param should NOT appear at all when sub_id is empty
        assert "subId1" not in url
        # u= still present
        assert "u=" in url


class TestAdapterIntegration:
    def test_adapter_returns_url_with_full_kwargs(self, populated_config, monkeypatch):
        from genlab_core.monetization.network_registry import ImpactAdapter

        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        adapter = ImpactAdapter()
        url = adapter.generate_url(
            "any_product_id",
            advertiser_key="acme_corp",
            target_url="https://merch.example.com/w",
            sub_id="movies_xyz",
        )
        assert url.startswith("https://acme.sjv.io/c/ACC999/12345/67890?")

    def test_adapter_skips_when_advertiser_key_missing(self, populated_config, monkeypatch):
        """Even with account_sid set, no advertiser_key → ""."""
        from genlab_core.monetization.network_registry import ImpactAdapter

        monkeypatch.setenv("IMPACT_ACCOUNT_SID", "ACC999")
        assert ImpactAdapter().generate_url("p", target_url="https://merch.example.com/w") == ""

    def test_adapter_validate_url_recognizes_7eer_subdomain(self):
        """7eer.net is one of Impact's other tracking subdomains;
        validate_url must recognize it alongside sjv.io and impact.com."""
        from genlab_core.monetization.network_registry import ImpactAdapter

        adapter = ImpactAdapter()
        assert adapter.validate_url("https://brand-b.7eer.net/c/1/2/3")
        assert adapter.validate_url("https://acme.sjv.io/c/1/2/3")
