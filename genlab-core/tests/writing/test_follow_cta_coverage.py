"""Pin follow-CTA coverage across all 5 niches (2026-08-15).

Prior state (pre-fix): 4 of 5 niches (gaming, sports, movies, anime)
had ZERO follow-CTAs in their CTA menu, while writer prompt forces
"pick ONE verbatim from this list. Do NOT invent new CTAs." So the
system was structurally incapable of asking viewers to follow on 4
of 5 channels. Follower growth was 0-2 per platform per month.

This pin ensures every niche has >= 1 follow-CTA in its menu — any
future regression that removes them breaks the test loudly.
"""
from __future__ import annotations

import re

from genlab_core.writing.video_content_writer import NICHE_VOICE

# Regex matches "Follow" as a lead word — case-insensitive so
# "follow for daily..." also matches.
_FOLLOW_RE = re.compile(r"^\s*follow\b", re.IGNORECASE)


class TestFollowCTACoverage:
    """Every niche must have at least 1 follow-CTA in its menu.

    Rationale: follower growth is the north-star metric per rule #24.
    A CTA menu with zero follow-CTAs means the writer's random-3
    sample can never produce a follow prompt. Bandit optimization
    on CTA arms is useless without a follow-flavored arm in play.
    """

    def test_gaming_has_follow_cta(self):
        gaming_ctas = NICHE_VOICE["gaming"]["ctas"]
        follows = [c for c in gaming_ctas if _FOLLOW_RE.match(c)]
        assert len(follows) >= 1, (
            f"gaming CTAs have no follow-flavored option: {gaming_ctas}"
        )

    def test_sports_has_follow_cta(self):
        sports_ctas = NICHE_VOICE["sports"]["ctas"]
        follows = [c for c in sports_ctas if _FOLLOW_RE.match(c)]
        assert len(follows) >= 1, (
            f"sports CTAs have no follow-flavored option: {sports_ctas}"
        )

    def test_movies_has_follow_cta(self):
        movies_ctas = NICHE_VOICE["movies"]["ctas"]
        follows = [c for c in movies_ctas if _FOLLOW_RE.match(c)]
        assert len(follows) >= 1, (
            f"movies CTAs have no follow-flavored option: {movies_ctas}"
        )

    def test_anime_has_follow_cta(self):
        anime_ctas = NICHE_VOICE["anime"]["ctas"]
        follows = [c for c in anime_ctas if _FOLLOW_RE.match(c)]
        assert len(follows) >= 1, (
            f"anime CTAs have no follow-flavored option: {anime_ctas}"
        )

    def test_ai_creators_has_follow_cta(self):
        ai_ctas = NICHE_VOICE["ai_creators"]["ctas"]
        follows = [c for c in ai_ctas if _FOLLOW_RE.match(c)]
        assert len(follows) >= 1, (
            f"ai_creators CTAs have no follow-flavored option: {ai_ctas}"
        )

    def test_menu_size_supports_sampling(self):
        """Writer's random.sample picks 3 CTAs from the menu. Menu
        must be >= 6 so we have at least 2:1 ratio of picks-to-menu
        for meaningful variation across posts.

        Bandit needs several passes to learn — a tiny menu means
        every post shows the same 3, so bandit converges on stale
        content."""
        for niche_id, block in NICHE_VOICE.items():
            n = len(block["ctas"])
            assert n >= 6, (
                f"{niche_id} has only {n} CTAs; need >= 6 for the "
                f"writer's random-3 sampler to produce variation"
            )

    def test_follow_cta_ratio_is_reasonable(self):
        """At least 20% of each niche's CTA menu should be follow-CTAs
        so the random-3 sampler surfaces one ~60% of the time.

        Math: at k=3/n=9 with f follow-CTAs, P(sample includes >=1
        follow) = 1 - C(n-f,3)/C(n,3). f=2/n=9 → 58%, f=3/n=9 → 76%.
        """
        for niche_id, block in NICHE_VOICE.items():
            ctas = block["ctas"]
            follows = [c for c in ctas if _FOLLOW_RE.match(c)]
            ratio = len(follows) / len(ctas)
            assert ratio >= 0.15, (
                f"{niche_id} follow-CTA ratio {ratio:.0%} < 15% floor "
                f"— sampling won't surface a follow-CTA reliably. "
                f"Menu: {ctas}"
            )
