"""Tests for genlab_core.monetization.paapi_client."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from genlab_core.monetization.paapi_client import (
    NICHE_SEARCH_INDEX,
    PaapiClient,
    PaapiProduct,
)


def _make_product(**kwargs) -> PaapiProduct:
    defaults = dict(
        asin="B000EXAMPLE",
        title="Test Product",
        price=999.0,
        currency="USD",
        image_url="https://example.com/img.jpg",
        detail_url="https://www.amazon.com/dp/B000EXAMPLE",
    )
    defaults.update(kwargs)
    return PaapiProduct(**defaults)


# ---------------------------------------------------------------------------
# test_not_configured_returns_empty
# ---------------------------------------------------------------------------


def test_not_configured_returns_empty(tmp_path, monkeypatch):
    """Client with no credentials must return [] for search and None for get_by_asin."""
    monkeypatch.delenv("PAAPI_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PAAPI_SECRET_KEY", raising=False)

    client = PaapiClient(access_key="", secret_key="", cache_dir=tmp_path)

    assert client.search("PS5 console") == []
    assert client.get_by_asin("B0DJHG2VVS") is None


# ---------------------------------------------------------------------------
# test_is_configured_with_keys
# ---------------------------------------------------------------------------


def test_is_configured_with_keys(tmp_path):
    """is_configured returns True when both keys are set."""
    client = PaapiClient(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG",
        cache_dir=tmp_path,
    )
    assert client.is_configured is True


def test_is_configured_false_when_missing(tmp_path, monkeypatch):
    """is_configured returns False when either key is absent."""
    monkeypatch.delenv("PAAPI_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PAAPI_SECRET_KEY", raising=False)

    client = PaapiClient(access_key="", secret_key="", cache_dir=tmp_path)
    assert client.is_configured is False


# ---------------------------------------------------------------------------
# test_cache_write_and_read
# ---------------------------------------------------------------------------


def test_cache_write_and_read(tmp_path):
    """Written products are read back correctly from disk cache."""
    client = PaapiClient(
        access_key="key",
        secret_key="secret",
        cache_dir=tmp_path,
        cache_ttl=3600,
    )

    products = [
        _make_product(asin="B001", title="Gaming Headset", price=2999.0),
        _make_product(asin="B002", title="PS5 Controller", price=5999.0),
    ]

    key = client._cache_key("test-query")
    client._write_cache(key, products)
    result = client._read_cache(key)

    assert result is not None
    assert len(result) == 2
    assert result[0].asin == "B001"
    assert result[0].title == "Gaming Headset"
    assert result[1].asin == "B002"
    assert result[1].price == 5999.0


# ---------------------------------------------------------------------------
# test_cache_expiry
# ---------------------------------------------------------------------------


def test_cache_expiry(tmp_path):
    """Expired cache (ts too old) returns None."""
    client = PaapiClient(
        access_key="key",
        secret_key="secret",
        cache_dir=tmp_path,
        cache_ttl=10,  # 10 seconds TTL
    )

    products = [_make_product()]
    key = client._cache_key("expired-query")

    # Write cache with an artificially old timestamp (25 seconds ago)
    path = tmp_path / f"{key}.json"
    data = {
        "ts": time.time() - 25,
        "products": [asdict(p) for p in products],
    }
    path.write_text(json.dumps(data))

    result = client._read_cache(key)
    assert result is None


def test_cache_not_expired_returns_data(tmp_path):
    """Fresh cache (within TTL) returns products."""
    client = PaapiClient(
        access_key="key",
        secret_key="secret",
        cache_dir=tmp_path,
        cache_ttl=3600,
    )

    products = [_make_product(asin="B999", title="Fresh Product")]
    key = client._cache_key("fresh-query")
    client._write_cache(key, products)

    result = client._read_cache(key)
    assert result is not None
    assert result[0].asin == "B999"


# ---------------------------------------------------------------------------
# test_niche_search_index_mapping
# ---------------------------------------------------------------------------


def test_niche_search_index_mapping():
    """All 5 GenLab niches must have a PA-API SearchIndex mapped."""
    expected_niches = {"gaming", "sports", "movies", "anime", "ai_creators"}
    assert expected_niches == set(NICHE_SEARCH_INDEX.keys())


def test_niche_search_index_values():
    """Each niche maps to a non-empty PA-API SearchIndex string."""
    for niche, index in NICHE_SEARCH_INDEX.items():
        assert isinstance(index, str) and index, f"Empty SearchIndex for niche: {niche}"


def test_niche_search_index_specific_mappings():
    """Spot-check specific niche → SearchIndex mappings."""
    assert NICHE_SEARCH_INDEX["gaming"] == "VideoGames"
    assert NICHE_SEARCH_INDEX["sports"] == "SportingGoods"
    assert NICHE_SEARCH_INDEX["movies"] == "MoviesAndTV"
    assert NICHE_SEARCH_INDEX["anime"] == "Books"
    assert NICHE_SEARCH_INDEX["ai_creators"] == "Electronics"


# ---------------------------------------------------------------------------
# Unconfigured client does not raise on search/get_by_asin
# ---------------------------------------------------------------------------


def test_search_returns_empty_not_raises_when_unconfigured(tmp_path, monkeypatch):
    """search() must return [] gracefully when not configured, never raise."""
    monkeypatch.delenv("PAAPI_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PAAPI_SECRET_KEY", raising=False)

    client = PaapiClient(access_key="", secret_key="", cache_dir=tmp_path)
    result = client.search("anything")
    assert result == []


# ---------------------------------------------------------------------------
# _item_to_product parsing
# ---------------------------------------------------------------------------


def test_item_to_product_full():
    """_item_to_product correctly parses a well-formed PA-API item dict."""
    client = PaapiClient(access_key="k", secret_key="s", cache_dir=Path("/tmp"))
    item = {
        "ASIN": "B0TEST123",
        "DetailPageURL": "https://www.amazon.com/dp/B0TEST123",
        "ItemInfo": {"Title": {"DisplayValue": "Example Product"}},
        "Offers": {"Listings": [{"Price": {"Amount": 49.99, "Currency": "USD"}}]},
        "Images": {"Primary": {"Large": {"URL": "https://example.com/img.jpg"}}},
    }
    product = client._item_to_product(item)

    assert product.asin == "B0TEST123"
    assert product.title == "Example Product"
    assert product.price == 49.99
    assert product.currency == "USD"
    assert product.image_url == "https://example.com/img.jpg"
    assert product.detail_url == "https://www.amazon.com/dp/B0TEST123"


def test_item_to_product_missing_fields():
    """_item_to_product handles missing optional fields with safe defaults."""
    client = PaapiClient(access_key="k", secret_key="s", cache_dir=Path("/tmp"))
    product = client._item_to_product({})

    assert product.asin == ""
    assert product.title == ""
    assert product.price == 0
    assert product.currency == "USD"


# ---------------------------------------------------------------------------
# R-31 (a): _signed_request wiring tests
# ---------------------------------------------------------------------------
#
# These pin the PA-API-specific plumbing layer on top of the SigV4
# implementation (which is independently tested in
# test_paapi_signing.py). End-to-end algorithm correctness is the
# signing module's job; here we verify the client calls it with the
# right per-op path / target / region.


def test_signed_request_unconfigured_raises(tmp_path, monkeypatch):
    """``_signed_request`` is defence in depth — ``search`` /
    ``get_by_asin`` already guard on ``is_configured``, but a
    direct call must surface the misconfiguration clearly."""
    monkeypatch.delenv("PAAPI_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PAAPI_SECRET_KEY", raising=False)
    client = PaapiClient(access_key="", secret_key="", cache_dir=tmp_path)
    with pytest.raises(ValueError, match="PA-API not configured"):
        client._signed_request("SearchItems", {})


def test_signed_request_uses_searchitems_path_and_target(tmp_path):
    """SearchItems operation maps to ``/paapi5/searchitems`` path
    + the documented X-Amz-Target header. Pin so a refactor that
    breaks the path / target derivation surfaces as a test failure
    rather than a generic PA-API 400."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        cache_dir=tmp_path,
        region="us",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"SearchResult": {"Items": []}}
    mock_response.raise_for_status.return_value = None

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ) as mock_post:
        client._signed_request("SearchItems", {"Keywords": "PS5"})

    args, kwargs = mock_post.call_args
    url = args[0]
    assert url == "https://webservices.amazon.com/paapi5/searchitems"
    headers = kwargs["headers"]
    assert headers["X-Amz-Target"] == "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    assert headers["Content-Type"] == "application/json; charset=UTF-8"
    assert headers["Content-Encoding"] == "amz-1.0"
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")


