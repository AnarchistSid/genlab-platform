"""Pin tests for `_PLATFORM_LIMITS` in `hashtag_generator.py`.

The Instagram hashtag cap of 3 (task #582, 2026-07-08) is a business
decision, not a technical constraint. Meta's algorithm updates through
2024 progressively down-weighted posts using >3 hashtags. Any future
contributor who bumps IG back to 5 needs to have a REASON — a memo, an
A/B test, an updated ranking article — because reversing this will
cost reach.

These tests fail loudly if someone changes the limit without updating
the test, which is exactly the friction we want: the wrong direction
of change should be inconvenient.
"""

from __future__ import annotations

from genlab_core.writing.hashtag_generator import _PLATFORM_LIMITS, generate_hashtags


class TestPlatformLimits:
    """Lock in the 2026-07-08 platform-limit values. Each is a business
    decision with a source; changing them requires updating this test
    file with a new reason (which surfaces the change in code review)."""

    def test_instagram_capped_at_three(self):
        """Post-2024 Meta ranking penalizes >3 hashtags. Sweet spot is 3."""
        assert _PLATFORM_LIMITS["instagram"] == 3, (
            "Instagram hashtag cap must be 3 (post-2024 Meta algo). "
            "If you're increasing this, cite the source in the code "
            "comment on _PLATFORM_LIMITS AND update this test to match."
        )

    def test_youtube_uses_description_tags_not_hashtags(self):
        """YouTube's hashtag surface is metadata in the description, not
        the caption. Setting 0 here prevents the writer from stuffing tags
        into the description body."""
        assert _PLATFORM_LIMITS["youtube"] == 0

    def test_twitter_conservative_at_two(self):
        """X/Twitter engagement peaks at 2-3 hashtags; going higher reads
        as spam and hurts retweet velocity."""
        assert _PLATFORM_LIMITS["twitter"] == 2

    def test_facebook_moderate_at_three(self):
        assert _PLATFORM_LIMITS["facebook"] == 3

    def test_threads_matches_facebook(self):
        assert _PLATFORM_LIMITS["threads"] == 3

    def test_tiktok_still_at_five(self):
        """TikTok's algorithm still rewards 3-5 tags per 2026 tests —
        different than IG's post-2024 change. Keep separate limits."""
        assert _PLATFORM_LIMITS["tiktok"] == 5


class TestInstagramCapEnforcement:
    """The limit CONSTANT alone isn't enough — the generator has to
    actually apply it. These tests ensure the plumbing works end-to-end."""

    def test_instagram_output_never_exceeds_limit(self):
        """Generator must clamp to `_PLATFORM_LIMITS["instagram"]` even
        when candidate tags (base + topic + trending) exceed it."""
        # Craft a story with rich keyword coverage so many topic tags
        # match. Base = 2 gaming tags, topic tags extracted from title,
        # trending optional. Pre-cap total could be 5+ — we assert ≤3.
        story = {
            "title": "Fortnite Minecraft Valorant Roblox — trending games",
            "summary": "Chapter 5 season 3 dropped, Minecraft cave update, Valorant patch.",
            "hook": "Games this week are goated",
        }
        tags = generate_hashtags(
            story,
            niche_id="gaming",
            platform="instagram",
            trending_keywords=["fortnite", "valorant"],
        )
        assert len(tags) <= 3, (
            f"IG output must be ≤3 hashtags per _PLATFORM_LIMITS. "
            f"Got {len(tags)}: {tags}. Regression: the generate function "
            f"is likely ignoring _PLATFORM_LIMITS['instagram']."
        )

    def test_instagram_output_includes_base_tags_first(self):
        """When capped at 3, the 2 niche base tags should survive.
        Topic tags fill the remaining slot(s)."""
        story = {
            "title": "Something about gaming",
            "summary": "",
            "hook": "",
        }
        tags = generate_hashtags(story, niche_id="gaming", platform="instagram")
        assert "#Gaming" in tags, f"Base tag #Gaming missing from IG output: {tags}"
        assert "#GamingClips" in tags, f"Base tag #GamingClips missing from IG output: {tags}"

    def test_twitter_output_never_exceeds_limit(self):
        """Same cap-enforcement check for Twitter (limit=2)."""
        story = {
            "title": "NBA Finals MVP LeBron highlights",
            "summary": "Kevin Durant, Steph Curry, all-star reactions",
            "hook": "",
        }
        tags = generate_hashtags(story, niche_id="sports", platform="twitter")
        assert len(tags) <= 2, f"Twitter output must be ≤2 hashtags. Got {tags}"


class TestGamingPersonaFreeOfBannedPhrases:
    """Task #591 (2026-07-08): the 2026-05-21 audit flagged the gaming
    engagement persona for still leaning on banned LLM-tell phrases
    ("insane", "hit different"). The fix landed partially — 2 of 4
    style_examples still contained those phrases through 2026-07-08.
    This test locks in the second-pass fix and prevents future drift.

    The banned phrases here are the SAME list `llm_hook_generator._BANNED_PHRASES`
    enforces for hooks. Applying the same standard to the engagement
    persona keeps the channel's voice consistent across hook + reply.
    """

    def test_gaming_persona_examples_free_of_banned_phrases(self):
        """No style_example may contain a banned LLM-tell substring
        (case-insensitive). If this test fails, the persona has drifted
        back toward the hype-tell voice the 2026-05-21 audit rejected."""
        from pathlib import Path

        import yaml

        # Grab a small subset of the most-critical banned phrases — the
        # ones the 2026-05-21 audit specifically named for gaming. Full
        # list lives in `llm_hook_generator._BANNED_PHRASES`; this test
        # locks in just the gaming-flavored ones.
        gaming_banned_phrases = [
            "hit different",  # audit named this specifically
            "absolutely insane",
            "goated",  # gaming-specific slang the audit flagged
            "literally",
            "no cap",
        ]

        # Resolve persona path from this test file's location so the
        # test survives on any checkout root (CI, dev, worktrees). This
        # file lives at genlab-core/tests/writing/, and the persona
        # lives at CriticalRush/niches/gaming/config/persona.yaml — both
        # under the same repo root.
        repo_root = Path(__file__).resolve().parents[3]
        persona_path = repo_root / "CriticalRush" / "niches" / "gaming" / "config" / "persona.yaml"
        assert persona_path.exists(), (
            f"Persona file not found at {persona_path} — "
            f"the test's repo-root resolution may be wrong."
        )
        with persona_path.open() as f:
            persona = yaml.safe_load(f)

        examples = persona.get("style_examples", [])
        assert examples, f"Gaming persona has no style_examples: {persona_path}"

        for example in examples:
            lower = example.lower()
            for banned in gaming_banned_phrases:
                assert banned not in lower, (
                    f"Gaming persona style_example contains banned phrase "
                    f"{banned!r}: {example!r}. Task #591 (2026-07-08) locked "
                    f"in the sentence-case editorial-casual voice; changing "
                    f"back to banned-phrase examples is a regression. "
                    f"Update the example OR update this test with a "
                    f"documented reason."
                )
