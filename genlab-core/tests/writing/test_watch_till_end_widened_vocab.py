"""2026-08-12: pin the widened watch_till_end vocab against real
recent prod titles.

Motivating investigation: 125/126 blueprints in a 14d window defaulted
to `single_clip` because none of the strict compilation-keyword vocab
(`highlights`, `top`, `moments`, etc) matched sports-news-shaped
titles ("Islam knocks out Volkanovski"), anime season markers
("Grand Blue Dreaming Season 3"), or movie trailers ("American
Doctor | Official Trailer"). Widened vocab now catches these shapes
so the bandit has variant-comparison data to learn from.

These pins are structural — they lock in that specific real-world
title shapes now match the selector. A future edit that removes
one of these keywords will fire the test with a clear message about
the incident.
"""

from __future__ import annotations

import pytest

from genlab_core.writing.watch_till_end_selector import (
    is_watch_till_end_eligible,
)


REAL_TITLES_THAT_SHOULD_MATCH = [
    # Sports (news-shape). NB: "vs" titles are excluded — they route
    # to the more-specific split_screen variant instead.
    ("Islam Makhachev knocks out Alexander Volkanovski", "sports/knocks_out"),
    ("Team defeats reigning champion in overtime", "sports/defeats"),
    ("Rangers beats Blackhawks 4-2", "sports/beats"),
    # Movies (trailer/teaser-shape)
    ("Josephine | Official Teaser (2026)", "movies/teaser"),
    ("American Doctor (2026) | Official Trailer", "movies/trailer"),
    ("Reveal Trailer for Untitled Marvel Project", "movies/reveal_trailer"),
    ("First look at the new Bond film", "movies/first_look"),
    # Anime (serial-shape)
    ("Grand Blue Dreaming Season 3", "anime/season"),
    ("Skeleton Knight in Another World Season 2", "anime/season"),
    # NB: "Episode N" titles trigger series_part (higher priority) —
    # they get an even stronger variant, not watch_till_end. Not
    # listed here because they're eligible for a BETTER variant.
]

REAL_TITLES_THAT_SHOULD_NOT_MATCH = [
    # Pure show/game names — no signal
    ("Escape from Tarkov", "gaming/bare_name"),
    ("Rainbow Six Siege", "gaming/bare_name"),
    ("THE GHOST IN THE SHELL", "anime/bare_name"),
    # AI/tutorial content
    ("Fix GPT-Image 2's Ugly Grain Problem", "ai_creators/tutorial"),
]


class TestWidenedVocabMatchesRealTitles:
    @pytest.mark.parametrize(
        "title,label", REAL_TITLES_THAT_SHOULD_MATCH
    )
    def test_should_match(self, title, label):
        """Widened vocab MUST match these real-shape titles. Duration
        set to 60s (mid-range) so only the keyword logic is under test."""
        story = {"title": title, "duration_seconds": 60}
        assert is_watch_till_end_eligible(story) is True, (
            f"{label}: {title!r} should match watch_till_end after "
            "widened vocab. Regression means a keyword was removed."
        )

    @pytest.mark.parametrize(
        "title,label", REAL_TITLES_THAT_SHOULD_NOT_MATCH
    )
    def test_should_not_match(self, title, label):
        """Bare show/game names + tutorial content have no payoff-
        promise structure — must still fall through to single_clip."""
        story = {"title": title, "duration_seconds": 60}
        assert is_watch_till_end_eligible(story) is False, (
            f"{label}: {title!r} incorrectly matches watch_till_end. "
            "Widened vocab is over-matching."
        )
