"""2026-08-12 (F-QB-0701): pins that FTC/ASCI disclosure lands in
the first 100 chars for all affiliate-injected captions.

Motivating audit finding: QB-2026-08 Phase 7 verified 17/17 movies
affiliate blueprints in a 30-day window had #ad past the 100-char
"more" fold. FTC 16 CFR 255 / ASCI guidelines both require "clear
and conspicuous" disclosure — burying past the fold does not qualify.

These pins guarantee any future change to caption-assembly order
(bandit variants, new CTA templates, hashtag re-ordering) can't
regress the disclosure position without CI catching it.
"""

from __future__ import annotations


from genlab_core.monetization.cta_engine import (
    _DISCLOSURE_HEAD_CHAR_LIMIT,
    _DISCLOSURE_MARKERS,
    _ensure_top_disclosure,
    _has_disclosure_in_head,
    inject_cta,
)


class TestEnsureTopDisclosure:
    def test_prepends_when_no_marker_in_head(self):
        text = "The X-Files trailer dropped and it's actually good. Peak 2000s energy."
        result = _ensure_top_disclosure(text)
        assert result.startswith("#ad ")
        # Original text preserved verbatim after the prepend.
        assert text in result

    def test_noop_when_ad_already_present(self):
        text = "#ad The X-Files trailer dropped and it's actually good."
        result = _ensure_top_disclosure(text)
        assert result == text

    def test_noop_when_affiliate_already_present(self):
        text = "#affiliate — The X-Files trailer dropped."
        result = _ensure_top_disclosure(text)
        assert result == text

    def test_noop_when_sponsored_in_head(self):
        text = "Sponsored content: check out this new movie."
        result = _ensure_top_disclosure(text)
        assert result == text

    def test_noop_when_paid_partnership_in_head(self):
        text = "Paid partnership with Amazon. Watch this."
        result = _ensure_top_disclosure(text)
        assert result == text

    def test_idempotent(self):
        text = "The X-Files trailer dropped and it's actually good."
        once = _ensure_top_disclosure(text)
        twice = _ensure_top_disclosure(once)
        assert once == twice

    def test_marker_beyond_100_chars_does_not_count(self):
        """The whole point: a marker BEYOND the fold is invisible
        without expanding the caption. Must count as non-compliant
        and prepend."""
        prefix = "x" * 120
        text = f"{prefix} #ad Amazon Prime Video"
        result = _ensure_top_disclosure(text)
        assert result.startswith("#ad "), (
            "expected prepend; #ad at char 121 does not satisfy the fold"
        )

    def test_empty_text_returns_empty(self):
        assert _ensure_top_disclosure("") == ""

    def test_custom_prepend_token(self):
        text = "The X-Files trailer dropped."
        result = _ensure_top_disclosure(text, prepend="#affiliate")
        assert result.startswith("#affiliate ")


class TestHasDisclosureInHead:
    def test_all_markers_recognised(self):
        for marker in _DISCLOSURE_MARKERS:
            text = f"{marker} some content"
            assert _has_disclosure_in_head(text), f"marker {marker!r} not recognised"

    def test_case_insensitive(self):
        assert _has_disclosure_in_head("#AD trailer")
        assert _has_disclosure_in_head("Sponsored content")
        assert _has_disclosure_in_head("#Affiliate")

    def test_char_limit_boundary(self):
        """A marker at exactly char 99 IS in the head; at char 100 is NOT."""
        # 96 chars of padding + "#ad " starting at position 96
        text = ("x" * 96) + "#ad content"
        assert _has_disclosure_in_head(text)  # char 96..99 contains #ad

        # 100 chars of padding + "#ad" starting at position 100
        text = ("x" * 100) + "#ad content"
        assert not _has_disclosure_in_head(text)