def test_signed_request_uses_getitems_path_and_target(tmp_path):
    """GetItems → ``/paapi5/getitems`` + GetItems X-Amz-Target."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        cache_dir=tmp_path,
        region="us",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"ItemsResult": {"Items": []}}
    mock_response.raise_for_status.return_value = None

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ) as mock_post:
        client._signed_request("GetItems", {"ItemIds": ["B000EXAMPLE"]})

    url, _ = mock_post.call_args
    assert url[0] == "https://webservices.amazon.com/paapi5/getitems"
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-Amz-Target"] == "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"


def test_signed_request_uses_correct_region_for_india(tmp_path):
    """India hits ``eu-west-1`` per the PAAPI_REGION map. The
    Authorization header's credential scope must reflect that.
    Pin so a US-only assumption can't slip back into the routing
    layer."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        cache_dir=tmp_path,
        region="in",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"SearchResult": {"Items": []}}
    mock_response.raise_for_status.return_value = None

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ) as mock_post:
        client._signed_request("SearchItems", {"Keywords": "PS5"})

    assert mock_post.call_args.args[0] == ("https://webservices.amazon.in/paapi5/searchitems")
    auth = mock_post.call_args.kwargs["headers"]["Authorization"]
    assert "eu-west-1/ProductAdvertisingAPI/aws4_request" in auth


