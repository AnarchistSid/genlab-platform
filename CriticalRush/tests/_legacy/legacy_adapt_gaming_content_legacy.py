"""Tests for gaming-specific platform adaptation."""

from __future__ import annotations

from niches.gaming.stages.adapt_gaming_content import (
    _enforce_hashtag_count,
    _select_cta,
    adapt_story_content,
)


class TestEnforceHashtagCount:
    def test_pads_from_pool(self):
        caption, hashtags = _enforce_hashtag_count(
            "test",
            ["#gaming"],
            ["#esports", "#twitch", "#gamer"],
            3,
            5,
        )
        assert len(hashtags) >= 3

    def test_trims_excess(self):
        tags = [f"#{i}" for i in range(10)]
        caption, hashtags = _enforce_hashtag_count("test", tags, [], 3, 5)
        assert len(hashtags) == 5

    def test_no_duplicates_when_padding(self):
        caption, hashtags = _enforce_hashtag_count(
            "test",
            ["#gaming", "#esports"],
            ["#gaming", "#twitch", "#gamer"],
            3,
            5,
        )
        # #gaming already exists, shouldn't be added again
        assert hashtags.count("#gaming") == 1

    def test_no_change_within_range(self):
        caption, hashtags = _enforce_hashtag_count(
            "test",
            ["#a", "#b", "#c", "#d"],
            [],
            3,
            5,
        )
        assert len(hashtags) == 4

    def test_pads_to_exact_min(self):
        caption, hashtags = _enforce_hashtag_count(
            "test",
            ["#a"],
            ["#b", "#c"],
            3,
            5,
        )
        assert len(hashtags) == 3

    def test_empty_pool_no_padding(self):
        caption, hashtags = _enforce_hashtag_count(
            "test",
            ["#solo"],
            [],
            3,
            5,
        )
        # Can't pad without pool — stays at 1
        assert len(hashtags) == 1

    def test_caption_returned_unchanged(self):
        caption, hashtags = _enforce_hashtag_count(
            "my caption text",
            ["#a"],
            ["#b", "#c"],
            3,
            5,
        )
        assert caption == "my caption text"


class TestSelectCta:
    def test_deterministic(self):
        ctas = ["CTA 1", "CTA 2", "CTA 3"]
        result1 = _select_cta("same_key", ctas)
        result2 = _select_cta("same_key", ctas)
        assert result1 == result2

    def test_different_keys_can_differ(self):
        ctas = ["CTA 1", "CTA 2", "CTA 3", "CTA 4", "CTA 5"]
        results = {_select_cta(f"key_{i}", ctas) for i in range(100)}
        assert len(results) > 1  # Different keys should produce different CTAs

    def test_returns_valid_cta(self):
        ctas = ["Alpha", "Beta", "Gamma"]
        result = _select_cta("any_key", ctas)
        assert result in ctas


class TestAdaptStoryContent:
    def test_instagram_url_stripped(self):
        content = {
            "hook": "Big news",
            "instagram": {
                "caption": "Check https://example.com out",
                "hashtags": ["#gaming", "#news", "#tech"],
            },
        }
        result = adapt_story_content(content, {"source_url": ""}, {}, {})
        assert "https://example.com" not in result["instagram"]["caption"]

    def test_twitter_link_moved(self):
        content = {
            "hook": "Breaking",
            "x_twitter": {"tweet": "News https://source.com here"},
        }
        result = adapt_story_content(
            content,
            {"source_url": "https://source.com"},
            {},
            {},
        )
        assert "https://source.com" not in result["x_twitter"]["tweet"]
        assert result["x_twitter"].get("first_reply")

    def test_twitter_first_reply_uses_template(self):
        overrides = {
            "x_twitter": {"link_reply_template": "Read more: {url}"},
        }
        content = {
            "hook": "Update",
            "x_twitter": {"tweet": "Big https://link.com update"},
        }
        result = adapt_story_content(
            content,
            {"source_url": "https://link.com"},
            overrides,
            {},
        )
        assert result["x_twitter"]["first_reply"] == "Read more: https://link.com"

    def test_facebook_short_caption_gets_engagement(self):
        content = {
            "hook": "Update",
            "facebook": {"caption": "Short post."},
        }
        result = adapt_story_content(content, {}, {}, {})
        assert "What do you think" in result["facebook"]["caption"]

    def test_mutates_in_place_and_returns(self):
        content = {
            "hook": "Test",
            "instagram": {
                "caption": "Hello",
                "hashtags": ["#a", "#b", "#c"],
            },
        }
        result = adapt_story_content(content, {}, {}, {})
        assert result is content  # Same object

    def test_missing_platform_sections_no_error(self):
        content = {"hook": "Nothing else"}
        result = adapt_story_content(content, {}, {}, {})
        assert result == {"hook": "Nothing else"}

    def test_safe_zone_set_for_vertical_video(self):
        content = {
            "hook": "Clip",
            "x_twitter": {"tweet": "Check this clip"},
        }
        story = {
            "source_url": "",
            "media": {
                "aspect_ratio": "9:16",
                "rendered_path": "/tmp/video.mp4",
            },
        }
        result = adapt_story_content(content, story, {}, {})
        assert result["x_twitter"]["safe_zone_enforced"] is True
        assert result["x_twitter"]["safe_zone_padding_pct"] == 0.08
