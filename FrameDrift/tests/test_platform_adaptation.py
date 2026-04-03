"""Tests for FrameDrift platform adaptation — X link rule."""

import pytest
from fd_strategies.platform_adaptation import AnimePlatformAdaptationStrategy


@pytest.fixture
def strategy():
    return AnimePlatformAdaptationStrategy()


class TestXTwitterLinkRule:
    def test_url_moved_to_first_reply(self, strategy):
        content = {
            "x_twitter": {
                "tweet": "This drop https://hypebeast.com/drop is fire",
            }
        }
        story = {"content": content, "source_url": "https://source.com"}
        strategy._adapt_x_twitter(content, story)

        assert "https://" not in content["x_twitter"]["tweet"]
        assert "first_reply" in content["x_twitter"]
        assert "https://hypebeast.com/drop" in content["x_twitter"]["first_reply"]

    def test_source_url_in_first_reply_when_no_inline_urls(self, strategy):
        content = {
            "x_twitter": {"tweet": "This drop is certified heat"}
        }
        story = {"content": content, "source_url": "https://hypebeast.com/news"}
        strategy._adapt_x_twitter(content, story)

        assert content["x_twitter"]["first_reply"] == "https://hypebeast.com/news"

    def test_tweet_text_preserved(self, strategy):
        content = {
            "x_twitter": {"tweet": "Certified heat https://drop.com/x"}
        }
        story = {"content": content}
        strategy._adapt_x_twitter(content, story)

        assert "Certified heat" in content["x_twitter"]["tweet"]
        assert "https://" not in content["x_twitter"]["tweet"]


class TestInstagramUrlStrip:
    def test_urls_stripped(self, strategy):
        content = {
            "instagram": {"caption": "Check https://link.com this fit"}
        }
        story = {"content": content}
        strategy._adapt_instagram(content, story)
        assert "https://" not in content["instagram"]["caption"]


class TestExecute:
    def test_execute_adapts_stories(self, strategy):
        ctx = {
            "stories": [
                {
                    "content": {
                        "x_twitter": {"tweet": "Test https://url.com"},
                    },
                    "source_url": "https://source.com",
                },
            ]
        }
        result = strategy.execute(ctx)
        assert result["run_stats"]["adapt"]["adapted"] == 1
