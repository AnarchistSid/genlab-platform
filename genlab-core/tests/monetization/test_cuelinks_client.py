"""Tests for genlab_core.monetization.cuelinks_client.

The Amazon guard is the load-bearing test in this file — the 2026-06-14
audit removed Cuelinks from the affiliate candidate list after prod
data showed every Cuelinks-brokered Amazon redirect earned ₹0. The V3
re-integration adds Cuelinks back ONLY for non-Amazon merchants. If a
future refactor removes or weakens the guard, these pins fail loudly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.monetization import cuelinks_client
from genlab_core.monetization.cuelinks_client import (
    _AMAZON_DOMAINS,
    AmazonUrlNotAllowed,
    _is_amazon_url,
    convert_url,
    list_campaigns,
    verify,
)

# ─── Amazon guard ────────────────────────────────────────────────────


class TestAmazonUrlDetection:
    """The ``_is_amazon_url`` predicate is what stands between us and the
    2026-06-14 ₹0-commission incident. Coverage matters more than speed."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://amazon.com/dp/B0XYZ",
            "https://www.amazon.com/dp/B0XYZ",
            "https://amazon.in/dp/B0XYZ",
            "https://www.amazon.in/dp/B0XYZ?tag=aspirehub-21",
            "https://amazon.co.uk/dp/B0XYZ",
            "https://amazon.de/dp/B0XYZ",
            "https://amazon.co.jp/dp/B0XYZ",
            "https://amazon.ca/dp/B0XYZ",
            "https://AMAZON.COM/dp/B0XYZ",  # case-insensitive
            "https://Amazon.In/dp/B0XYZ",
        ],
    )
    def test_amazon_urls_flagged(self, url):
        assert _is_amazon_url(url) is True, f"missed: {url}"

    @pytest.mark.parametrize(
        "url",
        [
            "https://flipkart.com/product/B0XYZ",
            "https://www.myntra.com/tshirt/12345",
            "https://ajio.com/xyz",
            "https://meesho.com/product/abc",
            "https://nykaa.com/skincare/def",
            # Trick cases — non-Amazon domains that contain 'amazon' as substring
            "https://amazon-lookalike.com/dp/xyz",
            "https://not-amazon.in/dp/xyz",
            "https://fake-amazon.io/xyz",
            # Query-string injection attempt (netloc is still not amazon)
            "https://malicious.com/redirect?url=https://amazon.in/dp/B0XYZ",
        ],
    )
    def test_non_amazon_urls_pass(self, url):
        assert _is_amazon_url(url) is False, f"false-positive: {url}"

    def test_amazon_domain_set_is_frozen(self):
        """``_AMAZON_DOMAINS`` is intentionally a frozenset — mutating it
        at runtime (e.g. via a well-meaning "add domain" hot-path) would
        be silent and unauditable. If this ever becomes a set/list, the
        code is drifting away from the invariant."""
        assert isinstance(_AMAZON_DOMAINS, frozenset)

    def test_new_amazon_geo_requires_explicit_addition(self):
        """Pin: every Amazon storefront geo we support must be in the
        set explicitly. If a new geo is added to the AMAZON_ADAPTERS
        elsewhere in the codebase (e.g. amazon.sg), someone must ALSO
        add it here — otherwise the guard silently doesn't fire for
        that geo, and cuelinks re-becomes a ₹0-attribution proxy for
        it."""
        # Core geos we support in prod today
        for geo in ("amazon.com", "amazon.in"):
            assert geo in _AMAZON_DOMAINS


