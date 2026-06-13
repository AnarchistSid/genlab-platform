"""Pins for #34a — ShareASale + CJ + EarnKaro + Impact adapters.

Key invariants:
  1. Adapters fall back to "" (graceful skip) when env vars unset.
     This lets geo_link_resolver try the next candidate instead of
     crashing the affiliate-match pipeline stage.
  2. Adapters return valid templated URLs when env vars + per-product
     kwargs are present (ShareASale + CJ only — EarnKaro + Impact
     still need their HTTP integrations).
  3. validate_url correctly recognizes URLs from all 5 CJ click-tracking
     subdomains, both ekaro.in + earnkaro.com, both impact.com + sjv.io.
  4. Missing per-product kwargs (banner_id, ad_id) → graceful skip,
     never an invalid URL.
  5. URL encoding survives a special-character target URL (no double-
     encoding, no broken `%` sequences).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.monetization.network_registry import (
    CJAffiliateAdapter,
    EarnKaroAdapter,
    ImpactAdapter,
    ShareASaleAdapter,
    get_adapter,
    validate_affiliate_url,
)


# ── ShareASale ─────────────────────────────────────────────────────────────

class TestShareASale:
    def test_skip_when_user_id_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            adapter = ShareASaleAdapter()
            assert adapter.generate_url("any_product") == ""

    def test_skip_when_banner_or_merchant_missing(self):
        """Per-product kwargs are required — without them, the URL
        would have placeholder values. Skip instead of build an invalid
        URL that earns no commission."""
        with patch.dict("os.environ", {"SHAREASALE_USER_ID": "ABC123"}):
            adapter = ShareASaleAdapter()
            # No banner_id / merchant_id
            assert adapter.generate_url("p") == ""
            # banner_id only
            assert adapter.generate_url("p", banner_id="123") == ""
            # merchant_id only
            assert adapter.generate_url("p", merchant_id="456") == ""

    def test_full_url_when_all_inputs_present(self):
        with patch.dict("os.environ", {"SHAREASALE_USER_ID": "ABC123"}):
            adapter = ShareASaleAdapter()
            url = adapter.generate_url(
                "p",
                banner_id="999",
                merchant_id="42",
                sub_id="gaming_abc12345",
                target_url="https://merchant.example.com/product/42",
            )
            assert url.startswith("https://shareasale.com/r.cfm?")
            assert "b=999" in url
            assert "u=ABC123" in url
            assert "m=42" in url
            assert "afftrack=gaming_abc12345" in url
            # urlencode quotes the target URL — verify it's encoded, not raw
            assert "https%3A%2F%2Fmerchant.example.com%2Fproduct%2F42" in url

    def test_optional_params_omitted_when_unset(self):
        with patch.dict("os.environ", {"SHAREASALE_USER_ID": "ABC"}):
            url = ShareASaleAdapter().generate_url(
                "p", banner_id="1", merchant_id="2"
            )
            # No sub_id / target_url → those keys MUST NOT be in the URL
            assert "afftrack" not in url
            assert "urllink" not in url

    def test_validate_url_recognizes_shareasale_links(self):
        assert ShareASaleAdapter().validate_url(
            "https://shareasale.com/r.cfm?b=1&u=2&m=3"
        )
        assert not ShareASaleAdapter().validate_url("https://amazon.com/dp/B0XYZ")


# ── CJ Affiliate ───────────────────────────────────────────────────────────

class TestCJAffiliate:
    def test_skip_when_publisher_id_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            assert CJAffiliateAdapter().generate_url("p", ad_id="123") == ""

    def test_skip_when_ad_id_missing(self):
        """ad_id is product-specific — without it, no valid click URL exists."""
        with patch.dict("os.environ", {"CJ_PUBLISHER_ID": "100"}):
            assert CJAffiliateAdapter().generate_url("p") == ""

    def test_default_domain_is_anrdoezrs(self):
        with patch.dict("os.environ", {"CJ_PUBLISHER_ID": "100"}):
            url = CJAffiliateAdapter().generate_url("p", ad_id="999")
            assert url.startswith("https://www.anrdoezrs.net/click-100-999")

    def test_caller_can_override_domain_for_load_balancing(self):
        with patch.dict("os.environ", {"CJ_PUBLISHER_ID": "100"}):
            url = CJAffiliateAdapter().generate_url(
                "p", ad_id="999", cj_domain="kqzyfj.com"
            )
            assert "https://www.kqzyfj.com/" in url

    def test_url_with_target_and_sid(self):
        with patch.dict("os.environ", {"CJ_PUBLISHER_ID": "100"}):
            url = CJAffiliateAdapter().generate_url(
                "p",
                ad_id="999",
                target_url="https://merch.example.com/widget",
                sub_id="movies_xyz789ab",
            )
            assert "url=https%3A%2F%2Fmerch.example.com%2Fwidget" in url
            assert "sid=movies_xyz789ab" in url

    def test_validate_url_recognizes_all_cj_subdomains(self):
        adapter = CJAffiliateAdapter()
        # Five known CJ click-tracking subdomains
        for domain in (
            "anrdoezrs.net",
            "dpbolvw.net",
            "kqzyfj.com",
            "tkqlhce.com",
            "jdoqocy.com",
            "cj.com",
        ):
            assert adapter.validate_url(f"https://www.{domain}/click-1-2"), (
                f"CJ adapter should recognize {domain}"
            )
        assert not adapter.validate_url("https://amazon.com/dp/B0XYZ")


# ── EarnKaro ───────────────────────────────────────────────────────────────

class TestEarnKaro:
    def test_skip_when_convert_key_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            assert EarnKaroAdapter().generate_url("p") == ""

    def test_integration_live_in_34b(self):
        """As of #34b (2026-06-13), EarnKaro is live via the HTTP
        client. Adapter delegates to convert_url; this test pins the
        delegation so a future refactor that breaks the wiring fails
        loudly. The detailed conversion behavior is covered by
        test_earnkaro_client.py."""
        with patch.dict("os.environ", {"EARNKARO_CONVERT_KEY": "tok"}):
            with patch(
                "genlab_core.monetization.earnkaro_client.convert_url",
                return_value="https://ekaro.in/abc",
            ) as mock_convert:
                url = EarnKaroAdapter().generate_url(
                    "p", target_url="https://merch.example.com/p"
                )
                assert url == "https://ekaro.in/abc"
                mock_convert.assert_called_once()

    def test_validate_url_recognizes_both_domains(self):
        adapter = EarnKaroAdapter()
        assert adapter.validate_url("https://ekaro.in/abc123")
        assert adapter.validate_url("https://www.earnkaro.com/deals/xyz")


# ── Impact.com ─────────────────────────────────────────────────────────────

class TestImpact:
    def test_skip_when_either_credential_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            assert ImpactAdapter().generate_url("p") == ""
        # account_sid set but token missing
        with patch.dict("os.environ", {"IMPACT_ACCOUNT_SID": "ACC"}, clear=True):
            assert ImpactAdapter().generate_url("p") == ""
        # token set but account_sid missing
        with patch.dict("os.environ", {"IMPACT_API_TOKEN": "TOK"}, clear=True):
            assert ImpactAdapter().generate_url("p") == ""

    def test_integration_live_in_34c(self):
        """As of #34c (2026-06-13), Impact ships per-advertiser template
        substitution from impact_advertisers.yaml. With account_sid set
        but no advertiser_key kwarg, generate_url returns "" gracefully
        (the matcher tries the next network). Detailed template behavior
        is covered by test_impact_client.py."""
        with patch.dict("os.environ", {"IMPACT_ACCOUNT_SID": "ACC"}):
            # No advertiser_key kwarg → graceful skip (not a crash)
            assert ImpactAdapter().generate_url("p") == ""

    def test_validate_url_recognizes_impact_and_sjv(self):
        adapter = ImpactAdapter()
        assert adapter.validate_url("https://example.impact.com/cmp/ABC")
        assert adapter.validate_url("https://publisher.sjv.io/c/12345/678/901")


# ── Cross-cutting: registry wiring ─────────────────────────────────────────

class TestRegistryWiring:
    def test_all_4_adapters_resolvable_by_id(self):
        """get_adapter() must find each of the new 4 adapters by their
        network_id. If a future PR mistypes the key in the ADAPTERS
        dict, this catches it before runtime."""
        for nid in ("earnkaro", "impact", "shareasale", "cj"):
            adapter = get_adapter(nid)
            assert adapter is not None, f"missing adapter for {nid}"
            assert adapter.network_id == nid

    def test_validate_affiliate_url_detects_new_networks(self):
        """validate_affiliate_url scans all adapters. The 4 new networks
        must each be detectable from a sample URL — otherwise URLs that
        sneak in via product feeds get classified as 'unknown'."""
        cases = [
            ("https://ekaro.in/abc", "earnkaro"),
            ("https://publisher.sjv.io/c/1/2/3", "impact"),
            ("https://shareasale.com/r.cfm?b=1", "shareasale"),
            ("https://www.anrdoezrs.net/click-100-999", "cj"),
        ]
        for url, expected_network in cases:
            assert validate_affiliate_url(url) == expected_network, (
                f"{url} should resolve to {expected_network}"
            )
