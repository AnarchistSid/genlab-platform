"""Tests for genlab_core.platforms.rules."""

from genlab_core.platforms.rules import (
    AdaptedContent,
    enforce_platform_rules,
)

# ---------------------------------------------------------------------------
# Instagram rules
# ---------------------------------------------------------------------------


class TestInstagramRules:
    def test_url_stripped_from_caption(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Check this out https://example.com for more info",
            hashtags=["AI", "Tech", "News"],
        )
        assert "https://example.com" not in result.caption
        assert "Check this out" in result.caption

    def test_www_url_not_stripped(self):
        """Only http/https URLs are stripped, not www-only."""
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Visit www.example.com today",
            hashtags=["AI", "Tech", "News"],
        )
        assert "www.example.com" in result.caption

    def test_fewer_than_3_hashtags_warns(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Short caption",
            hashtags=["AI", "Tech"],
        )
        assert any("hashtags" in w.lower() for w in result.warnings)

    def test_zero_hashtags_warns(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Test",
            hashtags=[],
        )
        assert any("hashtag" in w.lower() for w in result.warnings)

    def test_3_hashtags_no_warning(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Short caption",
            hashtags=["AI", "Tech", "News"],
        )
        assert not any("hashtags" in w.lower() for w in result.warnings)

    def test_cta_appended_when_missing(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Amazing discovery about AI",
            hashtags=["AI", "Tech", "News"],
        )
        assert "Save this for later!" in result.caption or any("CTA" in w for w in result.warnings)

    def test_custom_cta_used(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Amazing AI breakthrough today",
            hashtags=["AI", "Tech", "News"],
            cta="Drop a comment below!",
        )
        assert "Drop a comment below!" in result.caption

    def test_cta_not_duplicated(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Amazing thing. Save this for later!",
            hashtags=["AI", "Tech", "News"],
        )
        assert result.caption.count("Save this for later!") == 1


# ---------------------------------------------------------------------------
# X/Twitter rules
# ---------------------------------------------------------------------------


class TestTwitterRules:
    def test_url_moved_to_first_comment(self):
        result = enforce_platform_rules(
            platform="twitter",
            title="Test",
            caption="Breaking news https://example.com/story from today",
        )
        assert "https://" not in result.caption
        assert result.first_comment == "https://example.com/story"

    def test_tweet_body_cleaned(self):
        result = enforce_platform_rules(
            platform="x",
            title="Test",
            caption="Check https://example.com and https://other.com now",
        )
        assert "https://" not in result.caption
        assert result.first_comment == "https://example.com"

    def test_url_param_used_when_no_url_in_body(self):
        result = enforce_platform_rules(
            platform="twitter",
            title="Test",
            caption="Breaking news from today",
            url="https://source.com/article",
        )
        assert result.first_comment == "https://source.com/article"

    def test_no_op_when_no_urls(self):
        result = enforce_platform_rules(
            platform="twitter",
            title="",
            caption="No links here, just vibes",
        )
        assert result.caption == "No links here, just vibes"
        assert result.first_comment == ""

    def test_x_standard_platform_name(self):
        result = enforce_platform_rules(
            platform="x_standard",
            title="Test",
            caption="News https://example.com here",
        )
        assert "https://" not in result.caption


# ---------------------------------------------------------------------------
# YouTube rules
# ---------------------------------------------------------------------------


