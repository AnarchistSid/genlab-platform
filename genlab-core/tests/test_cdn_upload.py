"""Tests for genlab_core.platforms.cdn_upload."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
