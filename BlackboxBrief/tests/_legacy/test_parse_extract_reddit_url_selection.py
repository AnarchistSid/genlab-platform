"""Tests for reddit outbound/media URL selection in parse_extract."""

from execution.parse_extract import _select_primary_url, parse_entry


def test_select_primary_url_prefers_video_hint_for_reddit():
    entry_link = "https://www.reddit.com/r/aivideo/comments/abc123/demo_post/"
    summary = (
        '<a href="https://www.reddit.com/r/aivideo/comments/abc123/demo_post/">post</a>'
        '<span><a href="https://v.redd.it/xyz987">[link]</a></span>'
        '<span><a href="https://www.reddit.com/r/aivideo/comments/abc123/demo_post/">[comments]</a></span>'
    )

    selected = _select_primary_url(entry_link, summary)

    assert selected["primary_url"] == "https://v.redd.it/xyz987"
    assert selected["source_post_url"] == entry_link


def test_select_primary_url_keeps_non_reddit_as_primary():
    entry_link = "https://example.com/post/1"
    summary = '<a href="https://cdn.example.com/video.mp4">video</a>'

    selected = _select_primary_url(entry_link, summary)

    assert selected["primary_url"] == entry_link
    assert selected["source_post_url"] == ""


def test_parse_entry_preserves_source_post_url_and_media_hints():
    entry = {
        "title": "Creator clip",
        "link": "https://www.reddit.com/r/aivideo/comments/abc123/demo_post/",
        "summary": (
            '<a href="https://www.reddit.com/r/aivideo/comments/abc123/demo_post/">post</a>'
            '<span><a href="https://v.redd.it/xyz987">[link]</a></span>'
        ),
        "published": "2026-02-22T12:00:00+00:00",
        "author": "/u/tester",
    }

    parsed = parse_entry(entry, source_priority=0.9)

    assert parsed["url"] == "https://v.redd.it/xyz987"
    assert parsed["source_post_url"] == "https://www.reddit.com/r/aivideo/comments/abc123/demo_post"
    assert "https://v.redd.it/xyz987" in parsed["media_hint_urls"]
