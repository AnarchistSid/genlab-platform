# genlab-core/tests/platforms/test_dispatcher.py
"""Tests for dispatch_many — concurrent platform dispatch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from genlab_core.platforms.models import PublishPayload, PublishResult


def _make_payload(platform: str) -> PublishPayload:
    return PublishPayload(
        caption="Test",
        media_paths=[Path("/tmp/v.mp4")],
        media_type="video",
        hashtags=[],
        hook="",
        niche_id="ai_creators",
    )


def test_dispatch_many_success():
    from genlab_core.platforms.dispatcher import dispatch_many

    mock_client = MagicMock()
    mock_client.publish.return_value = PublishResult(
        platform="instagram", success=True, post_id="123"
    )

    with patch("genlab_core.platforms.dispatcher.get_client", return_value=mock_client):
        results = dispatch_many([
            ("instagram", _make_payload("instagram")),
            ("youtube", _make_payload("youtube")),
        ])

    assert len(results) == 2
    assert results["instagram"].success is True
    assert results["youtube"].success is True


def test_dispatch_many_partial_failure():
    """One platform crashes — others still return results."""
    from genlab_core.platforms.dispatcher import dispatch_many

    def mock_get_client(platform_id):
        client = MagicMock()
        if platform_id == "instagram":
            client.publish.return_value = PublishResult(
                platform="instagram", success=True, post_id="123"
            )
        else:
            client.publish.side_effect = RuntimeError("API down")
        return client

    with patch("genlab_core.platforms.dispatcher.get_client", side_effect=mock_get_client):
        results = dispatch_many([
            ("instagram", _make_payload("instagram")),
            ("youtube", _make_payload("youtube")),
        ])

    assert results["instagram"].success is True
    assert results["youtube"].success is False
    assert "API down" in results["youtube"].error


def test_dispatch_many_empty_list():
    from genlab_core.platforms.dispatcher import dispatch_many

    results = dispatch_many([])
    assert results == {}
