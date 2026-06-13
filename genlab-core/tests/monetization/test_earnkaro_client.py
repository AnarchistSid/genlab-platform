"""Pins for #34b — EarnKaro link converter HTTP client.

Most important invariants:
  1. **Graceful skip** on missing key, malformed input, HTTP failure,
     timeout, JSON decode error, unknown response shape. NEVER raises
     to the caller.
  2. **Response-shape tolerance** — try `data` → `url` → `deal` →
     `short_url` → `converted_url` keys in order; require the value to
     look like an ekaro.in URL before trusting it.
  3. **Disk cache hit avoids re-calling the API** — operator shouldn't
     pay the API roundtrip + rate-limit cost for a URL we've already
     converted in the last 30 days.
  4. **Force-refresh** bypasses cache.
  5. **Bearer header** + JSON content type on every request.
  6. **Cache key is deterministic + safe for disk_cache's validator**.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch, tmp_path):
    """Isolate the EarnKaro disk cache per-test so cross-test pollution
    can't cause spurious passes. Module-level `_cache` is rebound to a
    fresh tmp dir for each test."""
    from genlab_core.cache.disk_cache import Cache
    from genlab_core.monetization import earnkaro_client

    fresh = Cache(cache_dir=str(tmp_path / "earnkaro_cache"))
    monkeypatch.setattr(earnkaro_client, "_cache", fresh)
    yield


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    """Most tests want the key set; the missing-key test overrides."""
    monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-token-123")


def _mock_urlopen(json_body: dict | str | None = None, *, status: int = 200):
    """Build a context-manager-shaped mock for urllib.request.urlopen."""
    mock_cm = MagicMock()
    if isinstance(json_body, dict):
        payload = json.dumps(json_body).encode("utf-8")
    elif isinstance(json_body, str):
        payload = json_body.encode("utf-8")
    else:
        payload = b""
    mock_response = MagicMock()
    mock_response.read.return_value = payload
    mock_cm.__enter__.return_value = mock_response
    mock_cm.__exit__.return_value = False
    return mock_cm


class TestGracefulFailures:
    def test_empty_target_url_returns_empty(self):
        from genlab_core.monetization.earnkaro_client import convert_url
        assert convert_url("") == ""

    def test_non_http_target_url_returns_empty(self):
        from genlab_core.monetization.earnkaro_client import convert_url
        # Defensive: only http/https targets are valid for affiliate routing
        assert convert_url("ftp://example.com/file") == ""
        assert convert_url("javascript:alert(1)") == ""

    def test_missing_key_returns_empty_no_http_call(self, monkeypatch):
        """No key → return "" without making any network request."""
        monkeypatch.delenv("EARNKARO_CONVERT_KEY", raising=False)
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen"
        ) as mock_open:
            assert convert_url("https://merchant.example.com/p/42") == ""
            mock_open.assert_not_called()

    def test_http_error_returns_empty(self):
        """5xx / 401 / 429 from EarnKaro → return "" (graceful skip)."""
        from genlab_core.monetization.earnkaro_client import convert_url

        err = urllib.error.HTTPError(
            url="x", code=500, msg="err", hdrs=None, fp=io.BytesIO(b"")
        )
        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            side_effect=err,
        ):
            assert convert_url("https://merch.example.com/p") == ""

    def test_url_error_returns_empty(self):
        """DNS failure / connection refused → return "" (graceful skip)."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("no route to host"),
        ):
            assert convert_url("https://merch.example.com/p") == ""

    def test_timeout_returns_empty(self):
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            side_effect=TimeoutError(),
        ):
            assert convert_url("https://merch.example.com/p") == ""

    def test_invalid_json_response_returns_empty(self):
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen(json_body="not json at all"),
        ):
            assert convert_url("https://merch.example.com/p") == ""

    def test_response_with_no_recognized_key_returns_empty(self):
        """Unknown response shape → "". Operator sees a warning log
        with the keys that WERE present so the parser can be extended."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen({"status": "ok", "result": "ignored"}),
        ):
            assert convert_url("https://merch.example.com/p") == ""

    def test_response_value_must_look_like_ekaro_url(self):
        """A recognized key whose value ISN'T an ekaro URL must be rejected.
        Prevents accidental success on a redirect-error page."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen({"data": "https://google.com"}),
        ):
            assert convert_url("https://merch.example.com/p") == ""


