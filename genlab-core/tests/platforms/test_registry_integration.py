"""Verify all registered platform clients can be instantiated."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.platforms.registry import get_client, list_platforms

MOCK_ENV = {
    "META_ACCESS_TOKEN": "EAA_TEST",
    "META_IG_USER_ID": "123",
    "META_FB_PAGE_ID": "456",
    "YOUTUBE_CLIENT_ID": "test",
    "YOUTUBE_CLIENT_SECRET": "test",
    "YOUTUBE_REFRESH_TOKEN": "test",
    "X_API_KEY": "test",
    "X_API_SECRET": "test",
    "X_ACCESS_TOKEN": "test",
    "X_ACCESS_SECRET": "test",
    "THREADS_ACCESS_TOKEN": "test",
    "THREADS_USER_ID": "test",
}


@pytest.mark.parametrize("platform_id", list_platforms())
def test_get_client_returns_publisher(platform_id):
    from genlab_core.platforms.protocols import Publisher

    with patch.dict("os.environ", MOCK_ENV):
        client = get_client(platform_id)
    assert isinstance(client, Publisher)
    assert client.platform_id == platform_id
