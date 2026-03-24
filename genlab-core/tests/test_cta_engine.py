"""Tests for the affiliate CTA injection engine."""

from unittest.mock import patch

import pytest

from genlab_core.monetization.cta_engine import inject_cta


def _make_story(**overrides):
    base = {
        "affiliate_product": "PS5 Console",
        "affiliate_url": "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***",
        "_affiliate_disclosure_map": {
            "instagram": "#affiliate",
            "youtube": "Contains affiliate links.",
            "facebook": "#affiliate",
        },
    }
    base.update(overrides)
    return base


# Disable the bandit singleton for deterministic fallback-format tests.
# Tests that specifically exercise the bandit integration are in a separate class below.
@pytest.fixture(autouse=True)
def _disable_bandit():
    """Force inject_cta to use hardcoded CTA formats for deterministic assertions."""
    with patch("genlab_core.monetization.cta_engine._get_bandit", return_value=None):
        yield


class TestYouTubeCTA:
    def test_direct_url_prepended(self):
        story = _make_story()
        fields = inject_cta({"youtube_content": "Great gaming session today."}, story)
        # URL now includes UTM params appended by append_utm_params()
        assert "🔗 PS5 Console: https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***" in fields["youtube_content"]
        assert fields["youtube_content"].index("🔗 PS5 Console:") == 0  # still prepended

    def test_disclosure_appended(self):
        story = _make_story()
        fields = inject_cta({"youtube_content": "Some description here."}, story)
        assert fields["youtube_content"].endswith("Contains affiliate links.")

    def test_original_content_preserved_between_prefix_and_disclosure(self):
        story = _make_story()
        fields = inject_cta({"youtube_content": "Watch this amazing clip."}, story)
        assert "Watch this amazing clip." in fields["youtube_content"]

    def test_empty_youtube_content_still_gets_cta(self):
        """When youtube_content is empty but a URL exists, CTA is still injected."""
        story = _make_story()
        fields = inject_cta({"youtube_content": ""}, story)
        assert "🔗 PS5 Console:" in fields["youtube_content"]
        # URL base is preserved (UTM params may be appended)
        assert "amazon.in/dp/B0CY5QW186" in fields["youtube_content"]

    def test_does_not_contain_link_in_bio(self):
        """YouTube must use the direct URL, never 'link in bio'."""
        story = _make_story()
        fields = inject_cta({"youtube_content": "Epic gameplay here."}, story)
        assert "link in bio" not in fields["youtube_content"]

    def test_no_youtube_content_key_is_not_mutated(self):
        """If youtube_content is absent but url is present, key is still written."""
        story = _make_story()
        fields = inject_cta({}, story)
        # With no key and a url present, youtube_content should be added
        assert "🔗 PS5 Console:" in fields.get("youtube_content", "")

    def test_custom_disclosure_used(self):
        story = _make_story(
            _affiliate_disclosure_map={
                "instagram": "#ad",
                "youtube": "Affiliate link below.",
                "facebook": "#ad",
            }
        )
        fields = inject_cta({"youtube_content": "Content here."}, story)
        assert fields["youtube_content"].endswith("Affiliate link below.")

    def test_trailing_whitespace_stripped_before_disclosure(self):
        story = _make_story()
        fields = inject_cta({"youtube_content": "Gameplay description.   "}, story)
        # disclosure follows a \n\n with no extra trailing spaces before it
        assert "\n\nContains affiliate links." in fields["youtube_content"]


