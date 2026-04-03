"""Tests for _local_path_to_media_url file-existence validation (FIX 1)."""

import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_project_root(tmp_path):
    """Provide a temp directory as PROJECT_ROOT."""
    with patch("server.api.blueprints.PROJECT_ROOT", tmp_path):
        yield tmp_path


class TestLocalPathToMediaUrl:
    def test_existing_file_returns_url(self, mock_project_root):
        from server.api.blueprints import _local_path_to_media_url

        media = mock_project_root / ".tmp" / "renders" / "video.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"\x00" * 100)

        result = _local_path_to_media_url(str(media))
        assert result == "/api/media/.tmp/renders/video.mp4"

    def test_missing_file_returns_none(self, mock_project_root):
        from server.api.blueprints import _local_path_to_media_url

        missing = mock_project_root / ".tmp" / "renders" / "gone.mp4"
        result = _local_path_to_media_url(str(missing))
        assert result is None

    def test_empty_file_returns_none(self, mock_project_root):
        from server.api.blueprints import _local_path_to_media_url

        empty = mock_project_root / ".tmp" / "renders" / "empty.mp4"
        empty.parent.mkdir(parents=True)
        empty.write_bytes(b"")

        result = _local_path_to_media_url(str(empty))
        assert result is None

    def test_outside_project_root_returns_none(self, mock_project_root):
        from server.api.blueprints import _local_path_to_media_url

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00" * 100)
            outside_path = f.name

        try:
            result = _local_path_to_media_url(outside_path)
            assert result is None
        finally:
            os.unlink(outside_path)

    def test_png_file_returns_url(self, mock_project_root):
        from server.api.blueprints import _local_path_to_media_url

        img = mock_project_root / ".tmp" / "renders" / "slide_01.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        result = _local_path_to_media_url(str(img))
        assert result == "/api/media/.tmp/renders/slide_01.png"

    def test_multiple_paths_mixed_existence(self, mock_project_root):
        """Simulates _transform_media filtering: only existing files produce URLs."""
        from server.api.blueprints import _local_path_to_media_url

        renders = mock_project_root / ".tmp" / "renders"
        renders.mkdir(parents=True)

        (renders / "exists.mp4").write_bytes(b"\x00" * 100)
        # "missing.mp4" not created

        paths = [str(renders / "exists.mp4"), str(renders / "missing.mp4")]
        urls = [_local_path_to_media_url(p) for p in paths]

        assert urls[0] is not None
        assert urls[1] is None
