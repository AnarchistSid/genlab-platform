"""Tests for genlab_core.monetization.deep_linker."""

from genlab_core.monetization.deep_linker import (
    DeepLink,
    generate_deep_link,
    is_mobile,
    render_deep_link_page,
)

# ── Sample User-Agents ────────────────────────────────────────────────────────

_UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_UA_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
_UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_AMAZON_URL = "https://www.amazon.com/dp/B08N5KWB9H?tag=genlab-21&linkCode=ogi"
_AMAZON_IN_URL = "https://www.amazon.in/dp/B08N5KWB9H?tag=genlab-in-21"
_NON_AMAZON_URL = "https://www.flipkart.com/some-product/p/itm123"
_AMAZON_SEARCH_URL = "https://www.amazon.com/s?k=headphones&tag=genlab-21"


# ── is_mobile ────────────────────────────────────────────────────────────────

def test_is_mobile_iphone():
    assert is_mobile(_UA_IPHONE) is True


def test_is_mobile_android():
    assert is_mobile(_UA_ANDROID) is True


def test_is_mobile_desktop():
    assert is_mobile(_UA_DESKTOP) is False


# ── generate_deep_link ────────────────────────────────────────────────────────

def test_deep_link_android_amazon():
    result = generate_deep_link(_AMAZON_URL, _UA_ANDROID)
    assert result is not None
    assert result.is_android is True
    assert result.is_ios is False
    assert result.is_mobile is True
    # Must be an Android intent URL
    assert result.app_url.startswith("intent://")
    assert "B08N5KWB9H" in result.app_url
    assert "com.amazon.mShop.android.shopping" in result.app_url
    # Fallback URL present
    assert result.web_url == _AMAZON_URL


def test_deep_link_ios_amazon():
    result = generate_deep_link(_AMAZON_URL, _UA_IPHONE)
    assert result is not None
    assert result.is_ios is True
    assert result.is_android is False
    assert result.is_mobile is True
    # Must be amzn:// scheme
    assert result.app_url.startswith("amzn://")
    assert "B08N5KWB9H" in result.app_url
    assert result.web_url == _AMAZON_URL


def test_deep_link_desktop_returns_none():
    result = generate_deep_link(_AMAZON_URL, _UA_DESKTOP)
    assert result is None


def test_deep_link_non_amazon_returns_none():
    result = generate_deep_link(_NON_AMAZON_URL, _UA_ANDROID)
    assert result is None


def test_deep_link_no_asin_returns_none():
    """Amazon search URL has no /dp/<ASIN> — should return None."""
    result = generate_deep_link(_AMAZON_SEARCH_URL, _UA_ANDROID)
    assert result is None


def test_deep_link_preserves_tag():
    """The affiliate tag should appear in the generated app URL."""
    result = generate_deep_link(_AMAZON_URL, _UA_ANDROID)
    assert result is not None
    assert "genlab-21" in result.app_url


def test_deep_link_preserves_tag_ios():
    result = generate_deep_link(_AMAZON_URL, _UA_IPHONE)
    assert result is not None
    assert "genlab-21" in result.app_url


def test_deep_link_amazon_in_uses_correct_domain():
    """amazon.in URLs should use the .in domain in the deep link."""
    result = generate_deep_link(_AMAZON_IN_URL, _UA_ANDROID)
    assert result is not None
    assert "amazon.in" in result.app_url


def test_deep_link_ipad_is_ios():
    result = generate_deep_link(_AMAZON_URL, _UA_IPAD)
    assert result is not None
    assert result.is_ios is True
    assert result.app_url.startswith("amzn://")


# ── render_deep_link_page ─────────────────────────────────────────────────────

def test_render_page_contains_both_urls():
    dl = DeepLink(
        app_url="amzn://www.amazon.com/dp/B08N5KWB9H?tag=genlab-21",
        web_url=_AMAZON_URL,
        is_mobile=True,
        is_ios=True,
        is_android=False,
    )
    html = render_deep_link_page(dl, product_name="Test Headphones")
    assert dl.app_url in html
    assert dl.web_url in html
    assert "Test Headphones" in html


def test_render_page_no_product_name():
    dl = DeepLink(
        app_url="amzn://www.amazon.com/dp/B08N5KWB9H",
        web_url=_AMAZON_URL,
        is_mobile=True,
        is_ios=True,
        is_android=False,
    )
    html = render_deep_link_page(dl)
    # Should not crash and should have fallback text
    assert "product" in html
    assert dl.web_url in html


def test_render_page_is_valid_html():
    dl = DeepLink(
        app_url="intent://www.amazon.com/dp/B08N5KWB9H#Intent;end",
        web_url=_AMAZON_URL,
        is_mobile=True,
        is_ios=False,
        is_android=True,
    )
    html = render_deep_link_page(dl, "Widget")
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html
