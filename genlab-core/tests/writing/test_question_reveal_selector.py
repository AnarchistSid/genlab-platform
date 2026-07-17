"""Pin tests for question_reveal_selector — Layer 3 S4a.

Detection MUST match structured question titles (question word + "?").
MUST NOT false-fire on rhetorical questions embedded in longer titles,
declarative titles that happen to contain a question word, or titles
that end in "?" but don't START with a question word.

## Priority coverage

Series wins over question_reveal (variants exclusive). Question_reveal
vs watch_till_end is enforced at the wire level, not the selector, so
this file only tests the series-priority short-circuit.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.writing.question_reveal_selector import (
    format_question_reveal_prompt_section,
    is_question_reveal_eligible,
)


class TestPositiveEligibility:
    """Real trending-title patterns shaped as questions."""

    def test_how_question(self) -> None:
        story = {
            "title": "How did Curry hit this shot from 40 feet?",
            "duration_seconds": 45,
        }
        assert is_question_reveal_eligible(story) is True

    def test_why_question(self) -> None:
        story = {
            "title": "Why is Anthropic locking their own AI in a vault?",
            "duration_seconds": 60,
        }
        assert is_question_reveal_eligible(story) is True

    def test_what_question(self) -> None:
        story = {
            "title": "What actually makes this attack unblockable?",
            "duration_seconds": 50,
        }
        assert is_question_reveal_eligible(story) is True

    def test_who_question(self) -> None:
        story = {
            "title": "Who really decided to cancel this show?",
            "duration_seconds": 35,
        }
        assert is_question_reveal_eligible(story) is True

    def test_can_question(self) -> None:
        story = {
            "title": "Can this AI really beat Claude at coding?",
            "duration_seconds": 70,
        }
        assert is_question_reveal_eligible(story) is True

    def test_case_insensitive_prefix(self) -> None:
        for prefix in ["HOW", "how", "How", "hOw"]:
            story = {
                "title": f"{prefix} did this happen?",
                "duration_seconds": 45,
            }
            assert is_question_reveal_eligible(story) is True, f"failed on: {prefix}"

    def test_duration_at_lower_bound(self) -> None:
        story = {"title": "How did this happen?", "duration_seconds": 30}
        assert is_question_reveal_eligible(story) is True

    def test_duration_at_upper_bound(self) -> None:
        story = {"title": "Why is this significant?", "duration_seconds": 90}
        assert is_question_reveal_eligible(story) is True


class TestNegativeEligibility:
    """Titles that superficially look like questions but shouldn't fire."""

    def test_no_question_mark_at_end(self) -> None:
        story = {"title": "How to beat this boss", "duration_seconds": 45}
        assert is_question_reveal_eligible(story) is False

    def test_question_mark_but_no_question_prefix(self) -> None:
        """Ends with ? but doesn't start with a question word."""
        story = {
            "title": "This is insane, isn't it?",
            "duration_seconds": 45,
        }
        assert is_question_reveal_eligible(story) is False

    def test_question_word_not_at_start(self) -> None:
        """Question word appears mid-title but title isn't structured as a question."""
        story = {
            "title": "The video shows how Curry hits shots",
            "duration_seconds": 45,
        }
        assert is_question_reveal_eligible(story) is False

    def test_duration_too_short(self) -> None:
        story = {"title": "How did this happen?", "duration_seconds": 15}
        assert is_question_reveal_eligible(story) is False

    def test_duration_too_long(self) -> None:
        story = {"title": "How did this happen?", "duration_seconds": 180}
        assert is_question_reveal_eligible(story) is False

    def test_missing_duration(self) -> None:
        story = {"title": "How did this happen?"}
        assert is_question_reveal_eligible(story) is False

    def test_missing_title(self) -> None:
        story = {"duration_seconds": 45}
        assert is_question_reveal_eligible(story) is False

    def test_empty_dict(self) -> None:
        assert is_question_reveal_eligible({}) is False

    def test_word_boundary_prevents_partial_match(self) -> None:
        """'Howie' or 'Whatever' shouldn't match the question-word prefix."""
        story = {
            "title": "Howie Mandel reacts to the video?",
            "duration_seconds": 45,
        }
        # "Howie" doesn't match `how\b` — word boundary
        assert is_question_reveal_eligible(story) is False

    def test_invalid_duration_type_fails_open(self) -> None:
        story = {
            "title": "How did this happen?",
            "duration_seconds": "not-a-number",
        }
        assert is_question_reveal_eligible(story) is False


class TestSeriesPriority:
    """Series_part wins over question_reveal — variants exclusive on blueprint."""

    def test_series_title_short_circuits(self) -> None:
        """A "How to X - Part 3?" title matches both, but series wins."""
        story = {
            "title": "How does this attack work Part 3?",
            "duration_seconds": 45,
        }
        assert is_question_reveal_eligible(story) is False

    def test_episode_title_short_circuits(self) -> None:
        story = {
            "title": "Why did they do this? Episode 22",
            "duration_seconds": 60,
        }
        assert is_question_reveal_eligible(story) is False

    def test_series_detector_exception_fails_open(self) -> None:
        """If detect_series raises, selector must return False (safe default)."""
        story = {"title": "How did this happen?", "duration_seconds": 45}
        with patch(
            "genlab_core.writing.question_reveal_selector.detect_series",
            side_effect=RuntimeError("simulated failure"),
        ):
            assert is_question_reveal_eligible(story) is False


class TestPromptSection:
    def test_section_includes_mandate_marker(self) -> None:
        section = format_question_reveal_prompt_section()
        assert "QUESTION-REVEAL MANDATE" in section

    def test_section_includes_frameworks(self) -> None:
        section = format_question_reveal_prompt_section()
        assert "MYSTERY" in section or "PARADOX" in section

    def test_section_requires_literal_question_mark(self) -> None:
        section = format_question_reveal_prompt_section()
        # Prompt must explicitly instruct the LLM that hook must end with "?"
        assert "'?'" in section or "?" in section

    def test_section_calls_out_anti_patterns(self) -> None:
        section = format_question_reveal_prompt_section()
        assert "AVOID" in section
        assert "Rhetorical" in section or "clickbait" in section.lower()

    def test_section_allows_llm_to_skip(self) -> None:
        section = format_question_reveal_prompt_section()
        assert "empty hook" in section or "skip" in section