class TestInjectCtaProducesCompliantCaption:
    """End-to-end: inject_cta MUST produce captions with disclosure
    in the first 100 chars. Simulates the exact prod bug shape
    from F-QB-0701 — a movies caption where LLM writes the hook +
    story text + hashtags, and CTA engine adds the affiliate block."""

    def _base_story(self, with_affiliate: bool = True) -> dict:
        """inject_cta reads affiliate fields from `story`, not `fields`.
        `affiliate_product` maps to internal `product_name` variable;
        `affiliate_url` and `affiliate_price_inr` are direct."""
        story: dict = {
            "niche_id": "movies",
            "blueprint_id": "test-bp-123",
            "candidate_id": "test-cand-123",
            # Emulates AffiliateMatch stage output
            "_affiliate_disclosure_map": {
                "instagram": "#affiliate",
                "facebook": "#affiliate",
                "youtube": "This description contains affiliate links...",
            },
        }
        if with_affiliate:
            story["affiliate_product"] = "Amazon Prime Video"
            story["affiliate_url"] = "https://www.amazon.in/prime"
            story["affiliate_price_inr"] = 1499
        return story

    def _base_fields(self, hook: str = "Test hook") -> dict:
        # Long-form caption representative of what the LLM writes for
        # movies — no explicit disclosure, hashtags at tail. Matches
        # the F-QB-0701 prod shape.
        caption = (
            f"{hook} The X-Files: I Want to Believe - Official Trailer\n"
            "\n"
            "Via r/horror\n"
            "\n"
            "#Movies #Cinema #Horror"
        )
        return {
            "caption": caption,
            "facebook_content": caption,
            "threads_content": caption,
            "niche_id": "movies",
        }

    def test_ig_caption_gets_disclosure_in_first_100_chars(self):
        story = self._base_story()
        fields = self._base_fields()

        result = inject_cta(fields, story)

        head = result["caption"][:_DISCLOSURE_HEAD_CHAR_LIMIT].lower()
        assert any(m in head for m in _DISCLOSURE_MARKERS), (
            f"no disclosure marker in first {_DISCLOSURE_HEAD_CHAR_LIMIT} chars: "
            f"head={head!r}"
        )

    def test_facebook_content_gets_disclosure_in_first_100_chars(self):
        story = self._base_story()
        fields = self._base_fields()

        result = inject_cta(fields, story)
        fb = result.get("facebook_content", "")
        head = fb[:_DISCLOSURE_HEAD_CHAR_LIMIT].lower()
        assert any(m in head for m in _DISCLOSURE_MARKERS), (
            f"facebook_content head lacks disclosure: {head!r}"
        )

    def test_threads_content_gets_disclosure_in_first_100_chars(self):
        story = self._base_story()
        fields = self._base_fields()

        result = inject_cta(fields, story)
        th = result.get("threads_content", "")
        head = th[:_DISCLOSURE_HEAD_CHAR_LIMIT].lower()
        assert any(m in head for m in _DISCLOSURE_MARKERS), (
            f"threads_content head lacks disclosure: {head!r}"
        )

    def test_no_double_prepend_when_llm_already_disclosed(self):
        """If the LLM writes a caption already starting with #ad, we
        MUST NOT prepend a second #ad. Only markers from the LLM +
        (possibly) one from the bottom CTA block should exist."""
        story = self._base_story()
        fields = self._base_fields(hook="#ad Absolute banger of a trailer.")

        result = inject_cta(fields, story)
        caption = result["caption"]
        # Caption already starts with #ad from the LLM. No prepend.
        assert not caption.startswith("#ad #ad"), (
            f"double #ad detected at head: {caption[:40]!r}"
        )
        assert caption.startswith("#ad Absolute banger"), (
            f"LLM prefix not preserved: {caption[:40]!r}"
        )

    def test_no_change_when_no_affiliate(self):
        """When story has no affiliate_product, inject_cta short-circuits;
        the disclosure sweep should not fire on a non-affiliate caption."""
        story = self._base_story(with_affiliate=False)
        fields = self._base_fields()

        original_caption = fields["caption"]
        result = inject_cta(fields, story)
        assert result["caption"] == original_caption, (
            "non-affiliate caption should not receive an #ad prepend"
        )
