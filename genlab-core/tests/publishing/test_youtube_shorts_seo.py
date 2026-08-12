"""Pin the YouTube Shorts SEO description builder.

Behavioral contract:

  * Flag off (default) => byte-identical legacy shape
    (`#Shorts\\n\\n<caption>\\n\\n<hashtags>`). Callers can wire
    unconditionally without prod-behavior change until operator flips.
  * Flag on => enriched structure:
      - `#Shorts` first line
      - hook as curiosity anchor (2nd paragraph)
      - caption body (trailing hashtags stripped, source credit
        preserved because it doesn't start with #)
      - follow CTA
      - merged hashtag block (story + inline-caption + niche anchors)
      - source credit at end IF not already substring-present
  * Fail-open: any exception in enriched-path returns legacy shape.
  * Idempotent: source_credit already present in caption doesn't
    double-append.
  * Hashtag cap at 15 tags (YouTube's Shorts hashtag limit — beyond
    that the whole block gets ignored by classifier).
  * Description cap at 5000 chars (YouTube hard limit), cut at word
    boundary.
"""

from __future__ import annotations

import pytest

from genlab_core.publishing.youtube_shorts_seo import (
    _HASHTAG_LIMIT,
    _NICHE_ANCHOR_HASHTAGS,
    _dedupe_case_insensitive,
    _extract_hashtags,
    build_shorts_description,
)


class TestFlagGating:
    def test_flag_off_returns_legacy_shape(self, monkeypatch):
        monkeypatch.delenv("GENLAB_YT_SHORTS_SEO_ENABLED", raising=False)
        result = build_shorts_description(
            hook="Wild play from Game 7",
            caption="LeBron takes over in overtime.\n\n#Lakers",
            hashtags=["#NBA", "#Playoffs"],
            niche_id="sports",
        )
        # Legacy shape does NOT include hook line, CTA, or niche anchors
        assert result.startswith("#Shorts\n\n")
        assert "Watch until the end" not in result
        assert "#SportsShorts" not in result

    def test_flag_on_uses_enriched(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "1")
        result = build_shorts_description(
            hook="Wild play from Game 7",
            caption="LeBron takes over.\n\n#Lakers",
            hashtags=["#NBA"],
            niche_id="sports",
        )
        assert result.startswith("#Shorts\n\n")
        assert "Wild play from Game 7" in result
        assert "Watch until the end" in result
        # Niche anchor hashtags injected
        assert "#Sports" in result
        assert "#SportsShorts" in result


