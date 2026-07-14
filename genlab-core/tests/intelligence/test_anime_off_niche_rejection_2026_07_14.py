"""Pin tests for the 2026-07-14 anime relevance filter fix.

Session 2026-07-14 comprehensive audit found ~15 non-anime videos
routed to the anime niche with score=0.36 (just above the 0.35
threshold). Prod content_pool query revealed:

  - Tennis TV clips (Sinner/Zverev/Switzerland matches)
  - Marvel/Avengers Doomsday breakdowns
  - House of the Dragon Season 3 episode reviews
  - Movie roasts (Artemis Fowl)
  - Stephen King HBO adaptations

Root cause: anime's 204-item positive keyword list includes generic
English words that appear in ANY TV/movie content:
  - "season", "episode", "arc", "opening", "ending"
  - "movie", "special", "op", "ed", "sub", "dub"

3 ambiguous hits × 0.6/normalizer(5) = 0.36 → crosses 0.35 threshold.

Fix: add hard-reject negative keywords for the franchises + generic
markers that surfaced in the audit. Anime score → 0.0 regardless of
ambiguous positive hits.
"""

from __future__ import annotations

import pytest

from genlab_core.intelligence.niche_classifier import NicheClassifier


@pytest.fixture(scope="module")
def classifier() -> NicheClassifier:
    return NicheClassifier()


class TestAnimeHardRejects:
    """Prod content_pool titles that MUST score 0.0 on anime post-fix."""

    @pytest.mark.parametrize(
        "title",
        [
            # Tennis TV
            "Poetry in motion ✨",  # Tennis TV — no anime signal but generic
            "Sinner x Zverev Hits Different 😮‍💨",
            "When Switzerland Meets Argentina 🇨🇭🇦🇷",
            # Marvel
            "HUGE Avengers Doomsday Clues Revealed at Shanghai Expo!",
            "AVENGERS DOOMSDAY TEASER & Comic Con Panel Breakdown",
            # House of the Dragon
            "'House of the Dragon' Season 3 Grinds to a Halt",
            "House of the Dragon Season 3 Episode 4 Recap",
            "Is Sunfyre, Aegon's Dragon, Still Alive in House of the Dragon Season 3?!",
            # Stephen King HBO
            "Stephen King's First Ever Adaptation Of 47-Year-Old Thriller Is Officially HBO",
            # Movie roasts (Artemis Fowl)
            "ARTEMIS FOWL - Movie Roast",
        ],
    )
    def test_off_niche_video_scores_zero_on_anime(self, classifier, title):
        """Hard-reject via negative keywords → anime score = 0.0."""
        scores = classifier.classify(title, description="")
        anime_score = scores.get("anime", 1.0)
        assert anime_score == 0.0, (
            f"Off-niche title scored {anime_score} on anime — expected 0.0 "
            f"via negative-keyword hard-reject. Title: {title!r}"
        )

    @pytest.mark.parametrize(
        "title",
        [
            # Tennis
            "Sinner x Zverev Hits Different",
            # Marvel
            "AVENGERS DOOMSDAY TEASER Breakdown",
            # HotD
            "House of the Dragon Season 3 Episode 4 Recap",
        ],
    )
    def test_off_niche_not_routed_to_anime(self, classifier, title):
        """The full classify_and_route path — anime must NOT appear
        in routed_niches for these titles."""
        _scores, routed = classifier.classify_and_route(title, description="")
        assert "anime" not in routed, (
            f"Off-niche title routed to anime: {routed}. Title: {title!r}"
        )


class TestAnimePositiveContentStillMatches:
    """Regression pin: real anime content must still score above the
    0.35 threshold. Removing/tightening negatives should never
    accidentally block genuine anime."""

    @pytest.mark.parametrize(
        "title",
        [
            "Chainsaw Man Season 2 Episode 1 review — insane fight",
            "Attack on Titan Final Season arc analysis",
            "Jujutsu Kaisen manga chapter breakdown — Sukuna vs Gojo",
            "Best isekai anime this season — otaku picks",
            "Demon Slayer anime — studio Ufotable animation quality",
        ],
    )
    def test_real_anime_still_routes_to_anime(self, classifier, title):
        _scores, routed = classifier.classify_and_route(title, description="")
        assert "anime" in routed, (
            f"Real anime content did NOT route to anime: {routed}. Title: {title!r}"
        )
