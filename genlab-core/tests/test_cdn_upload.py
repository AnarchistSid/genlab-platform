"""Tests for genlab_core.platforms.cdn_upload."""

import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch


class TestUploadToCdn(unittest.TestCase):
    def test_nonexistent_file_returns_none(self):
        from genlab_core.platforms.cdn_upload import upload_to_cdn

        result = upload_to_cdn("/nonexistent/file.mp4")
        assert result is None

    @patch("genlab_core.platforms.cdn_upload._serve_via_tunnel")
    def test_tunnel_success(self, mock_tunnel):
        from genlab_core.platforms.cdn_upload import upload_to_cdn

        mock_tunnel.return_value = "https://tunnel.example.com/media/test.mp4"

        # Create a temp file
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"x" * 1024)
            tmp_path = f.name

        try:
            result = upload_to_cdn(tmp_path)
            assert result == "https://tunnel.example.com/media/test.mp4"
            mock_tunnel.assert_called_once()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @patch("genlab_core.platforms.cdn_upload._serve_via_tunnel")
    @patch("genlab_core.platforms.cdn_upload._upload_to_litterbox")
    @patch("genlab_core.platforms.cdn_upload._upload_to_tmpfiles")
    def test_all_methods_fail_returns_none(self, mock_tmp, mock_litter, mock_tunnel):
        from genlab_core.platforms.cdn_upload import upload_to_cdn

        mock_tunnel.return_value = None
        mock_litter.return_value = None
        mock_tmp.return_value = None

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"x" * 1024)
            tmp_path = f.name

        try:
            result = upload_to_cdn(tmp_path)
            assert result is None
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestTmpfilesDirectUrlResolver(unittest.TestCase):
    """Pin the 2026-07-19 tmpfiles.org URL-scheme fix.

    Site changed the direct-download URL to require a
    ``{TIMESTAMP}.{HASH}`` segment. The wrapper page HTML now has to
    be scraped to extract it. Prior code did a naive string replace
    from ``http://tmpfiles.org/`` to ``https://tmpfiles.org/dl/``,
    which now 302s back to the wrapper page → HEAD returns text/html
    → Meta preflight rejects.
    """

    @patch("genlab_core.platforms.cdn_upload.requests")
    def test_extracts_dl_url_with_timestamp_hash_segment(self, mock_requests):
        from genlab_core.platforms.cdn_upload import _resolve_tmpfiles_direct_url

        page_html = (
            '<html><body>'
            '<a href="https://tmpfiles.org/dl/1784446211.0a98261fb7675ef5/'
            'wBw9uC8oVKvX/c733fab790f73cf6_reel_instagram.mp4">Download</a>'
            '</body></html>'
        )

        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = page_html
        mock_requests.get.return_value = mock_resp

        result = _resolve_tmpfiles_direct_url(
            "http://tmpfiles.org/wBw9uC8oVKvX/c733fab790f73cf6_reel_instagram.mp4"
        )
        assert result == (
            "https://tmpfiles.org/dl/1784446211.0a98261fb7675ef5/"
            "wBw9uC8oVKvX/c733fab790f73cf6_reel_instagram.mp4"
        )

    @patch("genlab_core.platforms.cdn_upload.requests")
    def test_returns_none_when_wrapper_missing_dl_link(self, mock_requests):
        from genlab_core.platforms.cdn_upload import _resolve_tmpfiles_direct_url

        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>No download link here</body></html>"
        mock_requests.get.return_value = mock_resp

        result = _resolve_tmpfiles_direct_url("http://tmpfiles.org/token/file.mp4")
        assert result is None

    @patch("genlab_core.platforms.cdn_upload.requests")
    def test_returns_none_when_wrapper_fetch_fails(self, mock_requests):
        from genlab_core.platforms.cdn_upload import _resolve_tmpfiles_direct_url

        mock_requests.get.side_effect = Exception("network")

        result = _resolve_tmpfiles_direct_url("http://tmpfiles.org/token/file.mp4")
        assert result is None

    @patch("genlab_core.platforms.cdn_upload.requests")
    def test_returns_none_on_empty_page_url(self, mock_requests):
        from genlab_core.platforms.cdn_upload import _resolve_tmpfiles_direct_url

        result = _resolve_tmpfiles_direct_url("")
        assert result is None
        mock_requests.get.assert_not_called()

    @patch("genlab_core.platforms.cdn_upload.requests")
    def test_naive_dl_prefix_stripped_before_wrapper_fetch(self, mock_requests):
        """If caller already tried the (now-broken) naive `/dl/` prefix,
        strip it so we fetch the actual wrapper page, not a 302 loop."""
        from genlab_core.platforms.cdn_upload import _resolve_tmpfiles_direct_url

        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            '<a href="https://tmpfiles.org/dl/1784446211.abc/wBw/file.mp4">'
        )
        mock_requests.get.return_value = mock_resp

        _resolve_tmpfiles_direct_url("https://tmpfiles.org/dl/wBw/file.mp4")

        # The GET should have been called with the un-prefixed wrapper URL.
        called_url = mock_requests.get.call_args[0][0]
        assert "/dl/wBw" not in called_url, called_url
        assert called_url == "https://tmpfiles.org/wBw/file.mp4"


if __name__ == "__main__":
    unittest.main()
