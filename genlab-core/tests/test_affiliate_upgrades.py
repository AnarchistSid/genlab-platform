"""Tests for affiliate system upgrades (Sprint 69).

Covers:
- UTM parameter appending (append_utm_params)
- Product slug generation
- QR code snippet generation
- Match-product fails-closed behaviour when keywords miss

Note: an earlier version of this file also covered an LLM-powered
contextual matcher (``_llm_match_product``) used as a static-catalog
fallback.  That code path was removed in the cluster D structural fix
because the LLM was too willing to rationalise a match for unrelated
content (it produced "Anime Figure Collection" for a Wistoria character
moment with zero keyword overlap).  Static catalog now fails closed on
zero hits; the dynamic Amazon-search matcher has its own quality gates.
"""
from __future__ import annotations

from genlab_core.monetization.affiliate_matcher import match_product
from genlab_core.monetization.cta_engine import append_utm_params

# ---------------------------------------------------------------------------
# UTM parameter tests
# ---------------------------------------------------------------------------


class TestAppendUtmParams:
    """Tests for the append_utm_params utility."""

    def test_basic_utm_append(self):
        """UTM params are appended to a clean URL."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21"
        result = append_utm_params(url, niche_id="gaming", blueprint_id="bp-123")
        assert "utm_source=genlab" in result
        assert "utm_medium=affiliate" in result
        assert "utm_campaign=gaming" in result
        assert "utm_content=bp-123" in result

    def test_preserves_existing_params(self):
        """Existing query parameters are preserved."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21"
        result = append_utm_params(url, niche_id="gaming")
        assert "tag=test-tag-21" in result
        assert "utm_source=genlab" in result

    def test_no_double_utm(self):
        """If UTM params already exist, don't add them again."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21&utm_source=existing"
        result = append_utm_params(url, niche_id="gaming")
        assert result == url  # unchanged

    def test_empty_url_returns_empty(self):
        """Empty URL returns empty string."""
        assert append_utm_params("", niche_id="gaming") == ""

    def test_url_without_existing_query(self):
        """URL without any query params gets ? before UTM."""
        url = "https://www.amazon.in/dp/B0CY5QW186"
        result = append_utm_params(url, niche_id="sports")
        assert "?" in result
        assert "utm_source=genlab" in result

    def test_niche_optional(self):
        """UTM params work without niche_id."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21"
        result = append_utm_params(url)
        assert "utm_source=genlab" in result
        assert "utm_medium=affiliate" in result
        assert "utm_campaign" not in result  # no niche = no campaign param


# ---------------------------------------------------------------------------
# LLM product list builder tests
# ---------------------------------------------------------------------------


class TestMatchProductFailsClosed:
    """match_product returns the keyword-best product, or None — never an LLM force-match."""

    def _make_catalog(self, products):
        return {"niches": {"gaming": {"products": products}}, "settings": {}}

    def test_keyword_match_returns_product(self):
        """Strong keyword overlap returns the matching product."""
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5", "playstation"],
                "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
            },
        ]
        catalog = self._make_catalog(products)
        result = match_product(
            "the ps5 playstation is great", "gaming", catalog, seasonal_config={},
        )
        assert result is not None
        assert result["name"] == "PS5 Console"

    def test_zero_keyword_hits_returns_none(self):
        """Content with no keyword overlap returns None — no LLM force-match.

        This is the closed-fail behaviour that replaces the old LLM contextual
        fallback.  Previously, content like "weather in new york today" would
        get a forced-but-irrelevant product match.  Now it returns None and
        the caller's dynamic matcher (if eligible) can try a different path.
        """
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5", "playstation"],
                "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
            },
        ]
        catalog = self._make_catalog(products)
        result = match_product(
            "weather in new york today", "gaming", catalog, seasonal_config={},
        )
        assert result is None


# ---------------------------------------------------------------------------
# QR code snippet tests
# ---------------------------------------------------------------------------


class TestQRCodeSnippet:
    """Tests for the YouTube description snippet generator."""

    def test_snippet_contains_channel_url(self):
        from genlab_core.monetization.qr_generator import get_youtube_description_snippet
        snippet = get_youtube_description_snippet("clutchwire")
        assert "/links/clutchwire" in snippet
        assert "ref=yt_desc" in snippet

    def test_snippet_contains_scan_text(self):
        from genlab_core.monetization.qr_generator import get_youtube_description_snippet
        snippet = get_youtube_description_snippet("criticalrush")
        assert "Scan" in snippet or "scan" in snippet.lower()
