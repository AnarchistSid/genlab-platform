"""Tests for the affiliate network adapter registry."""

import os

# Set test affiliate tags before importing adapters (they read env at instantiation)
os.environ["AMAZON_US_AFFILIATE_TAG"] = "test-tag-20"
os.environ["AMAZON_IN_AFFILIATE_TAG"] = "test-tag-21"
os.environ["CUELINKS_PUBLISHER_ID"] = "000000"

from genlab_core.monetization.network_registry import (
    ADAPTERS,
    get_adapter,
    validate_affiliate_url,
)


def test_get_adapter_known():
    adapter = get_adapter("amazon")
    assert adapter is not None
    assert adapter.network_id == "amazon"


def test_get_adapter_unknown():
    adapter = get_adapter("unknown_network")
    assert adapter is None


def test_all_adapters_registered():
    assert len(ADAPTERS) == 8


def test_amazon_generate_url():
    adapter = get_adapter("amazon")
    url = adapter.generate_url("B0CY5QW186")
    assert "amazon.in" in url
    assert "B0CY5QW186" in url
    assert "tag=test-tag-21" in url


def test_amazon_us_generate_url():
    adapter = get_adapter("amazon_us")
    url = adapter.generate_url("B0CY5QW186")
    assert "amazon.com" in url
    assert "B0CY5QW186" in url
    assert "tag=test-tag-20" in url


def test_amazon_onelink_generate_url():
    adapter = get_adapter("amazon_onelink")
    url = adapter.generate_url("B0CY5QW186")
    assert "amazon.com" in url
    assert "B0CY5QW186" in url
    assert "linkCode=ll1" in url
    assert "tag=" in url


def test_cuelinks_generate_url():
    adapter = get_adapter("cuelinks")
    url = adapter.generate_url("B0CY5QW186")
    assert "linksredirect.com" in url
    assert "cid=000000" in url
    assert "amazon.in" in url


def test_validate_url_amazon():
    url = "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21"
    network = validate_affiliate_url(url)
    assert network == "amazon"


def test_validate_url_cuelinks():
    url = "https://linksredirect.com/?cid=000000&source=linkkit&url=https%3A%2F%2Fwww.amazon.in%2F"
    network = validate_affiliate_url(url)
    assert network == "cuelinks"


def test_validate_url_unknown():
    url = "https://example.com/product/123"
    network = validate_affiliate_url(url)
    assert network is None


def test_earnkaro_generate_returns_empty_without_key():
    """As of #34b (2026-06-13), EarnKaro is wired to a live HTTP client.
    Without EARNKARO_CONVERT_KEY set, it returns "" gracefully (graceful
    skip — matcher tries next network) instead of raising.

    Previously asserted NotImplementedError on the stub; replaced with the
    actual graceful-skip contract that the new earnkaro_client.convert_url
    enforces. Full live-call behavior is covered by test_earnkaro_client.py."""
    import os

    # Make sure no key is set (some CI envs may have a stale one)
    prior = os.environ.pop("EARNKARO_CONVERT_KEY", None)
    try:
        adapter = get_adapter("earnkaro")
        # Without env var → empty string, never raises
        assert adapter.generate_url("B0CY5QW186") == ""
    finally:
        if prior is not None:
            os.environ["EARNKARO_CONVERT_KEY"] = prior