class TestEnrichedStructure:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "1")

    def test_hook_appears_as_curiosity_anchor(self):
        result = build_shorts_description(
            hook="This ending broke Twitter",
            caption="The finale everyone's arguing about.",
            hashtags=["#Movies"],
            niche_id="movies",
        )
        parts = result.split("\n\n")
        assert parts[0] == "#Shorts"
        assert parts[1] == "This ending broke Twitter"

    def test_hashtag_dedup_across_sources(self):
        result = build_shorts_description(
            hook="AI wrote this song",
            caption="AI Music tools compared.\n\n#AI #Music",
            hashtags=["#AI", "#Automation"],  # #AI dupes with trailing + anchor
            niche_id="ai_creators",
        )
        tag_count = result.lower().count("#ai ") + result.lower().count("#ai\n")
        # Should appear exactly once despite 3 sources
        assert tag_count == 1

    def test_niche_anchors_appended_after_story_tags(self):
        result = build_shorts_description(
            hook="Ranked matchup",
            caption="Elden Ring PvP.",
            hashtags=["#EldenRing", "#PvP"],
            niche_id="gaming",
        )
        tag_line = [line for line in result.split("\n") if line.startswith("#") and " " in line]
        assert tag_line
        tags = tag_line[-1].split()
        # Story-specific tags come first, anchor tags after
        assert tags.index("#EldenRing") < tags.index("#Gaming")

    def test_hashtag_cap_at_15(self):
        story_tags = [f"#Tag{i}" for i in range(20)]
        result = build_shorts_description(
            hook="h",
            caption="c",
            hashtags=story_tags,
            niche_id="gaming",
        )
        tag_line = [line for line in result.split("\n") if line.startswith("#") and " " in line]
        assert tag_line
        assert len(tag_line[-1].split()) == _HASHTAG_LIMIT

    def test_source_credit_appended_once(self):
        credit = "🎬 Original: @somechannel — https://youtube.com/watch?v=abc"
        result = build_shorts_description(
            hook="Big moment",
            caption="Story body.",
            hashtags=["#Sports"],
            niche_id="sports",
            source_credit=credit,
        )
        assert result.count(credit) == 1

    def test_source_credit_not_double_appended_when_in_caption(self):
        credit = "🎬 Original: @somechannel — https://youtube.com/watch?v=abc"
        result = build_shorts_description(
            hook="Big moment",
            caption=f"Story body.\n\n{credit}\n\n#Sports",
            hashtags=["#Basketball"],
            niche_id="sports",
            source_credit=credit,
        )
        # Only appears once (via caption, not double-appended)
        assert result.count(credit) == 1

    def test_source_credit_preserved_when_only_in_caption(self):
        """Attribution defense stack invariant: if caption has credit and
        we don't pass source_credit explicitly, credit MUST still ship."""
        credit = "🎬 Original: @somechannel — https://youtube.com/watch?v=abc"
        result = build_shorts_description(
            hook="Big moment",
            caption=f"Story body.\n\n{credit}",
            hashtags=["#Basketball"],
            niche_id="sports",
        )
        assert credit in result

    def test_niche_cta_matches_niche(self):
        for niche_id, expected_snippet in [
            ("gaming", "gaming"),
            ("sports", "sports"),
            ("movies", "movie"),
            ("anime", "anime"),
            ("ai_creators", "AI"),
        ]:
            result = build_shorts_description(
                hook="h", caption="c", hashtags=[], niche_id=niche_id,
            )
            assert expected_snippet in result.lower() or expected_snippet in result

    def test_unknown_niche_falls_back_to_defaults(self):
        result = build_shorts_description(
            hook="h", caption="c", hashtags=["#foo"], niche_id="fashion_zzz",
        )
        assert "Watch until the end" in result
        # No niche anchors injected for unknown niche
        assert "#GamingShorts" not in result

    def test_description_length_capped_at_5000(self):
        long_caption = "abc " * 2000  # ~8000 chars
        result = build_shorts_description(
            hook="h",
            caption=long_caption,
            hashtags=["#Sports"],
            niche_id="sports",
        )
        assert len(result) <= 5000

    def test_empty_hook_still_produces_valid_output(self):
        result = build_shorts_description(
            hook="", caption="Story body.", hashtags=["#Sports"], niche_id="sports",
        )
        assert result.startswith("#Shorts\n\n")
        assert "Story body." in result

    def test_empty_caption_still_produces_valid_output(self):
        result = build_shorts_description(
            hook="Hook line", caption="", hashtags=["#Sports"], niche_id="sports",
        )
        assert result.startswith("#Shorts\n\n")
        assert "Hook line" in result


class TestExtractHashtags:
    def test_trailing_tag_block_stripped(self):
        body, tags = _extract_hashtags("The story.\n\n#Sports #NBA")
        assert body == "The story."
        assert tags == ["#Sports", "#NBA"]

    def test_inline_hashtag_preserved(self):
        body, tags = _extract_hashtags("Check out #EldenRing today.")
        # Inline usage kept as content
        assert "#EldenRing" in body
        assert tags == []

    def test_empty_input(self):
        body, tags = _extract_hashtags("")
        assert body == ""
        assert tags == []

    def test_only_hashtags(self):
        body, tags = _extract_hashtags("#Sports #NBA #Playoffs")
        assert body == ""
        assert tags == ["#Sports", "#NBA", "#Playoffs"]


class TestDedupe:
    def test_case_insensitive_dedup(self):
        assert _dedupe_case_insensitive(["#Sports", "#SPORTS", "#sports", "#NBA"]) == [
            "#Sports", "#NBA",
        ]

    def test_preserves_first_seen_form(self):
        assert _dedupe_case_insensitive(["#nba", "#NBA"]) == ["#nba"]

    def test_no_duplicates_returns_input(self):
        assert _dedupe_case_insensitive(["#A", "#B", "#C"]) == ["#A", "#B", "#C"]


class TestFailOpen:
    def test_broken_call_returns_legacy(self, monkeypatch):
        """Force enriched path to raise — must fall back to legacy shape."""
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "1")
        import genlab_core.publishing.youtube_shorts_seo as mod

        def _boom(**_):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(mod, "_build_enriched", _boom)
        result = build_shorts_description(
            hook="h", caption="Body.", hashtags=["#X"], niche_id="sports",
        )
        assert result == "#Shorts\n\nBody.\n\n#X"


class TestNicheAnchorsRegistry:
    def test_all_five_niches_have_anchors(self):
        for niche_id in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche_id in _NICHE_ANCHOR_HASHTAGS
            assert len(_NICHE_ANCHOR_HASHTAGS[niche_id]) >= 3

    def test_anchor_tags_are_valid_hashtag_format(self):
        for tags in _NICHE_ANCHOR_HASHTAGS.values():
            for tag in tags:
                assert tag.startswith("#")
                assert " " not in tag
                # Length reasonable — YouTube itself doesn't cap but
                # readability matters
                assert 2 <= len(tag) <= 30
