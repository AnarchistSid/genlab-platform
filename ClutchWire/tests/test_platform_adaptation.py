"""Tests for ClutchWire platform adaptation — X link rule."""

import pytest
from cw_strategies.platform_adaptation import SportPlatformAdaptationStrategy


@pytest.fixture
def strategy():
    return SportPlatformAdaptationStrategy()


class TestXTwitterLinkRule:
    """X/Twitter: links must go in first_reply, never in main tweet."""

    def test_url_moved_to_first_reply(self, strategy):
        content = {
            "x_twitter": {
                "tweet": "Amazing play https://example.com/clip check this out",
            }
        }
        story = {"content": content, "source_url": "https://source.com"}
        strategy._adapt_x_twitter(content, story)

        assert "https://" not in content["x_twitter"]["tweet"]
        assert "first_reply" in content["x_twitter"]
        assert "https://example.com/clip" in content["x_twitter"]["first_reply"]

    def test_source_url_in_first_reply_when_no_inline_urls(self, strategy):
        content = {
            "x_twitter": {"tweet": "What a clutch moment"}
        }
        story = {"content": content, "source_url": "https://espn.com/story"}
        strategy._adapt_x_twitter(content, story)

        assert content["x_twitter"]["first_reply"] == "https://espn.com/story"

    def test_tweet_text_preserved_without_url(self, strategy):
        content = {
            "x_twitter": {"tweet": "Nobody saw that coming https://clip.com/x"}
        }
        story = {"content": content}
        strategy._adapt_x_twitter(content, story)

        assert "Nobody saw that coming" in content["x_twitter"]["tweet"]
        assert "https://" not in content["x_twitter"]["tweet"]

    def test_multiple_urls_all_moved(self, strategy):
        content = {
            "x_twitter": {
                "tweet": "Check https://a.com and https://b.com"
            }
        }
        story = {"content": content}
        strategy._adapt_x_twitter(content, story)

        # URLs removed from tweet body
        assert "https://" not in content["x_twitter"]["tweet"]
        # At least one URL in first_reply
        reply = content["x_twitter"].get("first_reply", "")
        assert "https://a.com" in reply


class TestInstagramUrlStrip:
    def test_urls_stripped_from_instagram_caption(self, strategy):
        content = {
            "instagram": {"caption": "Amazing play https://link.com follow for more"}
        }
        story = {"content": content}
        strategy._adapt_instagram(content, story)
        assert "https://" not in content["instagram"]["caption"]


class TestExecute:
    def test_execute_adapts_all_stories(self, strategy):
        ctx = {
            "stories": [
                {
                    "content": {
                        "x_twitter": {"tweet": "Test https://url.com"},
                        "instagram": {"caption": "Test caption"},
                    },
                    "source_url": "https://source.com",
                },
                {
                    "content": {
                        "x_twitter": {"tweet": "Clean tweet"},
                    },
                    "source_url": "https://source2.com",
                },
            ]
        }
        result = strategy.execute(ctx)
        assert result["run_stats"]["adapt"]["adapted"] == 2
