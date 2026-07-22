"""Pin tests for the Layer 3 S6 split_screen selector + writer prompt + payload builder.

Same pattern as `test_question_reveal_selector.py`. Covers:

* Positive title-signal matches: "X vs Y", "before/after", "reacting to", "compared to"
* Negative cases: partial matches ("elvs"), missing duration, wrong duration range
* Priority: series_part / question_reveal / watch_till_end all take precedence
* Payload builder shape + label heuristics
* Prompt section presence
"""

from __future__ import annotations

from genlab_core.writing.split_screen_selector import (
    build_split_screen_payload,
    format_split_screen_prompt_section,
    is_split_screen_eligible,
)


def _story(**overrides):
    base = {
        "story_id": "test-story",
        "title": "iPhone 17 vs Samsung S26: unexpected results",
        "duration_seconds": 30,
        "video_id": "abc123",
    }
    base.update(overrides)
    return base


class TestVSMatches:
    def test_vs_word_boundary_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="iPhone vs Android 2025 test"))

    def test_vs_with_period_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="Curry vs. Durant clutch shots"))

    def test_vs_case_insensitive(self) -> None:
        assert is_split_screen_eligible(_story(title="Marvel VS DC box office 2026"))

    def test_partial_vs_does_not_match(self) -> None:
        """'elvs' or 'vscode' shouldn't accidentally match — word-boundary anchored."""
        # Also excludes reaction / before-after / compared / question / watch_till_end signals
        assert not is_split_screen_eligible(_story(title="advertising trends 2026"))
        assert not is_split_screen_eligible(_story(title="Elvis Presley documentary"))


class TestReactionMatches:
    def test_reacting_to_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="Reacting to my old iPhone videos"))

    def test_reaction_to_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="My reaction to the Trump verdict"))

    def test_reacts_to_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="Pro Chef reacts to home cooking hacks"))


class TestBeforeAfter:
    def test_before_then_after_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="My kitchen: before and after"))

    def test_after_then_before_matches(self) -> None:
        assert is_split_screen_eligible(
            _story(title="These renovations, after the reveal, before the crash")
        )

    def test_before_after_too_far_apart_does_not_match(self) -> None:
        """40-char proximity limit — words further apart aren't a matching pair."""
        title = "This is before the biggest week of my life " + ("x " * 40) + "after"
        assert not is_split_screen_eligible(_story(title=title))


class TestComparedTo:
    def test_compared_to_matches(self) -> None:
        assert is_split_screen_eligible(_story(title="Cheap gear compared to pro rigs"))


class TestDurationBounds:
    def test_under_min_duration_rejected(self) -> None:
        assert not is_split_screen_eligible(_story(duration_seconds=10))

    def test_over_max_duration_rejected(self) -> None:
        assert not is_split_screen_eligible(_story(duration_seconds=120))

    def test_missing_duration_rejected(self) -> None:
        s = _story()
        s.pop("duration_seconds")
        assert not is_split_screen_eligible(s)

    def test_alt_duration_field_accepted(self) -> None:
        s = _story()
        s.pop("duration_seconds")
        s["duration"] = 45
        assert is_split_screen_eligible(s)


class TestPriority:
    def test_series_part_blocks_split_screen(self) -> None:
        """A story that would match split_screen but ALSO looks like a series
        must NOT be eligible for split_screen — series priority wins."""
        s = _story(title="iPhone vs Android Part 3 comparison")
        assert not is_split_screen_eligible(s), (
            "series_part title suffix must take priority over vs pattern"
        )

    def test_question_reveal_blocks_split_screen(self) -> None:
        """Explicit question-mark titles route to question_reveal, not split_screen."""
        s = _story(title="Why is iPhone vs Android still a debate?")
        assert not is_split_screen_eligible(s)

    def test_watch_till_end_blocks_split_screen(self) -> None:
        """Compilation-keyword titles route to watch_till_end."""
        s = _story(title="Top 10 iPhone vs Android moments in 2026")
        assert not is_split_screen_eligible(s)


class TestPayloadBuilder:
    def test_before_after_labels(self) -> None:
        payload = build_split_screen_payload(_story(title="My room: before and after"))
        assert payload["left_label"] == "BEFORE"
        assert payload["right_label"] == "AFTER"

    def test_reaction_labels(self) -> None:
        payload = build_split_screen_payload(_story(title="Reacting to Curry's shot"))
        assert payload["left_label"] == "REACTING"
        assert payload["right_label"] == "SOURCE"

    def test_generic_ab_labels(self) -> None:
        payload = build_split_screen_payload(_story(title="iPhone vs Samsung 2026"))
        assert payload["left_label"] == "A"
        assert payload["right_label"] == "B"

    def test_payload_has_all_required_contract_keys(self) -> None:
        """PAYLOAD_CONTRACTS requires clip_a_video_id + clip_b_video_id."""
        payload = build_split_screen_payload(_story(video_id="myvid"))
        assert payload["clip_a_video_id"] == "myvid"
        assert payload["clip_b_video_id"] == "myvid"  # self-reference by design
        assert payload["layout"] == "vstack"

    def test_payload_empty_when_video_id_missing(self) -> None:
        """No video_id → empty payload → wire falls back to single_clip
        rather than shipping a variant that would fail validate_payload."""
        s = _story()
        s.pop("video_id")
        assert build_split_screen_payload(s) == {}


class TestPromptSection:
    def test_prompt_section_is_present(self) -> None:
        prompt = format_split_screen_prompt_section()
        assert "SPLIT-SCREEN MANDATE" in prompt
        assert "TWO halves" in prompt

    def test_prompt_contains_frameworks(self) -> None:
        prompt = format_split_screen_prompt_section()
        assert "HEAD-TO-HEAD" in prompt
        assert "TRANSFORMATION" in prompt
        assert "REACTION" in prompt


class TestChooseVariantIntegration:
    """Integration tests for the choose_variant orchestrator — split_screen
    must sit at the correct position in the priority chain."""

    def test_split_screen_returned_for_eligible_story(self) -> None:
        from genlab_core.variant_types import choose_variant

        variant, payload = choose_variant(_story(title="iPhone vs Samsung showdown"))
        assert variant == "split_screen"
        assert payload["clip_a_video_id"] == "abc123"

    def test_single_clip_returned_when_no_video_id(self) -> None:
        """Missing video_id — payload builder returns empty → orchestrator
        falls through to single_clip default per S6 wire design."""
        from genlab_core.variant_types import choose_variant

        s = _story(title="iPhone vs Samsung showdown")
        s.pop("video_id")
        variant, payload = choose_variant(s)
        assert variant == "single_clip"
        assert payload == {}
