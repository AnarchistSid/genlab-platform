"""Tests for AdaptGamingContent — platform rules enforcement.

One test per rule + edge cases. All deterministic, no live API calls.
"""

from unittest.mock import patch

from niches.gaming.stages.adapt_gaming_content import (
    AdaptGamingContent,
    _select_cta,
    adapt_story_content,
)

# Overrides matching config/platform_overrides.yaml
OVERRIDES = {
    "instagram": {
        "allowed_url_domains": [],
        "cta_phrases": [
            "Follow for daily gaming content 🎮",
            "Drop a 🔥 if this was insane",
            "Save this for the highlight reel 💾",
        ],
        "hashtag_min": 3,
        "hashtag_max": 5,
    },
    "x_twitter": {
        "link_reply_template": "🎮 Full clip + source: {url}",
        "safe_zone_padding_pct": 0.08,
    },
    "international_date": {
        "enabled": True,
        "format": "DD MMM YYYY",
    },
}

TEMPLATES = {
    "captions": {
        "hashtag_pool": [
            "#gaming",
            "#gamer",
            "#games",
            "#videogames",
            "#gamingcommunity",
        ],
        "cta_library": [
            "drop a 🎮 if you're playing this",
            "tag a friend who needs to see this",
        ],
    },
}


def _make_story(
    title="Test Game Update",
    source_url="https://example.com/story",
    content=None,
    aspect_ratio="",
    rendered_path=None,
):
    story = {
        "title": title,
        "source_url": source_url,
        "content": content or {},
        "media": {},
    }
    if aspect_ratio:
        story["media"]["aspect_ratio"] = aspect_ratio
    if rendered_path:
        story["media"]["rendered_path"] = rendered_path
    return story