class TestYouTubeRules:
    def test_title_converted_to_question(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="AI Just Changed Everything",
            caption="Description here",
        )
        assert result.title.endswith("?")

    def test_already_question_unchanged(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="Did AI Just Change Everything?",
            caption="Description here",
        )
        assert result.title == "Did AI Just Change Everything?"

    def test_title_truncated_at_40_chars(self):
        long_title = "This Is An Extremely Long Title That Exceeds The Forty Character Limit For YouTube Shorts?"
        result = enforce_platform_rules(
            platform="youtube",
            title=long_title,
            caption="Description here",
        )
        assert len(result.title) <= 40
        assert result.title.endswith("?")

    def test_short_title_preserved(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="Is GPT-5 Real?",
            caption="Description here",
        )
        assert result.title == "Is GPT-5 Real?"

    def test_empty_title_unchanged(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="",
            caption="Some description",
        )
        assert result.title == ""

    def test_strips_trailing_period_before_question(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="This Changes Everything.",
            caption="",
        )
        # "This ..." titles get "Did" prefix for real question format
        assert result.title == "Did this Changes Everything?"
        assert not result.title.endswith(".?")

    def test_warns_on_question_conversion(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="Big Update Incoming",
            caption="",
        )
        assert any("question" in w.lower() for w in result.warnings)

    def test_warns_on_truncation(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="A" * 50,
            caption="",
        )
        assert any("truncat" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Facebook rules
# ---------------------------------------------------------------------------


class TestFacebookRules:
    def test_short_caption_gets_engagement_question(self):
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Short post about AI.",
        )
        assert "What do you think" in result.caption

    def test_long_caption_unchanged(self):
        long_caption = "This is a properly detailed post about artificial intelligence " * 5
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption=long_caption,
        )
        assert "What do you think" not in result.caption

    def test_url_stripped_from_caption(self):
        """D1: Facebook captions must not contain URLs (spam trigger)."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Check this out https://example.com/story for more details about the AI breakthrough",
        )
        assert "https://example.com" not in result.caption
        assert "Check this out" in result.caption
        assert any("URL stripped" in w for w in result.warnings)

    def test_competitor_hashtags_removed_from_caption(self):
        """D1: Competitor platform mentions removed from FB caption."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Amazing AI news #tiktok #youtube #instagram and more",
        )
        assert "#tiktok" not in result.caption.lower()
        assert "#youtube" not in result.caption.lower()
        assert "#instagram" not in result.caption.lower()
        assert any("Competitor" in w for w in result.warnings)

    def test_competitor_hashtags_removed_from_hashtags_list(self):
        """D1: Competitor hashtags also filtered from the hashtags list."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Some content about AI",
            hashtags=["#AI", "#Tech", "#tiktok", "#twitter", "#News"],
        )
        for h in result.hashtags:
            assert h.lower() not in ("#tiktok", "#twitter")
        assert "#AI" in result.hashtags

    def test_hashtags_capped_at_5(self):
        """D1: Facebook hashtags capped at 5 (more triggers spam detection)."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Content here",
            hashtags=["#AI", "#Tech", "#News", "#ML", "#Data", "#Python", "#Code", "#Dev"],
        )
        assert len(result.hashtags) <= 5
        assert any("capped" in w.lower() for w in result.warnings)

    def test_5_or_fewer_hashtags_no_cap_warning(self):
        """No cap warning when hashtags are already <= 5."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="Content here",
            hashtags=["#AI", "#Tech", "#News"],
        )
        assert not any("capped" in w.lower() for w in result.warnings)

    def test_url_and_competitor_and_engagement_combined(self):
        """D1: All three rules apply together — URL stripped, competitor removed, engagement Q."""
        result = enforce_platform_rules(
            platform="facebook",
            title="Test",
            caption="See https://link.com #tiktok cool",
        )
        assert "https://" not in result.caption
        assert "#tiktok" not in result.caption.lower()
        # Short caption + engagement question
        assert "What do you think" in result.caption


# ---------------------------------------------------------------------------
# Universal date format
# ---------------------------------------------------------------------------


class TestDateFormat:
    def test_us_date_converted(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Released on 3/15/2026 worldwide",
            hashtags=["AI", "Tech", "News"],
        )
        assert "March 15, 2026" in result.caption
        assert "3/15/2026" not in result.caption

    def test_two_digit_year(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Coming 1/5/26 soon",
            hashtags=["AI", "Tech", "News"],
        )
        assert "January 5, 2026" in result.caption

    def test_date_in_title_also_converted(self):
        result = enforce_platform_rules(
            platform="youtube",
            title="Big news on 12/25/25?",
            caption="Details here",
        )
        assert "December 25, 2025" in result.title

    def test_invalid_month_left_unchanged(self):
        result = enforce_platform_rules(
            platform="instagram",
            title="Test",
            caption="Score was 15/3/2026 to 1",
            hashtags=["AI", "Tech", "News"],
        )
        # 15 is not a valid month, should be left as-is
        assert "15/3/2026" in result.caption


# ---------------------------------------------------------------------------
# Threads (uses Instagram backend)
# ---------------------------------------------------------------------------


class TestThreadsRules:
    def test_threads_strips_urls_like_instagram(self):
        result = enforce_platform_rules(
            platform="threads",
            title="Test",
            caption="Check https://example.com this",
            hashtags=["AI", "Tech", "News"],
        )
        assert "https://" not in result.caption


# ---------------------------------------------------------------------------
# AdaptedContent dataclass
# ---------------------------------------------------------------------------


class TestAdaptedContent:
    def test_default_values(self):
        c = AdaptedContent(title="T", caption="C", hashtags=[], first_comment="")
        assert c.warnings == []

    def test_warnings_mutable(self):
        c = AdaptedContent(title="T", caption="C", hashtags=[], first_comment="")
        c.warnings.append("test")
        assert len(c.warnings) == 1
