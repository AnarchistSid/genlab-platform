"""Pin tests for series_detector — Layer 3 S2.

Detection MUST match explicit series indicators + MUST NOT false-fire
on generic enumeration ("Top 5", "3 tips") or ordinal patterns ("1st").

## Why the negative cases matter more than the positives

A false NEGATIVE just publishes as ``single_clip`` — the safe default.
A false POSITIVE marks unrelated content as ``series_part``, corrupting
the bandit reward attribution AND making the writer inject a nonsensical
"this is Part N of X series" prompt into a standalone hook.

The regex was tuned specifically to avoid the false-positive space.
Each negative test here documents a real-world title pattern that
COULD look like a series but isn't.
"""

from __future__ import annotations

from genlab_core.writing.series_detector import (
    SeriesInfo,
    detect_series,
    format_series_prompt_section,
)

# ---------------------------------------------------------------------------
# POSITIVE cases — MUST detect
# ---------------------------------------------------------------------------


class TestPositiveDetection:
    def test_part_number_with_total(self) -> None:
        info = detect_series({"title": "Cyberpunk 2077 Part 3 of 5: The Ending"})
        assert info is not None
        assert info.part_number == 3
        assert info.total_parts == 5
        assert info.detection_pattern == "part_of"

    def test_part_number_slash_total(self) -> None:
        info = detect_series({"title": "Deep Dive Part 2/4"})
        assert info is not None
        assert info.part_number == 2
        assert info.total_parts == 4

    def test_part_no_total(self) -> None:
        info = detect_series({"title": "Elden Ring Playthrough Part 12"})
        assert info is not None
        assert info.part_number == 12
        assert info.total_parts == 12  # falls back to part_number
        assert info.detection_pattern == "part_only"

    def test_pt_abbreviation(self) -> None:
        info = detect_series({"title": "Reaction Pt. 3"})
        assert info is not None
        assert info.part_number == 3

    def test_episode_full_word(self) -> None:
        info = detect_series({"title": "Attack on Titan Episode 22"})
        assert info is not None
        assert info.part_number == 22
        assert info.detection_pattern == "episode_only"

    def test_ep_abbreviation(self) -> None:
        info = detect_series({"title": "Podcast Ep 47"})
        assert info is not None
        assert info.part_number == 47

    def test_season_episode_notation(self) -> None:
        info = detect_series({"title": "The Bear S03E11 Reaction"})
        assert info is not None
        assert info.part_number == 11
        assert info.detection_pattern == "season_episode"

    def test_season_episode_lowercase(self) -> None:
        info = detect_series({"title": "the bear s3e5"})
        assert info is not None
        assert info.part_number == 5

    def test_chapter_pattern(self) -> None:
        info = detect_series({"title": "The Book Chapter 7"})
        assert info is not None
        assert info.part_number == 7
        assert info.detection_pattern == "chapter_only"

    def test_case_insensitive(self) -> None:
        for title in ["PART 3", "part 3", "Part 3", "pArT 3"]:
            info = detect_series({"title": title})
            assert info is not None, f"failed on: {title}"
            assert info.part_number == 3

    def test_series_title_stripped_of_part_indicator(self) -> None:
        info = detect_series({"title": "Cyberpunk 2077 Part 3: The Ending"})
        assert info is not None
        # Part indicator removed + colon separator cleaned
        assert "Part 3" not in info.series_title
        assert "Cyberpunk" in info.series_title
        assert "Ending" in info.series_title


# ---------------------------------------------------------------------------
# NEGATIVE cases — MUST NOT false-fire
# ---------------------------------------------------------------------------


