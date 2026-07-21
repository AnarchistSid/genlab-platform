"""Pin the 2026-07-17 Layer 1 batch 2 platform-publisher fixes.

## What broke pre-fix

**IG**: Container-creation POST to Meta's `/{ig_user_id}/media` would
succeed even when our CDN URL was 404 / expired / wrong-content-type.
Meta polls the URL server-side and eventually 2207077-fails after
30-60s of async processing. Wasted budget + generic error string.

**YT**: `#Shorts` tag was appended to TITLE. YouTube's Shorts
algorithmic classifier weights DESCRIPTION-first-line tokens for
short-form detection much more heavily than title tokens. Titles
also got polluted with algorithmic metadata. Additionally,
ai_creators used category 28 (Science & Tech) which gets weak
Shorts recommendation-feed push — Entertainment (24) is what viral
tech Shorts channels use (MKBHD, MrWhoseTheBoss, etc.).

## Fix contract (this test locks it)

- IG `_preflight_video_url` returns None on 200 + video/* Content-Type
- IG `_preflight_video_url` returns error string on 404 / other
- YT description STARTS with `#Shorts` (first line)
- YT title does NOT contain `#Shorts`
- YT `_NICHE_CATEGORIES["ai_creators"] == "24"` (Entertainment)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_ig_preflight_accepts_video_content_type() -> None:
    from genlab_core.platforms.instagram import InstagramClient

    client = InstagramClient.__new__(InstagramClient)
    client._log = MagicMock()

    mock_resp = MagicMock(status_code=200, headers={"Content-Type": "video/mp4"})
    with patch("genlab_core.platforms.instagram._META_SESSION.head", return_value=mock_resp):
        result = client._preflight_video_url("https://cdn.example.com/reel.mp4")
    assert result is None, (
        f"200 + video/mp4 should preflight OK, got: {result!r}"
    )


def test_ig_preflight_accepts_octet_stream() -> None:
    """Some CDNs serve application/octet-stream for videos; still valid."""
    from genlab_core.platforms.instagram import InstagramClient

    client = InstagramClient.__new__(InstagramClient)
    client._log = MagicMock()

    mock_resp = MagicMock(
        status_code=200, headers={"Content-Type": "application/octet-stream"}
    )
    with patch("genlab_core.platforms.instagram._META_SESSION.head", return_value=mock_resp):
        result = client._preflight_video_url("https://cdn.example.com/reel.mp4")
    assert result is None


def test_ig_preflight_rejects_404() -> None:
    from genlab_core.platforms.instagram import InstagramClient

    client = InstagramClient.__new__(InstagramClient)
    client._log = MagicMock()

    mock_resp = MagicMock(status_code=404, headers={})
    with patch("genlab_core.platforms.instagram._META_SESSION.head", return_value=mock_resp):
        result = client._preflight_video_url("https://cdn.example.com/dead.mp4")
    assert result is not None
    assert "404" in result


def test_ig_preflight_rejects_wrong_content_type() -> None:
    """HTML CDN error page served with 200 must not fool preflight."""
    from genlab_core.platforms.instagram import InstagramClient

    client = InstagramClient.__new__(InstagramClient)
    client._log = MagicMock()

    mock_resp = MagicMock(
        status_code=200, headers={"Content-Type": "text/html; charset=utf-8"}
    )
    with patch("genlab_core.platforms.instagram._META_SESSION.head", return_value=mock_resp):
        result = client._preflight_video_url("https://cdn.example.com/error-page.html")
    assert result is not None
    assert "Content-Type" in result


def test_ig_preflight_handles_network_exception() -> None:
    from genlab_core.platforms.instagram import InstagramClient

    client = InstagramClient.__new__(InstagramClient)
    client._log = MagicMock()

    with patch(
        "genlab_core.platforms.instagram._META_SESSION.head",
        side_effect=ConnectionError("DNS resolution failed"),
    ):
        result = client._preflight_video_url("https://dead-cdn.example.com/reel.mp4")
    assert result is not None
    assert "ConnectionError" in result or "DNS" in result


def test_yt_ai_creators_category_is_entertainment() -> None:
    """Category 24 (Entertainment) drives Shorts discovery for a 4-sub
    ai_creators channel. Category 28 (Science) is search-optimized and
    gets weak recommendation-feed push."""
    import inspect

    from genlab_core.platforms import youtube

    src = inspect.getsource(youtube.YouTubeClient.publish)
    assert '"ai_creators": "24"' in src, (
        "ai_creators must map to category 24 (Entertainment) — audit "
        "round 4 finding. If reverting to 28 (Science & Tech), first "
        "prove Shorts discovery > search for a cold-start channel."
    )


def test_yt_shorts_tag_in_description_not_title() -> None:
    """#Shorts in DESCRIPTION first line gets much stronger algo signal
    than #Shorts appended to TITLE. Post-audit change 2026-07-17."""
    import inspect

    from genlab_core.platforms import youtube

    src = inspect.getsource(youtube.YouTubeClient.publish)

    # #Shorts must be in the description_parts list (as the first element)
    assert 'description_parts = ["#Shorts"]' in src, (
        "#Shorts must be the first line of description for the Shorts "
        "algo classifier. See audit round 4."
    )

    # Title should NOT append #Shorts anymore
    assert 'title = title[:92] + " #Shorts"' not in src, (
        "Old title-append pattern is back — #Shorts in title is a much "
        "weaker algorithmic signal than in description first line."
    )