class TestConvertUrlAmazonGuard:
    """convert_url() MUST raise AmazonUrlNotAllowed for any Amazon URL.
    This is the runtime enforcement of the 2026-06-14 invariant."""

    def test_raises_on_amazon_com(self):
        with pytest.raises(AmazonUrlNotAllowed) as exc_info:
            convert_url("https://amazon.com/dp/B0XYZ")
        # Error message must cite the audit + point at the fix location
        # so a future engineer hitting this can find the context fast
        assert "2026-06-14" in str(exc_info.value)
        assert "amazon_us" in str(exc_info.value) or "amazon_in" in str(exc_info.value)

    def test_raises_on_amazon_in(self):
        with pytest.raises(AmazonUrlNotAllowed):
            convert_url("https://amazon.in/dp/B0XYZ")

    def test_raises_even_with_amazon_tag_in_url(self):
        """Someone might argue 'we CAN put the tag in the query string
        and route through cuelinks safely' — the audit tested exactly
        this variant and it still lost the tag at the redirect layer.
        Guard fires regardless of tag presence."""
        with pytest.raises(AmazonUrlNotAllowed):
            convert_url("https://amazon.in/dp/B0XYZ?tag=aspirehub-21")

    def test_raises_before_env_var_check(self):
        """The guard must fire even when CUELINKS_V3_API_KEY is unset.
        Reason: we want a hard error in dev/test envs (no key) not a
        silent '' return that a caller might treat as 'cuelinks
        unavailable, fall back to Amazon direct'. AmazonUrlNotAllowed
        forces the caller to fix their code path."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AmazonUrlNotAllowed):
                convert_url("https://amazon.com/dp/B0XYZ")


class TestConvertUrlHappyPath:
    """Non-Amazon URLs should round-trip cleanly."""

    def test_flipkart_url_hits_convert_endpoint(self):
        fake_body = json.dumps(
            {"tracking_url": "https://linksredirect.com/?cid=X&url=flipkart..."}
        ).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None

        with (
            patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}),
            patch("urllib.request.urlopen", return_value=fake_resp),
        ):
            # Force refresh so we don't hit a stale cache entry
            result = convert_url(
                "https://flipkart.com/product/B0XYZ",
                subid="gaming:abc12345",
                force_refresh=True,
            )
        assert "linksredirect.com" in result

    def test_empty_url_returns_empty(self):
        assert convert_url("") == ""

    def test_non_http_url_returns_empty(self):
        assert convert_url("ftp://example.com/xyz") == ""

    def test_missing_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            assert convert_url("https://flipkart.com/product/x") == ""


class TestVerifyPing:
    def test_returns_body_on_success(self):
        fake_body = json.dumps({"publisher_id": "12345"}).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None

        with (
            patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}),
            patch("urllib.request.urlopen", return_value=fake_resp),
        ):
            result = verify()
        assert result == {"publisher_id": "12345"}

    def test_returns_none_when_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert verify() is None


class TestListCampaigns:
    def test_returns_list_on_success(self):
        fake_body = json.dumps(
            {
                "campaigns": [
                    {"id": 1, "name": "Flipkart", "epc_7d": 3.5},
                    {"id": 2, "name": "Myntra", "epc_7d": 2.1},
                ]
            }
        ).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None

        with (
            patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}),
            patch("urllib.request.urlopen", return_value=fake_resp),
        ):
            result = list_campaigns(force_refresh=True)
        assert len(result) == 2
        assert result[0]["name"] == "Flipkart"

    def test_returns_empty_on_failure(self):
        import urllib.error

        with (
            patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("network down"),
            ),
        ):
            assert list_campaigns(force_refresh=True) == []


class TestModuleContract:
    """Pin the exports so a future refactor can't accidentally
    drop the Amazon guard by removing AmazonUrlNotAllowed."""

    def test_amazon_url_not_allowed_is_a_value_error(self):
        """Subclass of ValueError so existing broad-except handlers
        treat it as a caller mistake (not a network error). If a
        refactor promotes it to Exception directly, dozens of
        try-except sites in geo_link_resolver stop catching it."""
        assert issubclass(AmazonUrlNotAllowed, ValueError)

    def test_public_exports_present(self):
        # Anything a downstream caller imports must survive refactors
        for name in (
            "AmazonUrlNotAllowed",
            "convert_url",
            "list_campaigns",
            "verify",
            "_is_amazon_url",
            "_AMAZON_DOMAINS",
        ):
            assert hasattr(cuelinks_client, name), f"missing export: {name}"
