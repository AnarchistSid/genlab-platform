"""Pin tests for the 2026-07-14 arm keyword coverage expansion.

Session 2026-07-14 audit found that only 3 of 939 pending_feedback rows
had usable ``propensity`` values, starving the doubly-robust reward
replay engine (Intervention 7). Root cause: 67-88% of production stories
keyword-matched ZERO arms → default arm assigned → propensity=None →
DR-replay excluded the row. Measured on 100 recent production hooks.

Fix: broaden existing arm keyword lists + add a ``viral_moment`` arm
per non-ai_creators niche (which was the dominant unmatched pattern:
"hit Twitch top 3", "went viral", "everyone's playing X"). Post-fix
coverage on the same 100-row sample: ai_creators 33→42%, anime 18→55%,
gaming 15→78%, movies 12→31%.

These tests pin the class-of-bug: silent keyword-list drift causes
downstream ML infrastructure to see less data than expected, and the
degradation is invisible without an end-to-end measurement.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import (
    _ARM_KEYWORDS,
    _NICHE_ARM_DEFAULTS,
)


class TestArmSetCovers5Niches:
    """The arm registry must have entries for all 5 production niches."""

    def test_all_5_niches_have_arms(self):
        expected = {"ai_creators", "gaming", "sports", "movies", "anime"}
        assert set(_ARM_KEYWORDS.keys()) == expected

    def test_all_5_niches_have_defaults(self):
        expected = {"ai_creators", "gaming", "sports", "movies", "anime"}
        assert set(_NICHE_ARM_DEFAULTS.keys()) == expected


class TestViralMomentArmShipped:
    """viral_moment arm ships on all 4 non-ai_creators niches.

    ai_creators does NOT get viral_moment because AI content's viral
    pattern is well-covered by model_release + creative_showcase +
    comparison_test (no gap in the 2026-07-14 measurement).
    """

    def test_gaming_has_viral_moment(self):
        arm_ids = [arm_id for arm_id, _ in _ARM_KEYWORDS["gaming"]]
        assert "viral_moment" in arm_ids

    def test_sports_has_viral_moment(self):
        arm_ids = [arm_id for arm_id, _ in _ARM_KEYWORDS["sports"]]
        assert "viral_moment" in arm_ids

    def test_movies_has_viral_moment(self):
        arm_ids = [arm_id for arm_id, _ in _ARM_KEYWORDS["movies"]]
        assert "viral_moment" in arm_ids

    def test_anime_has_viral_moment(self):
        arm_ids = [arm_id for arm_id, _ in _ARM_KEYWORDS["anime"]]
        assert "viral_moment" in arm_ids

    def test_ai_creators_no_viral_moment_by_design(self):
        """AI creators V1 arms already cover their viral patterns.

        Kept as a pin: if a future PR adds viral_moment to ai_creators
        without validation that it lifts coverage on ai_creators data,
        that PR needs to update this test with the coverage evidence.
        """
        arm_ids = [arm_id for arm_id, _ in _ARM_KEYWORDS["ai_creators"]]
        assert "viral_moment" not in arm_ids


class TestKeywordCoverageOnProductionSamples:
    """Sample production hooks + assert they now match.

    These are real hooks from 2026-07-01 to 2026-07-14 that had zero
    V1 keyword match. If any of these regress to no-match in the future,
    a subsequent PR is likely removing keywords without measuring the
    coverage delta.
    """

    @staticmethod
    def _matches(text: str, niche_id: str) -> list[str]:
        text_lower = text.lower()
        matched = []
        for arm_id, keywords in _ARM_KEYWORDS.get(niche_id, []):
            if any(kw in text_lower for kw in keywords):
                matched.append(arm_id)
        return matched

    def test_gaming_twitch_trending_matches_viral_moment(self):
        # Real 2026-07-14 unmatched hook — Twitch trending pattern
        matched = self._matches("Granny Chapter Two hit Twitch's top 3", "gaming")
        assert "viral_moment" in matched, f"expected viral_moment in {matched}"

    def test_gaming_hit_number_1_matches(self):
        matched = self._matches("Cloudrooms hit Twitch #1 and nobody knows why", "gaming")
        assert len(matched) > 0, "should match at least one arm"

    def test_anime_edit_matches_viral_moment(self):
        matched = self._matches("The Sukuna edit that broke 800K views in an hour", "anime")
        assert "viral_moment" in matched

    def test_anime_character_backstory_matches(self):
        # This one requires "moment" or "scene" or "iconic" in viral_moment
        matched = self._matches(
            "Rengoku's mom is the most devastating character arc in anime",
            "anime",
        )
        assert len(matched) > 0, f"expected match, got {matched}"

    def test_movies_origin_matches_cast_reveal(self):
        matched = self._matches(
            "Pam Voorhees before Jason—the origin story nobody asked for",
            "movies",
        )
        assert "cast_reveal" in matched, f"expected cast_reveal in {matched}"

    def test_movies_returns_matches_cast_reveal(self):
        matched = self._matches("Pixar's bringing Woody back after 9 years", "movies")
        assert "cast_reveal" in matched, f"expected cast_reveal in {matched}"

    def test_ai_creators_openai_matches_model_release(self):
        matched = self._matches("Why OpenAI's first hardware is a $macro pad", "ai_creators")
        assert "model_release" in matched, f"expected model_release in {matched}"


class TestKeywordListIntegrity:
    """Structural invariants — catches accidental empties or type breaks."""

    def test_no_empty_keyword_lists(self):
        for niche_id, arms in _ARM_KEYWORDS.items():
            for arm_id, keywords in arms:
                assert keywords, f"{niche_id}/{arm_id} has empty keyword list"

    def test_no_arm_id_duplicated_within_niche(self):
        for niche_id, arms in _ARM_KEYWORDS.items():
            arm_ids = [arm_id for arm_id, _ in arms]
            assert len(arm_ids) == len(set(arm_ids)), (
                f"{niche_id} has duplicate arm_id: {arm_ids}"
            )

    def test_keywords_are_lowercase(self):
        """classify() uses .lower() on text, so keywords must be lowercase
        or they'll never match. This catches accidental capitalization."""
        for niche_id, arms in _ARM_KEYWORDS.items():
            for arm_id, keywords in arms:
                for kw in keywords:
                    assert kw == kw.lower(), (
                        f"{niche_id}/{arm_id} has non-lowercase keyword: {kw!r}"
                    )
