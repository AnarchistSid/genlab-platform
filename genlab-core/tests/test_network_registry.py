"""Tests for the affiliate network adapter registry."""
import os

import pytest

# Set test affiliate tags before importing adapters (they read env at class init)
os.environ.setdefault("AMAZON_US_AFFILIATE_TAG", "test-tag-20")
os.environ.setdefault("AMAZON_IN_AFFILIATE_TAG", "test-tag-21")
os.environ.setdefault("CUELINKS_PUBLISHER_ID", "000000")

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
    url = "https://linksredirect.com/?cid=***REMOVED***&source=linkkit&url=https%3A%2F%2Fwww.amazon.in%2F"
    network = validate_affiliate_url(url)
    assert network == "cuelinks"


def test_validate_url_unknown():
    url = "https://example.com/product/123"
    network = validate_affiliate_url(url)
    assert network is None


def test_earnkaro_generate_raises():
    adapter = get_adapter("earnkaro")
    with pytest.raises(NotImplementedError):
        adapter.generate_url("B0CY5QW186")
