"""Pin tests for watch_till_end_selector — Layer 3 S3.

## Why selectivity matters

Unlike ``series_detector`` (which fires whenever a source title
signals series), ``watch_till_end`` is a WRITING STRATEGY choice. If
the selector is too permissive, every video gets the mandate and we
lose signal (no comparison against single_clip baseline). If too
restrictive, we never get enough samples for the S5 bandit to learn
which framing performs better.

Target: 10-20% of trending fetches should match. The compilation
keyword list was tuned against real gaming/sports/anime titles from
30 days of trending fetches to hit that range.

## Priority coverage

Series_part MUST take priority — variant_type is exclusive on a
blueprint. A "Cyberpunk Highlights Part 3" title matches both
detectors, but only series_part should apply. This is enforced by
``is_watch_till_end_eligible`` calling ``detect_series`` first.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.writing.watch_till_end_selector import (
    format_watch_till_end_prompt_section,
    is_watch_till_end_eligible,
)


class TestPositiveEligibility:
    """Real trending-title patterns that SHOULD fire."""

    def test_top_n_compilation(self) -> None:
        story = {
            "title": "Top 10 Elden Ring boss fights",
            "duration_seconds": 55,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_highlights_variant(self) -> None:
        story = {
            "title": "NBA best moments — regular season highlights",
            "duration_seconds": 45,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_reactions_content(self) -> None:
        story = {
            "title": "Fan reactions to the season finale",
            "duration_seconds": 60,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_compilation_word(self) -> None:
        story = {
            "title": "Gaming rage compilation 2026",
            "duration_seconds": 75,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_montage_word(self) -> None:
        story = {
            "title": "Sports montage of the year",
            "duration_seconds": 40,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_recap_word(self) -> None:
        story = {
            "title": "Week 12 anime recap",
            "duration_seconds": 50,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_duration_at_lower_bound(self) -> None:
        story = {"title": "Top 5 plays", "duration_seconds": 30}
        assert is_watch_till_end_eligible(story) is True

    def test_duration_at_upper_bound(self) -> None:
        story = {"title": "Compilation of moments", "duration_seconds": 90}
        assert is_watch_till_end_eligible(story) is True


class TestNegativeEligibility:
    """Real trending-title patterns that MUST NOT fire."""

    def test_no_compilation_keyword(self) -> None:
        story = {
            "title": "New Elden Ring DLC trailer",
            "duration_seconds": 55,
        }
        assert is_watch_till_end_eligible(story) is False

    def test_duration_too_short(self) -> None:
        story = {"title": "Top 10 plays", "duration_seconds": 20}
        assert is_watch_till_end_eligible(story) is False

    def test_duration_too_long(self) -> None:
        story = {"title": "Best moments", "duration_seconds": 120}
        assert is_watch_till_end_eligible(story) is False

    def test_missing_duration(self) -> None:
        story = {"title": "Top 10 plays"}
        assert is_watch_till_end_eligible(story) is False

    def test_missing_title(self) -> None:
        story = {"duration_seconds": 45}
        assert is_watch_till_end_eligible(story) is False

    def test_empty_dict(self) -> None:
        assert is_watch_till_end_eligible({}) is False

    def test_ambiguous_keyword_topic_rejected(self) -> None:
        """``topic``, ``topple`` shouldn't match ``top ``."""
        story = {"title": "Trending topic today", "duration_seconds": 45}
        assert is_watch_till_end_eligible(story) is False

    def test_ambiguous_keyword_highlighted_rejected(self) -> None:
        """``highlighted`` shouldn't match ``highlight ``."""
        story = {
            "title": "The player highlighted a key issue",
            "duration_seconds": 45,
        }
        assert is_watch_till_end_eligible(story) is False

    def test_invalid_duration_type_fails_open(self) -> None:
        story = {"title": "Top 10 plays", "duration_seconds": "not a number"}
        assert is_watch_till_end_eligible(story) is False


class TestSeriesPriority:
    """Series_part MUST take priority — variants are exclusive on a blueprint."""

    def test_series_title_short_circuits(self) -> None:
        """A "Highlights Part 3" title matches both, but series wins."""
        story = {
            "title": "NBA Highlights Part 3",
            "duration_seconds": 45,
            "channel_id": "UC_test",
        }
        assert is_watch_till_end_eligible(story) is False

    def test_episode_title_short_circuits(self) -> None:
        story = {
            "title": "Best moments Episode 22",
            "duration_seconds": 60,
        }
        assert is_watch_till_end_eligible(story) is False

    def test_non_series_still_eligible(self) -> None:
        """Sanity check: without series indicator, same title fires."""
        story = {
            "title": "NBA Highlights of the Week",
            "duration_seconds": 45,
        }
        assert is_watch_till_end_eligible(story) is True

    def test_series_detector_exception_fails_open(self) -> None:
        """If detect_series raises, selector must return False (safe default)."""
        story = {"title": "Top 10 plays", "duration_seconds": 45}
        with patch(
            "genlab_core.writing.watch_till_end_selector.detect_series",
            side_effect=RuntimeError("simulated failure"),
        ):
            assert is_watch_till_end_eligible(story) is False


class TestPromptSection:
    def test_section_includes_mandate_marker(self) -> None:
        section = format_watch_till_end_prompt_section()
        assert "WATCH-TILL-END MANDATE" in section

    def test_section_includes_frameworks(self) -> None:
        section = format_watch_till_end_prompt_section()
        # At least one framework name present
        assert "COUNTDOWN" in section or "SPECIFIC-TIMESTAMP" in section

    def test_section_includes_avoid_guidance(self) -> None:
        """Prompt must call out anti-patterns (front-loading, vague teases)."""
        section = format_watch_till_end_prompt_section()
        assert "AVOID" in section
        assert "Front-loading" in section or "front-load" in section.lower()

    def test_section_allows_llm_to_skip(self) -> None:
        """LLM should return empty hook rather than invent a fake payoff."""
        section = format_watch_till_end_prompt_section()
        assert "empty hook" in section or "skip" in section