class TestInstagramURLStripping:
    def test_instagram_url_stripped_from_caption(self):
        content = {
            "instagram": {
                "caption": "Check out this gameplay https://twitch.tv/streamer amazing stuff",
                "hashtags": ["#gaming", "#gamer", "#games"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert "https://" not in content["instagram"]["caption"]
        assert "twitch.tv" not in content["instagram"]["caption"]


class TestInstagramCTA:
    def test_instagram_cta_appended_when_missing(self):
        content = {
            "instagram": {
                "caption": "This game just changed everything",
                "hashtags": ["#gaming", "#gamer", "#games"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        caption = content["instagram"]["caption"]
        assert any(
            cta in caption for cta in OVERRIDES["instagram"]["cta_phrases"]
        )

    def test_instagram_cta_not_doubled_when_already_present(self):
        cta = OVERRIDES["instagram"]["cta_phrases"][0]
        content = {
            "instagram": {
                "caption": f"This game just changed everything\n\n{cta}",
                "hashtags": ["#gaming", "#gamer", "#games"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        caption = content["instagram"]["caption"]
        # CTA should appear exactly once
        assert caption.count(cta) == 1


class TestInstagramHashtags:
    def test_instagram_hashtag_count_padded_to_minimum(self):
        content = {
            "instagram": {
                "caption": "Short caption",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert len(content["instagram"]["hashtags"]) >= 3

    def test_instagram_hashtag_count_trimmed_to_maximum(self):
        content = {
            "instagram": {
                "caption": "Short caption",
                "hashtags": [
                    "#gaming", "#gamer", "#games",
                    "#videogames", "#gamingcommunity",
                    "#pcgaming", "#consolegaming",
                ],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert len(content["instagram"]["hashtags"]) <= 5

    def test_padding_does_not_duplicate_existing_hashtags(self):
        content = {
            "instagram": {
                "caption": "Short caption",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        hashtags = content["instagram"]["hashtags"]
        assert len(hashtags) == len(set(h.lower() for h in hashtags))


class TestXTwitterLinkInReply:
    def test_x_link_moved_to_first_reply(self):
        content = {
            "x_twitter": {
                "tweet": "This game is insane https://example.com/clip",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert "https://" not in content["x_twitter"]["tweet"]
        assert "first_reply" in content["x_twitter"]
        assert "https://example.com/clip" in content["x_twitter"]["first_reply"]
        assert "🎮" in content["x_twitter"]["first_reply"]

    def test_x_clean_caption_not_modified(self):
        content = {
            "x_twitter": {
                "tweet": "This game is insane no links here",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(
            content=content,
            source_url="https://example.com/story",
        )
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        # Tweet body should be unchanged (no URL was in it)
        assert "This game is insane no links here" in content["x_twitter"]["tweet"]
        # But source_url should still be in first_reply
        assert "first_reply" in content["x_twitter"]
        assert "https://example.com/story" in content["x_twitter"]["first_reply"]


class TestSafeZone:
    def test_safe_zone_flag_set_for_vertical_video(self):
        content = {
            "x_twitter": {
                "tweet": "Gaming clip of the day",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(
            content=content,
            aspect_ratio="9:16",
            rendered_path="/tmp/video.mp4",
        )
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert content["x_twitter"]["safe_zone_enforced"] is True
        assert content["x_twitter"]["safe_zone_padding_pct"] == 0.08

    def test_safe_zone_flag_not_set_for_landscape_video(self):
        content = {
            "x_twitter": {
                "tweet": "Gaming clip of the day",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(
            content=content,
            aspect_ratio="16:9",
            rendered_path="/tmp/video.mp4",
        )
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert "safe_zone_enforced" not in content["x_twitter"]

    def test_safe_zone_flag_not_set_without_rendered_path(self):
        content = {
            "x_twitter": {
                "tweet": "Gaming clip of the day",
                "hashtags": ["#gaming"],
            }
        }
        story = _make_story(
            content=content,
            aspect_ratio="9:16",
        )
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        assert "safe_zone_enforced" not in content["x_twitter"]


class TestInternationalDateFormat:
    def test_international_date_format_applied(self):
        content = {
            "instagram": {
                "caption": "Update dropped on 3/7/2026 and it's wild",
                "hashtags": ["#gaming", "#gamer", "#games"],
            }
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        caption = content["instagram"]["caption"]
        # US date 3/7/2026 should be converted to "March 7, 2026"
        assert "3/7/2026" not in caption
        assert "March 7, 2026" in caption


class TestExistingRulesNotBroken:
    def test_existing_rules_not_broken(self):
        """YouTube title and Facebook format are handled by WriteGamingContent.

        AdaptGamingContent does NOT modify YouTube or Facebook content,
        so those rules should be unaffected.
        """
        content = {
            "youtube": {
                "title": "Is This Game Worth It?",
                "description": "The latest update just dropped.",
            },
            "facebook": {
                "caption": "Big gaming update just dropped. What do you think?",
            },
            "instagram": {
                "caption": "This is wild",
                "hashtags": ["#gaming", "#gamer", "#games"],
            },
        }
        story = _make_story(content=content)
        adapt_story_content(content, story, OVERRIDES, TEMPLATES)

        # YouTube title should be completely untouched by adapt stage
        assert content["youtube"]["title"] == "Is This Game Worth It?"
        # Facebook caption should be untouched
        assert "Big gaming update" in content["facebook"]["caption"]


class TestDeterministicCTASelection:
    def test_deterministic_cta_selection(self):
        """Same story_key always produces the same CTA."""
        cta_phrases = OVERRIDES["instagram"]["cta_phrases"]

        cta_1 = _select_cta("https://example.com/story-1", cta_phrases)
        cta_2 = _select_cta("https://example.com/story-1", cta_phrases)
        cta_3 = _select_cta("https://example.com/story-1", cta_phrases)

        assert cta_1 == cta_2 == cta_3
        assert cta_1 in cta_phrases

    def test_different_stories_can_get_different_ctas(self):
        """Different story keys should produce varied CTAs over many inputs."""
        cta_phrases = OVERRIDES["instagram"]["cta_phrases"]
        seen = set()
        for i in range(100):
            cta = _select_cta(f"https://example.com/story-{i}", cta_phrases)
            seen.add(cta)
        # With 100 inputs and 3 CTAs, all should appear
        assert len(seen) == len(cta_phrases)


class TestStageExecute:
    def test_execute_adapts_stories(self):
        context = {
            "stories": [
                _make_story(content={
                    "instagram": {
                        "caption": "Test caption https://link.com/x for gaming",
                        "hashtags": ["#gaming"],
                    },
                    "x_twitter": {
                        "tweet": "Test tweet",
                        "hashtags": ["#gaming"],
                    },
                }),
                _make_story(content=None),  # no content — should be skipped
            ],
            "feature_flags": {},
            "run_stats": {},
        }

        with patch(
            "niches.gaming.stages.adapt_gaming_content._load_overrides",
            return_value=OVERRIDES,
        ), patch(
            "niches.gaming.stages.adapt_gaming_content._load_templates",
            return_value=TEMPLATES,
        ):
            stage = AdaptGamingContent()
            result = stage.execute(context)

        stats = result["run_stats"]["adapt"]
        assert stats["adapted"] == 1
        assert stats["skipped_no_content"] == 1

        # URL should be stripped from Instagram caption
        ig_caption = result["stories"][0]["content"]["instagram"]["caption"]
        assert "https://" not in ig_caption