class TestSuccessfulConversion:
    @pytest.mark.parametrize(
        "key",
        ["data", "url", "deal", "short_url", "converted_url"],
    )
    def test_recognized_response_keys(self, key):
        """Each documented key shape returns the short URL."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen({key: "https://ekaro.in/abc123"}),
        ):
            assert convert_url("https://merch.example.com/p") == "https://ekaro.in/abc123"

    def test_request_carries_bearer_header_and_json_content_type(self):
        """Pin the request shape so a future refactor doesn't drop auth."""
        from genlab_core.monetization.earnkaro_client import convert_url

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return _mock_urlopen({"data": "https://ekaro.in/x"})

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            convert_url("https://merch.example.com/widget")

        assert captured["url"] == "https://ekaro.in/api/converter/public"
        assert captured["method"] == "POST"
        # Header capitalization varies by HTTP lib; check case-insensitively
        header_lower = {k.lower(): v for k, v in captured["headers"].items()}
        assert header_lower["authorization"] == "Bearer test-token-123"
        assert "json" in header_lower.get("content-type", "")
        # Body carries the target URL
        body = json.loads(captured["data"].decode("utf-8"))
        assert body["deal"] == "https://merch.example.com/widget"


class TestCaching:
    def test_repeat_call_uses_cache_no_second_http(self):
        """Cache hit on second call — operator doesn't pay API cost twice."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen({"data": "https://ekaro.in/cached"}),
        ) as mock_open:
            url1 = convert_url("https://merch.example.com/p")
            url2 = convert_url("https://merch.example.com/p")

        assert url1 == "https://ekaro.in/cached"
        assert url2 == "https://ekaro.in/cached"
        mock_open.assert_called_once()  # second call hit the cache

    def test_force_refresh_bypasses_cache(self):
        """`force_refresh=True` re-hits the API even when cached."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            return_value=_mock_urlopen({"data": "https://ekaro.in/fresh"}),
        ) as mock_open:
            convert_url("https://merch.example.com/p")  # populate cache
            convert_url("https://merch.example.com/p", force_refresh=True)

        assert mock_open.call_count == 2

    def test_failed_conversions_not_cached(self):
        """If the first call returned "" (failure), the second call
        should try again — don't lock in a broken state."""
        from genlab_core.monetization.earnkaro_client import convert_url

        with patch(
            "genlab_core.monetization.earnkaro_client.urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("transient"),
                _mock_urlopen({"data": "https://ekaro.in/now-works"}),
            ],
        ):
            first = convert_url("https://merch.example.com/p")
            second = convert_url("https://merch.example.com/p")

        assert first == ""
        assert second == "https://ekaro.in/now-works"


class TestAdapterIntegration:
    """The EarnKaroAdapter's generate_url should now call convert_url."""

    def test_adapter_calls_client_when_key_and_target_url_set(self):
        from genlab_core.monetization.network_registry import EarnKaroAdapter

        adapter = EarnKaroAdapter()
        # Mock the import-site path — that's where the adapter looks it up
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            return_value="https://ekaro.in/via-adapter",
        ) as mock_convert:
            url = adapter.generate_url(
                "any_product_id",
                target_url="https://amazon.in/dp/B0XYZ",
            )
            assert url == "https://ekaro.in/via-adapter"
            mock_convert.assert_called_once_with("https://amazon.in/dp/B0XYZ")

    def test_adapter_skips_when_target_url_missing(self):
        """Even with key set, no target URL → "" (don't call the API
        with an empty string)."""
        from genlab_core.monetization.network_registry import EarnKaroAdapter

        adapter = EarnKaroAdapter()
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url"
        ) as mock_convert:
            assert adapter.generate_url("p") == ""
            mock_convert.assert_not_called()
