"""Tests for SpliceReel platform adaptation — X link rule."""

import pytest
from sr_strategies.platform_adaptation import MoviePlatformAdaptationStrategy


@pytest.fixture
def strategy():
    return MoviePlatformAdaptationStrategy()


class TestXTwitterLinkRule:
    def test_url_moved_to_first_reply(self, strategy):
        content = {
            "x_twitter": {
                "tweet": "This scene https://trailer.com/clip is incredible",
            }
        }
        story = {"content": content, "source_url": "https://source.com"}
        strategy._adapt_x_twitter(content, story)

        assert "https://" not in content["x_twitter"]["tweet"]
        assert "first_reply" in content["x_twitter"]
        assert "https://trailer.com/clip" in content["x_twitter"]["first_reply"]

    def test_source_url_in_first_reply_when_no_inline_urls(self, strategy):
        content = {"x_twitter": {"tweet": "This scene was incredible"}}
        story = {"content": content, "source_url": "https://imdb.com/title"}
        strategy._adapt_x_twitter(content, story)

        assert content["x_twitter"]["first_reply"] == "https://imdb.com/title"

    def test_tweet_text_preserved(self, strategy):
        content = {"x_twitter": {"tweet": "Cinema at its finest https://clip.com/x"}}
        story = {"content": content}
        strategy._adapt_x_twitter(content, story)

        assert "Cinema at its finest" in content["x_twitter"]["tweet"]
        assert "https://" not in content["x_twitter"]["tweet"]


class TestInstagramUrlStrip:
    def test_urls_stripped(self, strategy):
        content = {"instagram": {"caption": "Watch this https://link.com now"}}
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