class TestInstagramCTA:
    def test_link_in_bio_used_not_direct_url(self):
        story = _make_story()
        fields = inject_cta({"caption": "Amazing reel content."}, story)
        assert "link in bio" in fields["caption"]
        assert "amazon.in" not in fields["caption"]

    def test_cta_inserted_before_hashtags(self):
        story = _make_story()
        caption = "Great gaming session. #gaming #ps5 #console"
        fields = inject_cta({"caption": caption}, story)
        result = fields["caption"]
        cta_pos = result.index("🔗 PS5 Console — link in bio")
        hashtag_pos = result.index("#gaming")
        assert cta_pos < hashtag_pos

    def test_disclosure_added(self):
        story = _make_story()
        fields = inject_cta({"caption": "Incredible content here."}, story)
        assert "#affiliate" in fields["caption"]

    def test_cta_inserted_at_end_when_no_hashtags(self):
        story = _make_story()
        fields = inject_cta({"caption": "Pure caption text with no tags."}, story)
        result = fields["caption"]
        # CTA appears after the main body (before disclosure)
        assert "🔗 PS5 Console — link in bio" in result

    def test_original_text_preserved(self):
        story = _make_story()
        caption = "Watch this crazy highlight."
        fields = inject_cta({"caption": caption}, story)
        assert "Watch this crazy highlight." in fields["caption"]

    def test_empty_caption_not_modified(self):
        """Empty caption should not get CTA injected."""
        story = _make_story()
        fields = inject_cta({"caption": ""}, story)
        # caption key stays empty — engine only injects if caption is non-empty
        assert fields.get("caption", "") == ""

    def test_disclosure_before_cta(self):
        """Disclosure should appear before the CTA for FTC/ASCI compliance."""
        story = _make_story()
        fields = inject_cta({"caption": "Hot take content. #trending #viral"}, story)
        caption = fields["caption"]
        assert "#affiliate" in caption
        assert "link in bio" in caption
        # Disclosure should come BEFORE the CTA
        disc_pos = caption.find("#affiliate")
        cta_pos = caption.find("link in bio")
        assert disc_pos < cta_pos, "Disclosure must appear before CTA"

    def test_custom_ig_disclosure(self):
        story = _make_story(
            _affiliate_disclosure_map={
                "instagram": "#ad #sponsored",
                "youtube": "Contains affiliate links.",
                "facebook": "#affiliate",
            }
        )
        fields = inject_cta({"caption": "Great product review."}, story)
        assert "#ad #sponsored" in fields["caption"]

    def test_no_direct_url_in_caption(self):
        """Instagram captions must never contain the raw affiliate URL."""
        story = _make_story()
        fields = inject_cta({"caption": "Amazing deal for gamers. #gaming"}, story)
        assert "amazon.in" not in fields["caption"]
        assert "***REMOVED***" not in fields["caption"]


