"""Regression tests for ``_is_url_healthy`` HTTP code allow-list.

The 2026-06-19 monetization audit found that amazon.com returns HTTP
405 (Method Not Allowed) to our ranged-GET liveness check on /dp/*
URLs, while amazon.in returns 206. With 405 NOT in the allow-list,
every US-targeted catalog product failed health-check, ``geo_link_resolver``
fell back to amazon.in, and PR #277's geo→US routing was a structural
no-op for catalog matches since the day it landed.

These pins guard the allow-list against regression. ANY future PR that
narrows _HEALTHY_CODES (e.g. removing 405 because "405 is weird") must
demonstrate it doesn't re-introduce the amazon.com → amazon.in
fall-through silent failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monetization import geo_link_resolver as glr


class TestUrlHealthyAcceptsAmazonCom405:
    """The critical regression — amazon.com 405 must be 'healthy'."""

    def setup_method(self):
        # Reset the cache between tests so prior results don't leak
        glr._health_cache.clear()

    def test_405_response_is_healthy(self):
        """A 405 from a working URL (e.g. amazon.com refusing ranged
        GET on /dp/*) must be treated as 'reachable'."""
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            resp = MagicMock()
            resp.status = 405
            resp.__enter__ = lambda self: resp
            resp.__exit__ = lambda *a: None
            mock_open.return_value = resp
            assert glr._is_url_healthy("https://www.amazon.com/dp/B0FVGBMH9B") is True

    def test_405_via_httperror_is_healthy(self):
        """When the server raises HTTPError with code 405 (urllib's
        non-2xx default), the URL is still reachable."""
        import urllib.error

        glr._health_cache.clear()
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://www.amazon.com/dp/B0FVGBMH9B",
                code=405,
                msg="Method Not Allowed",
                hdrs=None,
                fp=None,
            )
            assert glr._is_url_healthy("https://www.amazon.com/dp/B0FVGBMH9B") is True

    def test_416_range_not_satisfiable_is_healthy(self):
        """Server rejecting our specific Range header doesn't invalidate
        the URL — 416 should count as 'reachable'."""
        glr._health_cache.clear()
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            resp = MagicMock()
            resp.status = 416
            resp.__enter__ = lambda self: resp
            resp.__exit__ = lambda *a: None
            mock_open.return_value = resp
            assert glr._is_url_healthy("https://example.com/file") is True


class TestUrlHealthyStillRejectsRealBrokenLinks:
    """The new allow-list MUST NOT accidentally accept genuinely broken URLs."""

    def setup_method(self):
        glr._health_cache.clear()

    def test_404_is_not_healthy(self):
        """404 = page doesn't exist = broken link."""
        import urllib.error

        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://www.amazon.com/dp/MISSING",
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=None,
            )
            assert glr._is_url_healthy("https://www.amazon.com/dp/MISSING") is False

    def test_500_is_not_healthy(self):
        """5xx = server error = treat as broken (don't route users to it)."""
        import urllib.error

        glr._health_cache.clear()
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://broken.example.com/x",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            )
            assert glr._is_url_healthy("https://broken.example.com/x") is False

    def test_403_is_not_healthy(self):
        """403 = the server refuses to give us the resource = caller can't
        reach it either. Don't route users to a 403."""
        import urllib.error

        glr._health_cache.clear()
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://denied.example.com/x",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            )
            assert glr._is_url_healthy("https://denied.example.com/x") is False

    def test_timeout_is_not_healthy(self):
        """Connection timeouts mean the URL is unreachable."""
        glr._health_cache.clear()
        with patch.object(glr.urllib.request, "urlopen") as mock_open:
            mock_open.side_effect = TimeoutError("timeout")
            assert glr._is_url_healthy("https://hangs.example.com/x") is False


class TestHealthyCodesAllowListContract:
    """Pin the exact contents of _HEALTHY_CODES."""

    def test_includes_405_for_amazon_com_pattern(self):
        assert 405 in glr._HEALTHY_CODES, (
            "amazon.com returns 405 to ranged GET on /dp/* — removing this "
            "value would re-break PR #277's geo→US routing for catalog matches"
        )

    def test_includes_416_for_range_rejecting_servers(self):
        assert 416 in glr._HEALTHY_CODES

    def test_includes_canonical_2xx_3xx(self):
        for code in (200, 206, 301, 302, 303, 307, 308):
            assert code in glr._HEALTHY_CODES

    def test_excludes_4xx_client_errors_other_than_405_416(self):
        for code in (400, 401, 403, 404):
            assert code not in glr._HEALTHY_CODES

    def test_excludes_5xx_server_errors(self):
        for code in (500, 502, 503, 504):
            assert code not in glr._HEALTHY_CODES
