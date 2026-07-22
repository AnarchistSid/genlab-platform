"""Pin tests for the Layer 3 S7 (writer-only slice) storytime variant.

Same shape as `test_split_screen_selector.py`. Covers:

* Positive narrative-arc signals: story of, the day X, what happened, etc.
* Negative cases: partial matches, wrong duration, missing narration source
* Priority: series_part / question_reveal / watch_till_end / split_screen
  all block storytime — storytime is the bottom of the chain
* Payload builder shape (narration_text length + trim + 40-char floor)
* Prompt section presence + framework markers
"""

from __future__ import annotations

from genlab_core.writing.storytime_selector import (
    build_storytime_payload,
    format_storytime_prompt_section,
    is_storytime_eligible,
)


def _story(**overrides):
    base = {
        "story_id": "test-story",
        "title": "The day GitHub Copilot changed how devs write code",
        "duration_seconds": 75,
        "video_id": "abc123",
        "summary": (
            "In late 2021 GitHub previewed Copilot to a small group of "
            "developers. Within weeks the tool had rewritten how a whole "
            "generation of programmers approached their editors."
        ),
    }
    base.update(overrides)
    return base


class TestStoryOfMatches:
    def test_the_story_of_matches(self) -> None:
        assert is_storytime_eligible(_story(title="The story of Bitcoin's first pizza"))

    def test_the_story_of_case_insensitive(self) -> None:
        assert is_storytime_eligible(_story(title="THE STORY OF the OpenAI board coup"))


class TestTheDayMatches:
    def test_the_day_i_matches(self) -> None:
        assert is_storytime_eligible(_story(title="The day I quit using ChatGPT for real"))

    def test_the_day_we_matches(self) -> None:
        assert is_storytime_eligible(_story(title="The day we launched Anthropic's Claude API"))


class TestWhatHappenedMatches:
    def test_what_really_happened_matches(self) -> None:
        assert is_storytime_eligible(_story(title="What really happened at Meta's AI reset"))

    def test_what_happened_when_matches(self) -> None:
        assert is_storytime_eligible(
            _story(title="What happened when Anthropic shipped Sonnet 4")
        )


class TestHowXHappenedMatches:
    def test_how_it_happened_matches(self) -> None:
        assert is_storytime_eligible(
            _story(title="How OpenAI's browser agent happened despite the freeze")
        )

    def test_how_it_ended_matches(self) -> None:
        assert is_storytime_eligible(_story(title="How the LK-99 hype ended in tears"))


class TestNegativeCases:
    def test_no_narrative_signal_rejected(self) -> None:
        """A title without any narrative marker (even at 90s) must not match."""
        assert not is_storytime_eligible(_story(title="AI trends 2026 roundup"))

    def test_short_duration_rejected(self) -> None:
        """Under 60s = no room for arc structure."""
        assert not is_storytime_eligible(_story(duration_seconds=45))

    def test_long_duration_rejected(self) -> None:
        """Over 120s exits short-form platform sweet spot."""
        assert not is_storytime_eligible(_story(duration_seconds=150))

    def test_missing_duration_rejected(self) -> None:
        s = _story()
        s.pop("duration_seconds")
        assert not is_storytime_eligible(s)


class TestPriority:
    def test_series_part_blocks_storytime(self) -> None:
        """A title that would match storytime AND looks like a series
        must NOT be eligible — series priority wins across the whole chain."""
        s = _story(title="The story of Kubernetes Part 3")
        assert not is_storytime_eligible(s)

    def test_question_reveal_blocks_storytime(self) -> None:
        """Explicit question-mark titles route to question_reveal."""
        # Question reveal needs duration 30-90 too, so put duration inside both
        s = _story(
            title="How did OpenAI's coup really unfold?",
            duration_seconds=80,
        )
        assert not is_storytime_eligible(s)

    def test_watch_till_end_blocks_storytime(self) -> None:
        """Compilation keywords route to watch_till_end."""
        # Duration 60-90 satisfies BOTH watch_till_end AND storytime bounds
        s = _story(title="Top 10 stories of how AI hype ended in 2026", duration_seconds=75)
        assert not is_storytime_eligible(s)

    def test_split_screen_blocks_storytime(self) -> None:
        """Comparison signals route to split_screen. Duration 60-90 satisfies
        both split_screen (15-90) AND storytime (60-120)."""
        s = _story(
            title="The day I switched from ChatGPT vs Claude vs Gemini",
            duration_seconds=80,
        )
        assert not is_storytime_eligible(s)


class TestPayloadBuilder:
    def test_summary_used_as_narration(self) -> None:
        payload = build_storytime_payload(
            _story(summary="A long narrative summary that clears the 40-char floor easily.")
        )
        assert "narration_text" in payload
        assert payload["narration_text"].startswith("A long narrative")

    def test_narration_text_wins_over_summary(self) -> None:
        """Explicit narration_text on the story dict takes precedence."""
        payload = build_storytime_payload(
            _story(
                narration_text="Explicit override narration that is long enough.",
                summary="A different summary that should be ignored.",
            )
        )
        assert payload["narration_text"].startswith("Explicit override")

    def test_narration_trimmed_to_1500_chars(self) -> None:
        long_narration = "x" * 3000
        payload = build_storytime_payload(_story(narration_text=long_narration))
        assert len(payload["narration_text"]) == 1500

    def test_empty_payload_when_narration_too_short(self) -> None:
        """< 40 chars → empty payload → wire falls back to single_clip."""
        payload = build_storytime_payload(_story(summary="short"))
        assert payload == {}

    def test_empty_payload_when_all_sources_missing(self) -> None:
        s = _story()
        s.pop("summary")
        s.pop("narration_text", None)
        payload = build_storytime_payload(s)
        assert payload == {}

    def test_description_snippet_fallback(self) -> None:
        """No summary + no narration_text → falls back to description_snippet."""
        s = _story()
        s.pop("summary")
        s["description_snippet"] = "A description-snippet fallback narration text of sufficient length."
        payload = build_storytime_payload(s)
        assert "description-snippet fallback" in payload["narration_text"]


class TestPromptSection:
    def test_prompt_present_and_mandate(self) -> None:
        prompt = format_storytime_prompt_section()
        assert "STORYTIME MANDATE" in prompt
        # Prompt words wrap across newlines — check the constituent words
        # rather than a specific contiguous phrase that a re-flow could break.
        assert "narrative" in prompt
        assert "arc" in prompt

    def test_prompt_contains_frameworks(self) -> None:
        prompt = format_storytime_prompt_section()
        assert "ORIGIN" in prompt
        assert "REVELATION" in prompt
        assert "TRANSFORMATION" in prompt
        assert "REVERSAL" in prompt


class TestChooseVariantIntegration:
    """Integration tests: storytime must sit at the bottom of the priority
    chain in the choose_variant orchestrator."""

    def test_storytime_returned_for_eligible_narrative_story(self) -> None:
        from genlab_core.variant_types import choose_variant

        variant, payload = choose_variant(_story())
        assert variant == "storytime"
        assert "narration_text" in payload

    def test_single_clip_returned_when_narration_missing(self) -> None:
        """No narration source → payload builder returns empty → orchestrator
        falls through to single_clip default."""
        from genlab_core.variant_types import choose_variant

        s = _story()
        s.pop("summary")
        s.pop("narration_text", None)
        variant, payload = choose_variant(s)
        assert variant == "single_clip"
        assert payload == {}