class TestFacebookCTA:
    def test_direct_url_in_content(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Check this out!"}, story)
        # URL base is preserved (UTM params may be appended)
        assert "amazon.in/dp/B0CY5QW186" in fields["facebook_content"]

    def test_get_product_format(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Awesome deal."}, story)
        assert "🔗 Get PS5 Console:" in fields["facebook_content"]

    def test_disclosure_added(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Some Facebook post."}, story)
        assert "#affiliate" in fields["facebook_content"]

    def test_cta_appended_not_prepended(self):
        """Facebook CTA goes at the end, unlike YouTube which prepends."""
        story = _make_story()
        content = "Original Facebook post content here."
        fields = inject_cta({"facebook_content": content}, story)
        result = fields["facebook_content"]
        original_pos = result.index("Original Facebook post content here.")
        cta_pos = result.index("🔗 Get PS5 Console:")
        assert original_pos < cta_pos

    def test_original_content_preserved(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Gaming deal of the year."}, story)
        assert "Gaming deal of the year." in fields["facebook_content"]

    def test_empty_facebook_content_still_gets_cta(self):
        """Even with empty facebook_content, CTA is injected when URL exists."""
        story = _make_story()
        fields = inject_cta({"facebook_content": ""}, story)
        assert "🔗 Get PS5 Console:" in fields["facebook_content"]

    def test_does_not_use_link_in_bio(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Deal alert!"}, story)
        assert "link in bio" not in fields["facebook_content"]

    def test_custom_fb_disclosure(self):
        story = _make_story(
            _affiliate_disclosure_map={
                "instagram": "#affiliate",
                "youtube": "Contains affiliate links.",
                "facebook": "#ad",
            }
        )
        fields = inject_cta({"facebook_content": "Check this deal."}, story)
        assert "#ad" in fields["facebook_content"]

    def test_trailing_whitespace_stripped_before_cta(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Some content.   "}, story)
        result = fields["facebook_content"]
        # CTA should follow content without preserving trailing spaces
        assert "\n\n🔗 Get PS5 Console:" in result


class TestTwitterCTA:
    def test_twitter_content_not_modified(self):
        story = _make_story()
        original = "Hot take on the PS5 drop."
        fields = inject_cta({"twitter_content": original}, story)
        assert fields["twitter_content"] == original

    def test_twitter_content_key_unchanged_with_hashtags(self):
        story = _make_story()
        original = "PS5 is here! #gaming #ps5"
        fields = inject_cta({"twitter_content": original}, story)
        assert fields["twitter_content"] == original

    def test_twitter_content_key_unchanged_empty(self):
        story = _make_story()
        fields = inject_cta({"twitter_content": ""}, story)
        assert fields.get("twitter_content") == ""

    def test_no_url_injected_into_twitter(self):
        story = _make_story()
        fields = inject_cta({"twitter_content": "Big gaming news."}, story)
        assert "amazon.in" not in fields["twitter_content"]

    def test_no_disclosure_injected_into_twitter(self):
        story = _make_story()
        fields = inject_cta({"twitter_content": "Gaming moment."}, story)
        assert "#affiliate" not in fields["twitter_content"]
        assert "affiliate" not in fields["twitter_content"].lower()

    def test_twitter_not_present_stays_absent(self):
        story = _make_story()
        fields = inject_cta({"caption": "Some caption."}, story)
        assert "twitter_content" not in fields


class TestNoProduct:
    def test_empty_affiliate_product_returns_fields_unchanged(self):
        story = _make_story(affiliate_product="")
        original_fields = {
            "caption": "Caption without product.",
            "youtube_content": "YouTube without product.",
            "facebook_content": "Facebook without product.",
            "twitter_content": "Twitter without product.",
        }
        fields = inject_cta(dict(original_fields), story)
        assert fields == original_fields

    def test_missing_affiliate_product_returns_fields_unchanged(self):
        story = {
            "affiliate_url": "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***",
            "_affiliate_disclosure_map": {"instagram": "#affiliate"},
        }
        original_fields = {"caption": "Some caption.", "youtube_content": "Some YouTube."}
        fields = inject_cta(dict(original_fields), story)
        assert fields == original_fields

    def test_no_product_no_cta_in_caption(self):
        story = _make_story(affiliate_product="")
        fields = inject_cta({"caption": "Just a regular caption. #gaming"}, story)
        assert "link in bio" not in fields["caption"]
        assert "🔗" not in fields["caption"]

    def test_no_product_no_cta_in_youtube(self):
        story = _make_story(affiliate_product="")
        original = "Plain YouTube description."
        fields = inject_cta({"youtube_content": original}, story)
        assert fields["youtube_content"] == original

    def test_no_product_no_cta_in_facebook(self):
        story = _make_story(affiliate_product="")
        original = "Plain Facebook post."
        fields = inject_cta({"facebook_content": original}, story)
        assert fields["facebook_content"] == original

    def test_returns_same_dict_object(self):
        """inject_cta returns the same dict instance it received."""
        story = _make_story()
        original = {"caption": "Test caption with content."}
        result = inject_cta(original, story)
        assert result is original

    def test_empty_product_returns_same_dict_object(self):
        story = _make_story(affiliate_product="")
        original = {"caption": "Some text."}
        result = inject_cta(original, story)
        assert result is original


class TestBanditIntegration:
    """Tests that verify the bandit is actually wired in when available."""

    def test_bandit_variant_selected_and_stored(self):
        """When bandit is available, variant arm_id is stored on fields."""
        # Don't use the autouse fixture — let the real bandit run
        with patch("genlab_core.monetization.cta_engine._get_bandit") as mock_get:
            from genlab_core.monetization.cta_bandit import CTAVariant
            mock_bandit = type("MockBandit", (), {
                "select": lambda self, platform: CTAVariant(
                    arm_id=f"test_{platform}",
                    platform=platform,
                    template="{product_name} — link in bio" if platform == "instagram"
                    else "Get {product_name} here: {url}",
                    emoji="🔗",
                ),
            })()
            mock_get.return_value = mock_bandit

            story = _make_story()
            fields = inject_cta({"caption": "Test.", "youtube_content": "Test."}, story)
            assert "affiliate_cta_variant" in fields
            assert "test_instagram" in fields["affiliate_cta_variant"]
            assert "test_youtube" in fields["affiliate_cta_variant"]

    def test_bandit_fallback_on_failure(self):
        """If bandit.select() raises, hardcoded CTA is used."""
        with patch("genlab_core.monetization.cta_engine._get_bandit") as mock_get:
            mock_bandit = type("BrokenBandit", (), {
                "select": lambda self, platform: (_ for _ in ()).throw(RuntimeError("boom")),
            })()
            mock_get.return_value = mock_bandit

            story = _make_story()
            fields = inject_cta({"caption": "Test caption."}, story)
            # Should fall back to hardcoded format
            assert "🔗 PS5 Console — link in bio" in fields["caption"]
            # No variant stored because bandit failed
            assert "affiliate_cta_variant" not in fields


class TestCaptionLengthEnforcement:
    """Tests for platform caption length limit enforcement."""

    def test_instagram_within_limit_not_truncated(self):
        story = _make_story()
        fields = inject_cta({"caption": "Short caption."}, story)
        assert len(fields["caption"]) <= 2200

    def test_instagram_long_caption_truncated(self):
        story = _make_story()
        # Create a caption that will exceed 2200 chars after CTA injection
        long_caption = "A" * 2200
        fields = inject_cta({"caption": long_caption}, story)
        assert len(fields["caption"]) <= 2200
        # CTA should still be present (original text truncated, not the CTA)
        assert "link in bio" in fields["caption"]
        assert "#affiliate" in fields["caption"]
