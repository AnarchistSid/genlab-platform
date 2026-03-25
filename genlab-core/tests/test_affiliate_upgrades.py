"""Tests for affiliate system upgrades (Sprint 69).

Covers:
- LLM-powered contextual matching (_llm_match_product)
- UTM parameter appending (append_utm_params)
- Product slug generation
- QR code snippet generation
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from genlab_core.monetization.affiliate_matcher import (
    _build_llm_product_list,
    _llm_match_product,
    match_product,
)
from genlab_core.monetization.cta_engine import append_utm_params


# ---------------------------------------------------------------------------
# UTM parameter tests
# ---------------------------------------------------------------------------


class TestAppendUtmParams:
    """Tests for the append_utm_params utility."""

    def test_basic_utm_append(self):
        """UTM params are appended to a clean URL."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***"
        result = append_utm_params(url, niche_id="gaming", blueprint_id="bp-123")
        assert "utm_source=genlab" in result
        assert "utm_medium=affiliate" in result
        assert "utm_campaign=gaming" in result
        assert "utm_content=bp-123" in result

    def test_preserves_existing_params(self):
        """Existing query parameters are preserved."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***"
        result = append_utm_params(url, niche_id="gaming")
        assert "tag=***REMOVED***" in result
        assert "utm_source=genlab" in result

    def test_no_double_utm(self):
        """If UTM params already exist, don't add them again."""
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***&utm_source=existing"
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
        url = "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***"
        result = append_utm_params(url)
        assert "utm_source=genlab" in result
        assert "utm_medium=affiliate" in result
        assert "utm_campaign" not in result  # no niche = no campaign param


# ---------------------------------------------------------------------------
# LLM product list builder tests
# ---------------------------------------------------------------------------


class TestBuildLLMProductList:
    """Tests for the prompt product list builder."""

    def test_builds_numbered_list(self):
        """Products are formatted as numbered lines with name and category."""
        products = [
            {"name": "PS5 Console", "category": "hardware", "keywords": ["ps5", "playstation"]},
            {"name": "Gaming Mouse", "category": "peripheral", "keywords": ["mouse", "logitech"]},
        ]
        result = _build_llm_product_list(products)
        assert "0: PS5 Console [hardware]" in result
        assert "1: Gaming Mouse [peripheral]" in result

    def test_handles_empty_keywords(self):
        """Products with no keywords still format correctly."""
        products = [{"name": "Test", "category": "test", "keywords": []}]
        result = _build_llm_product_list(products)
        assert "0: Test [test]" in result

    def test_truncates_keywords_to_five(self):
        """Only the first 5 keywords are included in the prompt."""
        products = [
            {"name": "X", "category": "y", "keywords": ["a", "b", "c", "d", "e", "f", "g"]},
        ]
        result = _build_llm_product_list(products)
        assert "f" not in result
        assert "g" not in result


# ---------------------------------------------------------------------------
# LLM match product tests (mocked)
# ---------------------------------------------------------------------------


class TestLLMMatchProduct:
    """Tests for the LLM contextual matcher with mocked API calls."""

    _products = [
        {"name": "PS5 Console", "category": "hardware", "keywords": ["ps5"], "enabled": True},
        {"name": "Gaming Mouse", "category": "peripheral", "keywords": ["mouse"], "enabled": True},
        {"name": "Gaming Chair", "category": "hardware", "keywords": ["chair"], "enabled": True},
    ]

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_returns_none(self):
        """Without ANTHROPIC_API_KEY, LLM match returns None."""
        result = _llm_match_product("some text about gaming", "gaming", self._products)
        assert result is None

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("genlab_core.writing.llm_client.AnthropicLLMClient")
    def test_llm_returns_valid_index(self, mock_llm_cls):
        """LLM returning a valid product index selects that product."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "1"
        mock_llm_cls.return_value = mock_client

        result = _llm_match_product("wireless logitech setup", "gaming", self._products)
        assert result is not None
        assert result["name"] == "Gaming Mouse"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("httpx.post")
    def test_llm_returns_none_string(self, mock_post):
        """LLM returning 'none' means no match."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "none"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _llm_match_product("weather forecast today", "gaming", self._products)
        assert result is None

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("httpx.post")
    def test_llm_returns_out_of_range_index(self, mock_post):
        """LLM returning an out-of-range index returns None."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "99"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _llm_match_product("something", "gaming", self._products)
        assert result is None

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("httpx.post")
    def test_llm_api_failure_returns_none(self, mock_post):
        """API call failure is handled gracefully."""
        mock_post.side_effect = Exception("network error")

        result = _llm_match_product("something", "gaming", self._products)
        assert result is None

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("genlab_core.writing.llm_client.AnthropicLLMClient")
    def test_llm_parses_index_from_verbose_response(self, mock_llm_cls):
        """LLM responding with '2 - Gaming Chair' still parses index 2."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "2 - Gaming Chair seems most relevant"
        mock_llm_cls.return_value = mock_client

        result = _llm_match_product("ergonomic setup for long sessions", "gaming", self._products)
        assert result is not None
        assert result["name"] == "Gaming Chair"

    def test_empty_products_returns_none(self):
        """Empty product list returns None without calling API."""
        result = _llm_match_product("some text", "gaming", [])
        assert result is None

    def test_disabled_products_filtered(self):
        """Disabled products are excluded from LLM matching."""
        products = [
            {"name": "Disabled", "category": "test", "keywords": ["x"], "enabled": False},
        ]
        result = _llm_match_product("some text", "gaming", products)
        assert result is None


# ---------------------------------------------------------------------------
# Integration: keyword fallback → LLM pipeline
# ---------------------------------------------------------------------------


class TestMatchProductWithLLMFallback:
    """Tests that match_product falls back to LLM when keywords fail."""

    def _make_catalog(self, products):
        return {"niches": {"gaming": {"products": products}}, "settings": {}}

    @patch("genlab_core.monetization.affiliate_matcher._llm_match_product")
    def test_keyword_match_skips_llm(self, mock_llm):
        """When keyword matching succeeds, LLM is NOT called."""
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5", "playstation"],
                "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
            }
        ]
        catalog = self._make_catalog(products)
        result = match_product("the ps5 playstation is great", "gaming", catalog, seasonal_config={})
        assert result is not None
        assert result["name"] == "PS5 Console"
        mock_llm.assert_not_called()

    @patch("genlab_core.monetization.affiliate_matcher._llm_match_product")
    def test_keyword_miss_triggers_llm(self, mock_llm):
        """When keyword matching fails, LLM is called as fallback."""
        mock_llm.return_value = {
            "name": "PS5 Console",
            "keywords": ["ps5"],
            "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
        }
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5", "playstation"],
                "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
            }
        ]
        catalog = self._make_catalog(products)
        # Text has NO keyword matches
        result = match_product("next gen gaming experience review", "gaming", catalog, seasonal_config={})
        assert result is not None
        mock_llm.assert_called_once()

    @patch("genlab_core.monetization.affiliate_matcher._llm_match_product")
    def test_llm_returns_none_means_no_match(self, mock_llm):
        """When LLM also returns None, match_product returns None."""
        mock_llm.return_value = None
        products = [
            {
                "name": "PS5 Console",
                "keywords": ["ps5"],
                "networks": {"amazon": {"url": "https://amzn.to/ps5", "commission_pct": 4.0}},
            }
        ]
        catalog = self._make_catalog(products)
        result = match_product("weather in new york today", "gaming", catalog, seasonal_config={})
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
