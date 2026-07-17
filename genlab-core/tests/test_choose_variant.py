"""Pin tests for choose_variant orchestrator — Layer 3 S4.5.

Validates that the priority chain is enforced correctly for all
combinations of matching selectors + that fail-open works even if
selector modules explode.

## Why this test file lives at tests/ root (not tests/writing/)

choose_variant is at the top of ``genlab_core`` (module
``genlab_core.variant_types``), not under ``writing/``. Placing the
test alongside the module it tests keeps the discovery pattern clean.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.variant_types import DEFAULT_VARIANT, choose_variant


class TestPositivePerVariant:
    """One test per variant type verifying it correctly wins its slot."""

    def test_series_wins(self) -> None:
        story = {"title": "Elden Ring Playthrough Part 3 of 5", "duration_seconds": 45}
        variant, payload = choose_variant(story)
        assert variant == "series_part"
        assert payload["part_number"] == 3
        assert payload["total_parts"] == 5

    def test_question_reveal_wins_when_no_series(self) -> None:
        story = {
            "title": "How did Curry hit this shot from 40 feet?",
            "duration_seconds": 45,
        }
        variant, payload = choose_variant(story)
        assert variant == "question_reveal"
        assert payload == {}

    def test_watch_till_end_wins_when_neither(self) -> None:
        story = {
            "title": "Top 10 Elden Ring boss fights",
            "duration_seconds": 55,
        }
        variant, payload = choose_variant(story)
        assert variant == "watch_till_end"
        assert payload == {}

    def test_single_clip_default(self) -> None:
        story = {
            "title": "New Elden Ring DLC trailer",
            "duration_seconds": 55,
        }
        variant, payload = choose_variant(story)
        assert variant == DEFAULT_VARIANT == "single_clip"
        assert payload == {}


class TestPriorityChain:
    """Priority: series > question_reveal > watch_till_end > single_clip."""

    def test_series_beats_question_reveal(self) -> None:
        """Question + Part indicator → series wins."""
        story = {"title": "How does this attack work Part 3?", "duration_seconds": 45}
        variant, _ = choose_variant(story)
        assert variant == "series_part"

    def test_series_beats_watch_till_end(self) -> None:
        """Compilation + Part indicator → series wins."""
        story = {"title": "NBA Highlights Part 3", "duration_seconds": 45}
        variant, _ = choose_variant(story)
        assert variant == "series_part"

    def test_question_reveal_beats_watch_till_end(self) -> None:
        """Question + compilation keyword → question_reveal wins."""
        story = {"title": "How are these clips ranked?", "duration_seconds": 45}
        variant, _ = choose_variant(story)
        assert variant == "question_reveal"

    def test_all_three_matches_series_wins(self) -> None:
        """A pathological title matching all three: series still wins."""
        story = {
            "title": "How are these highlights ranked Part 3?",
            "duration_seconds": 45,
        }
        variant, _ = choose_variant(story)
        assert variant == "series_part"


class TestFailOpen:
    """No selector exception should ever crash the orchestrator."""

    def test_series_detector_crash_falls_through(self) -> None:
        """If detect_series raises, question_reveal gets a fair shot."""
        story = {"title": "How did this happen?", "duration_seconds": 45}
        with patch(
            "genlab_core.writing.series_detector.detect_series",
            side_effect=RuntimeError("simulated"),
        ):
            variant, _ = choose_variant(story)
            # question_reveal should still fire — its own selector
            # doesn't depend on the series detector being importable
            assert variant in ("question_reveal", "single_clip")
            # single_clip is acceptable IF the question_reveal selector's
            # own detect_series call also fails (patch replaces the
            # module-level function everywhere). Either way, no crash.

    def test_all_selectors_crash_returns_single_clip(self) -> None:
        story = {"title": "How did Curry hit this?", "duration_seconds": 45}
        with (
            patch(
                "genlab_core.writing.series_detector.detect_series",
                side_effect=RuntimeError("simulated 1"),
            ),
            patch(
                "genlab_core.writing.question_reveal_selector.is_question_reveal_eligible",
                side_effect=RuntimeError("simulated 2"),
            ),
            patch(
                "genlab_core.writing.watch_till_end_selector.is_watch_till_end_eligible",
                side_effect=RuntimeError("simulated 3"),
            ),
        ):
            variant, payload = choose_variant(story)
            assert variant == "single_clip"
            assert payload == {}

    def test_empty_story_returns_default(self) -> None:
        variant, payload = choose_variant({})
        assert variant == "single_clip"
        assert payload == {}

    def test_none_fields_return_default(self) -> None:
        variant, payload = choose_variant({"title": None, "duration_seconds": None})
        assert variant == "single_clip"
        assert payload == {}


class TestPayloadShape:
    """Payload contract per variant matches PAYLOAD_CONTRACTS."""

    def test_series_payload_has_required_keys(self) -> None:
        from genlab_core.variant_types import PAYLOAD_CONTRACTS

        story = {"title": "Elden Ring Part 3", "duration_seconds": 45}
        variant, payload = choose_variant(story)
        assert variant == "series_part"
        for key in PAYLOAD_CONTRACTS["series_part"]:
            assert key in payload, f"series_part payload missing required key: {key}"

    def test_watch_till_end_payload_empty(self) -> None:
        story = {"title": "Top 10 plays", "duration_seconds": 45}
        variant, payload = choose_variant(story)
        assert variant == "watch_till_end"
        assert payload == {}

    def test_question_reveal_payload_empty(self) -> None:
        story = {"title": "How did this happen?", "duration_seconds": 45}
        variant, payload = choose_variant(story)
        assert variant == "question_reveal"
        assert payload == {}
