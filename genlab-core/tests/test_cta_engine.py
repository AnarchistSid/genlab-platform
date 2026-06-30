"""Tests for the affiliate CTA injection engine."""

from unittest.mock import patch

import pytest
from genlab_core.monetization.cta_engine import inject_cta


def _make_story(**overrides):
    base = {
        "affiliate_product": "PS5 Console",
        "affiliate_url": "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21",
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
        assert (
            "🔗 PS5 Console: https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21"
            in fields["youtube_content"]
        )
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
    # IG fallback CTA produced by _build_cta_text when bandit is None
    # and no price is set on the affiliate match.  Includes the "off-caption"
    # navigation hint "(link in bio)" — IG doesn't surface clickable links in
    # post captions so we anchor users to the bio link (dashboard hosts
    # the per-niche link-in-bio page that actually opens to product URLs).
    _IG_FALLBACK_NAV_HINT = "(link in bio)"
    _IG_FALLBACK_CTA = "🔗 Get PS5 Console 👇 (link in bio)"

    def test_off_caption_nav_hint_used_not_direct_url(self):
        """IG caption must direct users off-caption (no raw URLs in IG)."""
        story = _make_story()
        fields = inject_cta({"caption": "Amazing reel content."}, story)
        assert self._IG_FALLBACK_NAV_HINT in fields["caption"]
        assert "amazon.in" not in fields["caption"]

    def test_cta_inserted_before_hashtags(self):
        story = _make_story()
        caption = "Great gaming session. #gaming #ps5 #console"
        fields = inject_cta({"caption": caption}, story)
        result = fields["caption"]
        cta_pos = result.index(self._IG_FALLBACK_CTA)
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
        assert self._IG_FALLBACK_CTA in result

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
        assert self._IG_FALLBACK_NAV_HINT in caption
        # Disclosure should come BEFORE the CTA
        disc_pos = caption.find("#affiliate")
        cta_pos = caption.find(self._IG_FALLBACK_NAV_HINT)
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
        assert "test-tag-21" not in fields["caption"]


class TestFacebookCTA:
    def test_direct_url_routed_to_first_comment(self):
        """Affiliate URL must NOT be in main FB caption — FB downranks
        external URLs in captions, so URL ships as a follow-up comment."""
        story = _make_story()
        fields = inject_cta({"facebook_content": "Check this out!"}, story)
        assert "amazon.in/dp/B0CY5QW186" not in fields["facebook_content"]
        assert "amazon.in/dp/B0CY5QW186" in fields["facebook_first_comment"]

    def test_get_product_format_in_first_comment(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Awesome deal."}, story)
        # 🔗 Get-product CTA lives in first_comment, not main caption
        assert "🔗 Get PS5 Console:" in fields["facebook_first_comment"]
        assert "🔗 Get PS5 Console:" not in fields["facebook_content"]

    def test_disclosure_added(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Some Facebook post."}, story)
        # Disclosure stays in the main caption for FTC compliance
        assert "#affiliate" in fields["facebook_content"]

    def test_first_comment_emitted_when_affiliate_present(self):
        """facebook_first_comment field must be set whenever there's a URL."""
        story = _make_story()
        content = "Original Facebook post content here."
        fields = inject_cta({"facebook_content": content}, story)
        assert fields.get("facebook_first_comment", "")
        # Main caption keeps the original body
        assert "Original Facebook post content here." in fields["facebook_content"]

    def test_original_content_preserved(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Gaming deal of the year."}, story)
        assert "Gaming deal of the year." in fields["facebook_content"]

    def test_empty_facebook_content_still_gets_first_comment(self):
        """Even with empty facebook_content, the first-comment CTA exists when URL is present."""
        story = _make_story()
        fields = inject_cta({"facebook_content": ""}, story)
        # CTA in first_comment, not main caption
        assert "🔗 Get PS5 Console:" in fields["facebook_first_comment"]

    def test_does_not_use_link_in_bio(self):
        story = _make_story()
        fields = inject_cta({"facebook_content": "Deal alert!"}, story)
        # Neither main caption nor first-comment uses "link in bio" (FB-specific)
        assert "link in bio" not in fields["facebook_content"]
        assert "link in bio" not in fields.get("facebook_first_comment", "")

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

    def test_trailing_whitespace_stripped_before_disclosure(self):
        """Trailing whitespace is stripped before appending #affiliate disclosure."""
        story = _make_story()
        fields = inject_cta({"facebook_content": "Some content.   "}, story)
        result = fields["facebook_content"]
        # No double-space between body and disclosure
        assert "Some content.\n\n#affiliate" in result
        # CTA itself lives in first_comment, not main caption
        assert "🔗 Get PS5 Console:" in fields["facebook_first_comment"]


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
            "affiliate_url": "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21",
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


class TestSkipCtaInjectFlag:
    """OPS #2 + #6 (2026-06-30) — source-tool path bypass.

    BlackboxBrief's source-tool detector appends its OWN
    "Made with <Tool> — <link>" disclosure directly to every
    platform caption, then sets ``story["_skip_cta_inject"] = True``
    so the generic bandit-CTA layer below short-circuits. These
    tests pin that behavior so a future change can't silently
    re-enable double-CTA stacking on source-tool blueprints.
    """

    def test_flag_set_skips_all_platforms(self):
        story = _make_story()
        story["_skip_cta_inject"] = True
        original_fields = {
            "caption": "Made with Runway — https://runwayml.com",
            "youtube_content": "YouTube description body.",
            "facebook_content": "Facebook body.",
            "twitter_content": "Tweet body.",
        }
        fields = inject_cta(dict(original_fields), story)
        # Every field unchanged — no bandit CTA layered on top.
        assert fields == original_fields

    def test_flag_false_does_not_skip(self):
        story = _make_story()
        story["_skip_cta_inject"] = False
        fields = inject_cta({"caption": "Plain copy."}, story)
        # Normal injection happens — caption now carries a CTA.
        assert "PS5 Console" in fields["caption"] or "link in bio" in fields["caption"].lower()

    def test_flag_absent_does_not_skip(self):
        story = _make_story()
        # No _skip_cta_inject key at all.
        fields = inject_cta({"caption": "Plain copy."}, story)
        assert "PS5 Console" in fields["caption"] or "link in bio" in fields["caption"].lower()

    def test_made_with_in_caption_skips_instagram_injection(self):
        """Belt + suspenders: even WITHOUT the flag, an existing
        ``Made with`` token in the caption tells the Instagram branch
        not to layer a second CTA. This catches the re-run case
        where ``_skip_cta_inject`` is somehow not preserved on the
        story dict but the disclosure already landed in the caption.
        """
        story = _make_story()
        caption_with_disclosure = "wild ai demo\n\nMade with Runway — https://runwayml.com"
        fields = inject_cta({"caption": caption_with_disclosure}, story)
        # No "link in bio" or generic CTA appended.
        assert fields["caption"] == caption_with_disclosure

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

            mock_bandit = type(
                "MockBandit",
                (),
                {
                    "select": lambda self, platform: CTAVariant(
                        arm_id=f"test_{platform}",
                        platform=platform,
                        template="{product_name} — link in bio"
                        if platform == "instagram"
                        else "Get {product_name} here: {url}",
                        emoji="🔗",
                    ),
                },
            )()
            mock_get.return_value = mock_bandit

            story = _make_story()
            fields = inject_cta({"caption": "Test.", "youtube_content": "Test."}, story)
            assert "affiliate_cta_variant" in fields
            assert "test_instagram" in fields["affiliate_cta_variant"]
            assert "test_youtube" in fields["affiliate_cta_variant"]

    def test_bandit_fallback_on_failure(self):
        """If bandit.select() raises, hardcoded CTA from _build_cta_text is used."""
        with patch("genlab_core.monetization.cta_engine._get_bandit") as mock_get:
            mock_bandit = type(
                "BrokenBandit",
                (),
                {
                    "select": lambda self, platform: (_ for _ in ()).throw(RuntimeError("boom")),
                },
            )()
            mock_get.return_value = mock_bandit

            story = _make_story()
            fields = inject_cta({"caption": "Test caption."}, story)
            # Should fall back to the price-aware hardcoded format
            # ("🔗 Get PS5 Console 👇" with no price + "(link in bio)" for IG)
            assert "🔗 Get PS5 Console 👇 (link in bio)" in fields["caption"]
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
        # CTA should still be present (original text truncated, not the CTA).
        # Anchor on the off-caption nav hint to survive future CTA copy changes.
        assert "(link in bio)" in fields["caption"]
        assert "#affiliate" in fields["caption"]


class TestTrackedUrl:
    """R-23/CD-5: published affiliate links route through /links/go for click
    tracking, attributed by the bp param."""

    def test_routes_through_redirect_when_domain_set(self, monkeypatch):
        from genlab_core.monetization.cta_engine import _tracked_url

        monkeypatch.setenv("GENLAB_DOMAIN", "https://bb.example.com")
        out = _tracked_url(
            "https://amzn.to/raw", "Gaming Mouse Pro", niche_id="gaming", attribution_id="cand-1"
        )
        assert out == "https://bb.example.com/links/go/gaming-mouse-pro?bp=cand-1"

    def test_adds_scheme_when_domain_bare(self, monkeypatch):
        from genlab_core.monetization.cta_engine import _tracked_url

        monkeypatch.setenv("GENLAB_DOMAIN", "bb.example.com")
        out = _tracked_url("https://amzn.to/raw", "X", niche_id="gaming", attribution_id="c1")
        assert out.startswith("https://bb.example.com/links/go/")

    def test_falls_back_to_raw_utm_without_domain_in_soft_mode(self, monkeypatch):
        """Soft mode (default in CI / local dev): no domain → warn once, fall back."""
        from genlab_core.monetization import cta_engine

        monkeypatch.delenv("GENLAB_DOMAIN", raising=False)
        monkeypatch.delenv("GENLAB_REQUIRE_TRACKING_DOMAIN", raising=False)
        # Reset the one-time warning flag so it fires for this test
        monkeypatch.setattr(cta_engine, "_TRACKING_DOMAIN_WARNING_EMITTED", False)
        out = cta_engine._tracked_url(
            "https://amzn.to/raw", "Gaming Mouse", niche_id="gaming", attribution_id="cand-1"
        )
        assert "amzn.to/raw" in out  # raw URL preserved (no regression)
        assert "utm_source" in out

    def test_raises_without_domain_in_strict_mode(self, monkeypatch):
        """2026-06-19 regression pin: prod sets GENLAB_REQUIRE_TRACKING_DOMAIN=1.
        Missing GENLAB_DOMAIN must raise loudly so the silent monetization-
        bypass bug (100% of YT/FB/Threads attribution lost when env unset)
        cannot recur."""
        import pytest
        from genlab_core.monetization.cta_engine import _tracked_url

        monkeypatch.delenv("GENLAB_DOMAIN", raising=False)
        monkeypatch.setenv("GENLAB_REQUIRE_TRACKING_DOMAIN", "1")
        with pytest.raises(RuntimeError, match="GENLAB_DOMAIN is required"):
            _tracked_url(
                "https://amzn.to/raw",
                "Gaming Mouse",
                niche_id="gaming",
                attribution_id="cand-1",
            )

    def test_strict_mode_does_not_apply_when_domain_is_set(self, monkeypatch):
        """Strict mode is only about the unset case — if domain is set,
        strict-mode flag is a no-op."""
        from genlab_core.monetization.cta_engine import _tracked_url

        monkeypatch.setenv("GENLAB_DOMAIN", "https://bb.example.com")
        monkeypatch.setenv("GENLAB_REQUIRE_TRACKING_DOMAIN", "1")
        out = _tracked_url(
            "https://amzn.to/raw",
            "Gaming Mouse Pro",
            niche_id="gaming",
            attribution_id="cand-1",
        )
        assert out == "https://bb.example.com/links/go/gaming-mouse-pro?bp=cand-1"

    def test_soft_mode_warning_emitted_only_once(self, monkeypatch, caplog):
        """Operator-friendly: log the warning once per process, not per CTA."""
        import logging

        from genlab_core.monetization import cta_engine

        monkeypatch.delenv("GENLAB_DOMAIN", raising=False)
        monkeypatch.delenv("GENLAB_REQUIRE_TRACKING_DOMAIN", raising=False)
        monkeypatch.setattr(cta_engine, "_TRACKING_DOMAIN_WARNING_EMITTED", False)
        with caplog.at_level(logging.WARNING, logger="genlab_core.monetization.cta_engine"):
            for _ in range(3):
                cta_engine._tracked_url("u", "p", niche_id="n", attribution_id="a")
        domain_warnings = [r for r in caplog.records if "GENLAB_DOMAIN is unset" in r.message]
        assert len(domain_warnings) == 1, f"expected 1 warning, got {len(domain_warnings)}"

    def test_product_slug(self):
        from genlab_core.monetization.cta_engine import _product_slug

        assert _product_slug("Gaming Mouse Pro!") == "gaming-mouse-pro"
        assert _product_slug("PS5 Console") == "ps5-console"
