"""TikTok client stub tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genlab_core.platforms.models import PublishPayload


def test_tiktok_disabled_by_default():
    from genlab_core.platforms.tiktok import TikTokClient

    client = TikTokClient()
    payload = PublishPayload(
        caption="Test", media_paths=[Path("/tmp/v.mp4")],
        media_type="video", hashtags=[], hook="", niche_id="gaming",
    )
    result = client.publish(payload)
    assert result.success is False
    assert "disabled" in result.error.lower() or "audit" in result.error.lower()


def test_tiktok_enabled_with_env():
    from genlab_core.platforms.tiktok import TikTokClient

    with patch.dict("os.environ", {"TIKTOK_AUDIT_APPROVED": "true"}):
        client = TikTokClient()
    assert client.platform_id == "tiktok"