def test_signed_request_propagates_http_errors(tmp_path):
    """A non-2xx PA-API response must propagate as HTTPError so
    ``search`` / ``get_by_asin``'s outer try/except logs + returns
    the empty/None fallback rather than silently masking a quota /
    auth / signing error as success."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        cache_dir=tmp_path,
    )

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ):
        with pytest.raises(requests.HTTPError, match="403"):
            client._signed_request("SearchItems", {})


def test_search_swallows_signing_failure_and_returns_empty(tmp_path):
    """The public ``search`` path keeps the "log + degrade
    gracefully" semantics even now that ``_signed_request`` is
    no longer raising NotImplementedError. A pipeline run must
    never crash because PA-API hiccuped."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        cache_dir=tmp_path,
    )

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("503")

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ):
        result = client.search("PS5 console", search_index="VideoGames")

    assert result == []


def test_search_end_to_end_with_mocked_paapi_response(tmp_path):
    """Full happy path: configured client → ``search`` → signed
    request → parsed PaapiProduct list. Mocks the HTTP layer with a
    realistic SearchResult payload."""
    client = PaapiClient(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        partner_tag="genlabtest-20",
        cache_dir=tmp_path,
        region="us",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "SearchResult": {
            "Items": [
                {
                    "ASIN": "B0CY5QW186",
                    "DetailPageURL": "https://www.amazon.com/dp/B0CY5QW186",
                    "ItemInfo": {"Title": {"DisplayValue": "Wireless Gaming Headset"}},
                    "Offers": {"Listings": [{"Price": {"Amount": 4999.0, "Currency": "USD"}}]},
                    "Images": {"Primary": {"Large": {"URL": "https://m.media-amazon.com/x.jpg"}}},
                }
            ]
        }
    }
    mock_response.raise_for_status.return_value = None

    with patch(
        "genlab_core.monetization.paapi_client.requests.post",
        return_value=mock_response,
    ):
        products = client.search("gaming headset", search_index="VideoGames")

    assert len(products) == 1
    assert products[0].asin == "B0CY5QW186"
    assert products[0].title == "Wireless Gaming Headset"
    assert products[0].price == 4999.0
