"""Tests for platform data models."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def test_publish_payload_creation():
    from genlab_core.platforms.models import PublishPayload, YouTubeSpecific

    payload = PublishPayload(
        caption="Test caption",
        media_paths=[Path("/tmp/video.mp4")],
        media_type="video",
        hashtags=["#test"],
        hook="Breaking news",
        niche_id="ai_news",
        platform_specific=YouTubeSpecific(shorts_title="Test Short"),
    )
    assert payload.caption == "Test caption"
    assert payload.platform_specific.shorts_title == "Test Short"


def test_publish_payload_no_platform_specific():
    from genlab_core.platforms.models import PublishPayload

    payload = PublishPayload(
        caption="Test",
        media_paths=[],
        media_type="text",
        hashtags=[],
        hook="",
        niche_id="gaming",
    )
    assert payload.platform_specific is None


def test_publish_result_backward_compat():
    from genlab_core.platforms.models import PublishResult

    result = PublishResult(platform="instagram", success=True, post_id="123")
    assert result.metadata == {}  # alias for raw_response
    assert result.post_url == ""
    assert result.error == ""


def test_publish_result_metadata_alias():
    from genlab_core.platforms.models import PublishResult

    result = PublishResult(
        platform="youtube",
        success=True,
        post_id="abc",
        raw_response={"video_url": "https://..."},
    )
    assert result.metadata == {"video_url": "https://..."}
    assert result.metadata is result.raw_response


def test_platform_metrics_defaults():
    from genlab_core.platforms.models import PlatformMetrics

    m = PlatformMetrics()
    assert m.views == 0
    assert m.likes == 0
    assert m.extra == {}


def test_token_status_fields():
    from genlab_core.platforms.models import TokenStatus

    ts = TokenStatus(
        valid=True,
        platform="instagram",
        expires_at=None,
        needs_refresh=False,
        message="EAA token is permanent",
    )
    assert ts.valid is True
    assert ts.details == {}


def test_youtube_specific_defaults():
    from genlab_core.platforms.models import YouTubeSpecific

    yt = YouTubeSpecific()
    assert yt.category_id == "28"
    assert yt.privacy_status == "public"
    assert yt.tags == []


def test_twitter_specific_defaults():
    from genlab_core.platforms.models import TwitterSpecific

    tw = TwitterSpecific()
    assert tw.routing == "single"
    assert tw.thread_tweets == []