class TestNegativeDetection:
    """Real-world titles that COULD look like series but aren't."""

    def test_top_n_enumeration(self) -> None:
        for title in [
            "Top 5 anime scenes",
            "Best 10 games of 2026",
            "3 tips for beginners",
        ]:
            assert detect_series({"title": title}) is None, f"false positive: {title}"

    def test_ordinal_places(self) -> None:
        for title in [
            "How to win 1st place in Fortnite",
            "The 3rd best moment ever",
        ]:
            assert detect_series({"title": title}) is None, f"false positive: {title}"

    def test_sequel_number_alone(self) -> None:
        """Matrix 3, Iron Man 2, GTA 5 — sequels, not part indicators."""
        for title in [
            "The Matrix 3 trailer",
            "Iron Man 2 review",
            "GTA 5 speedrun",
        ]:
            assert detect_series({"title": title}) is None, f"false positive: {title}"

    def test_random_number_in_title(self) -> None:
        for title in [
            "This 10-year-old just beat the world record",
            "1M views in 24 hours",
            "1080p vs 4K comparison",
        ]:
            assert detect_series({"title": title}) is None, f"false positive: {title}"

    def test_partial_word_matches_rejected(self) -> None:
        """Word boundary — 'chaptered' or 'partition' must NOT match."""
        for title in [
            "The book is chaptered into 5 sections",
            "Disk partition tutorial",
            "particle physics explained",
        ]:
            assert detect_series({"title": title}) is None, f"false positive: {title}"

    def test_empty_or_missing_title(self) -> None:
        assert detect_series({}) is None
        assert detect_series({"title": ""}) is None
        assert detect_series({"title": None}) is None

    def test_out_of_range_part_number(self) -> None:
        """Prevent matching 'Part 2024' (year) or 'Part 999999' (view count)."""
        assert detect_series({"title": "Part 2024 review"}) is None
        assert detect_series({"title": "Part 999999"}) is None


# ---------------------------------------------------------------------------
# series_id stability
# ---------------------------------------------------------------------------


class TestSeriesId:
    def test_same_series_same_id_across_calls(self) -> None:
        s1 = detect_series({"title": "Elden Ring Part 3", "channel_id": "UC_abc"})
        s2 = detect_series({"title": "Elden Ring Part 4", "channel_id": "UC_abc"})
        assert s1 is not None and s2 is not None
        # Same series title (after stripping) + same channel → same ID
        assert s1.series_id == s2.series_id

    def test_different_channel_different_id(self) -> None:
        s1 = detect_series({"title": "Elden Ring Part 3", "channel_id": "UC_abc"})
        s2 = detect_series({"title": "Elden Ring Part 3", "channel_id": "UC_xyz"})
        assert s1 is not None and s2 is not None
        assert s1.series_id != s2.series_id

    def test_no_channel_id_still_generates_id(self) -> None:
        info = detect_series({"title": "Cyberpunk Part 3"})
        assert info is not None
        assert info.series_id  # non-empty


# ---------------------------------------------------------------------------
# format_series_prompt_section
# ---------------------------------------------------------------------------


class TestFormatSeriesPromptSection:
    def _info(self, part=3, total=5, title="Test Series") -> SeriesInfo:
        return SeriesInfo(
            series_id="test1234abcd",
            part_number=part,
            total_parts=total,
            series_title=title,
            detection_pattern="part_of",
        )

    def test_includes_part_and_total_when_known(self) -> None:
        section = format_series_prompt_section(self._info(part=3, total=5))
        assert "Part 3 of 5" in section
        assert "SERIES CONTEXT" in section

    def test_part_only_when_total_equals_part(self) -> None:
        section = format_series_prompt_section(self._info(part=3, total=3))
        assert "Part 3" in section
        assert "Part 3 of 3" not in section  # avoid awkward "of self" wording

    def test_series_title_included(self) -> None:
        section = format_series_prompt_section(self._info(title="Elden Ring Playthrough"))
        assert "Elden Ring Playthrough" in section

    def test_prompt_references_subscribe_algorithm(self) -> None:
        """The prompt should educate the LLM on WHY series matter, not just
        state facts. This is the algorithmic-intent framing per the audit."""
        section = format_series_prompt_section(self._info())
        assert "subscribe" in section.lower() or "algorithm" in section.lower()
