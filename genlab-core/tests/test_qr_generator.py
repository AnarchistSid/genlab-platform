"""Tests for genlab_core.monetization.qr_generator."""

import tempfile
import unittest
from pathlib import Path


class TestQRGenerator(unittest.TestCase):
    def test_import(self):
        from genlab_core.monetization.qr_generator import generate_qr_code

        assert callable(generate_qr_code)

    def test_generate_returns_path_or_none(self):
        """generate_qr_code should return a path string or None."""
        from genlab_core.monetization.qr_generator import generate_qr_code

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_qr_code(
                channel_slug="testchannel",
                output_dir=Path(tmpdir),
            )
            # Returns None if qrcode lib not installed, or a path if it is
            if result is not None:
                assert Path(result).exists()


if __name__ == "__main__":
    unittest.main()
