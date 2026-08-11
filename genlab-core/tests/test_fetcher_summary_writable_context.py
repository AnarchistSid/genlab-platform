"""2026-08-12: pin that all gaming local fetchers emit summaries
that clear the writer's `_has_writable_context` >=40 char floor.

Motivating class-of-bug (see memory:
`class-of-bug-fetcher-schema-drift-from-downstream-contract`):

* Writer's base_writing._has_writable_context requires
  summary/description_snippet/description >= 40 chars.
* Twitch fetcher was emitting "Twitch clip by <broadcaster>"
  (~15-25 chars). Steam fetcher was emitting "Official trailer
  for <game_name>" (marginal for short game names like "Wu Kong").
* Every gaming story failed the floor -> `excluded_incomplete_content`
  filter -> `blueprints_count=0` per run -> gaming pipeline
  produced no output for weeks.

These pins ensure any future fetcher regression is caught at CI
rather than at a "gaming published 0 blueprints again" audit
5 days later.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.fetch_steam_trailers import (
    _WRITER_MIN_CONTEXT_CHARS as _STEAM_FLOOR,
    _build_steam_summary,
)
from genlab_core.pipeline.stages.fetch_twitch_clips import (
    _WRITER_MIN_CONTEXT_CHARS as _TWITCH_FLOOR,
    _build_twitch_summary,
)


class TestTwitchSummaryClearsFloor:
    """Even the most degenerate Twitch clip (terse title + short
    broadcaster name + zero views + zero duration) must clear the
    40-char floor after synthesis."""

    def test_terse_title_still_clears_floor(self):
        s = _build_twitch_summary(
            title="gg",
            broadcaster="x",
            view_count=0,
            duration=0.0,
        )
        assert len(s) >= _TWITCH_FLOOR, (
            f"summary {s!r} ({len(s)} chars) below floor {_TWITCH_FLOOR}"
        )

    def test_typical_gaming_clip_readable(self):
        """A typical Twitch clip should produce a natural-language
        summary that reads well — not just meets the floor."""
        s = _build_twitch_summary(
            title="INSANE 1v5 CLUTCH IN RANKED",
            broadcaster="Kai_Cenat",
            view_count=12345,
            duration=45.0,
        )
        assert "1V5" in s.upper()
        assert "Kai_Cenat" in s
        assert "12,345" in s  # thousands separator preserved
        assert "45" in s

    def test_empty_broadcaster_still_clears_floor(self):
        """Empty broadcaster (very rare — Helix API always returns it,
        but defensive) — the title + view/duration must still pad it
        past the floor."""
        s = _build_twitch_summary(
            title="a",
            broadcaster="",
            view_count=0,
            duration=0.0,
        )
        assert len(s) >= _TWITCH_FLOOR

    def test_float_duration_formatted_without_decimal(self):
        """Duration is a float from Helix (e.g. 30.5) — display as
        an integer to avoid awkward "30.500000000s" formatting."""
        s = _build_twitch_summary(
            title="clutch",
            broadcaster="streamer",
            view_count=100,
            duration=30.5,
        )
        assert "30s" in s
        assert "30.5" not in s


class TestSteamSummaryClearsFloor:
    """Steam summaries must clear the floor for both cases:
    short_description present (typical) and absent (rare)."""

    def test_short_description_path_reads_well(self):
        s = _build_steam_summary(
            game_name="Elden Ring",
            movie_name="Launch Trailer",
            short_description=(
                "THE NEW FANTASY ACTION RPG. Rise, Tarnished, "
                "and be guided by grace to brandish the power of the Elden Ring."
            ),
            app_id=1245620,
        )
        assert len(s) >= _STEAM_FLOOR
        assert "Elden Ring" in s
        assert "Launch Trailer" in s
        assert "Tarnished" in s

    def test_no_short_description_fallback_clears_floor(self):
        """Rare case: game has no short_description. Synthesized
        fallback with movie_name + app_id must still clear the floor."""
        s = _build_steam_summary(
            game_name="Wu",  # very short game name
            movie_name="Trailer",
            short_description="",
            app_id=12345,
        )
        assert len(s) >= _STEAM_FLOOR

    def test_summary_capped_at_500_chars(self):
        """Long short_description must be truncated to 500 chars so
        downstream fixed-width text ops (LLM prompts, DB columns)
        don't blow up."""
        s = _build_steam_summary(
            game_name="Test",
            movie_name="Trailer",
            short_description="X" * 1000,
            app_id=1,
        )
        assert len(s) <= 500

    def test_short_description_takes_priority(self):
        """When short_description is present, its content must appear
        in the summary — not silently ignored in favor of the fallback."""
        marker = "unique-marker-string-abc123"
        s = _build_steam_summary(
            game_name="Test",
            movie_name="Trailer",
            short_description=marker,
            app_id=1,
        )
        assert marker in s


class TestFloorConstantsAlignWithWriter:
    """The 40-char floor MUST match the writer's own constant. If the
    writer moves the floor, both fetchers need to update simultaneously.
    """

    def test_twitch_floor_matches_writer(self):
        # base_writing._has_writable_context uses 40 as the floor.
        # If it moves, our fetchers must move too.
        assert _TWITCH_FLOOR == 40

    def test_steam_floor_matches_writer(self):
        assert _STEAM_FLOOR == 40

    def test_both_fetchers_agree(self):
        assert _TWITCH_FLOOR == _STEAM_FLOOR
