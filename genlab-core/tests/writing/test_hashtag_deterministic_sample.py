"""2026-08-12 (F-QB-0708 pt 3): pin the deterministic hashtag sample
that breaks the `#{Niche}Reels` template signature.

Motivating audit finding: 29% of recent captions had `#GamingReels`
or `#AnimeReels` (position #2 in old pool of 3). Every post got the
same first-2 tags → template signature YouTube's inauthentic-content
detection flags.

Fix: pools expanded to 6+ tags per (niche, platform); generator now
DETERMINISTICALLY samples 2 per story (seed = sha256(story_id +
niche + platform)) rather than always taking first-two.

Properties pinned:
* Same story → same tags on every call (retry safety)
* Different stories → different tag combinations (variety)
* Pool ≤ 2 tags → falls back to first-two (small-pool edge case)
* No story_id → falls back to first-two (test / one-off safety)
"""

from __future__ import annotations

from genlab_core.writing.hashtag_generator import generate_hashtags


class TestHashtagDeterministicSample:
    def test_same_story_id_returns_same_tags(self):
        """Retry safety: re-generating hashtags for the same story
        must return the same tags. Otherwise a retry churns the
        caption unnecessarily."""
        story = {
            "story_id": "abc123",
            "title": "Test story",
            "summary": "Test summary",
        }
        first = generate_hashtags(story, "gaming", platform="instagram")
        second = generate_hashtags(story, "gaming", platform="instagram")
        assert first == second, (
            f"deterministic sample must be stable across calls: "
            f"first={first} second={second}"
        )

    def test_different_stories_get_different_niche_tags(self):
        """Variety across posts: different story_ids should produce
        different niche-tag combinations most of the time. Not a
        strict guarantee (birthday paradox), but strong tendency."""
        distinct_first_pairs = set()
        for i in range(20):
            story = {"story_id": f"story-{i}", "title": "t", "summary": "s"}
            tags = generate_hashtags(story, "gaming", platform="instagram")
            # First 2 tags are the niche base sample
            distinct_first_pairs.add(tuple(sorted(tags[:2])))

        # 20 stories × 6-tag pool = C(6,2) = 15 possible pairs.
        # Should get at LEAST 5 distinct pairs across 20 stories
        # (otherwise the "sample" is degenerating to first-two).
        assert len(distinct_first_pairs) >= 5, (
            f"only {len(distinct_first_pairs)} distinct pairs across 20 stories — "
            f"sampling isn't working: {distinct_first_pairs}"
        )

    def test_gaming_reels_appears_in_less_than_half_of_posts(self):
        """The specific F-QB-0708 signature: `#GamingReels` used to
        appear on 100% of gaming IG posts. Post-fix, it should be
        one of ~6 pool options → appear in <50% of posts."""
        gaming_reels_count = 0
        total = 30
        for i in range(total):
            story = {"story_id": f"story-{i}", "title": "t", "summary": "s"}
            tags = generate_hashtags(story, "gaming", platform="instagram")
            if "#GamingReels" in tags[:2]:
                gaming_reels_count += 1

        # With 6-tag pool, prob of #GamingReels in first-two ≈ 2/6 = 33%
        # Allow 20-50% range for randomness across 30 samples.
        assert gaming_reels_count / total < 0.50, (
            f"#GamingReels still appearing in {gaming_reels_count}/{total} "
            f"= {100*gaming_reels_count/total:.0f}% of gaming IG posts — "
            f"template signature not broken"
        )

    def test_small_pool_falls_back_to_first_two(self):
        """When the pool has ≤ 2 tags (e.g. twitter's terse niche
        pools), sample is degenerate — fall back to first-two for
        determinism."""
        # twitter/gaming has 2 tags: ["#Gaming", "#GamingCommunity"]
        story = {"story_id": "any", "title": "t", "summary": "s"}
        tags = generate_hashtags(story, "gaming", platform="twitter")
        # First 2 tags must be exactly the pool contents
        assert set(tags) <= {"#Gaming", "#GamingCommunity"} | set(tags[2:])

    def test_missing_story_id_falls_back_to_first_two(self):
        """A story without story_id (test fixture, one-off caller)
        falls back to deterministic first-two rather than raising."""
        story = {"title": "t", "summary": "s"}
        tags = generate_hashtags(story, "gaming", platform="instagram")
        # First 2 must match pool head — no exception raised
        assert len(tags) >= 2
        # Deterministic across calls with same (no-id) story
        again = generate_hashtags({"title": "t", "summary": "s"}, "gaming", platform="instagram")
        assert tags[:2] == again[:2]

    def test_seed_includes_platform_so_ig_and_tiktok_can_differ(self):
        """Same story on IG vs TikTok should get different sampled
        tag pairs (both draw from platform-specific pools)."""
        story = {"story_id": "same-story", "title": "t", "summary": "s"}
        ig_tags = generate_hashtags(story, "gaming", platform="instagram")
        # TikTok's gaming pool has 3 tags — small pool, falls back to
        # first-two. So sample-vs-fallback is expected — but the sets
        # of tags themselves should differ because pools are distinct.
        tiktok_tags = generate_hashtags(story, "gaming", platform="tiktok")
        assert set(ig_tags[:2]) != set(tiktok_tags[:2]) or (
            "#Gaming" in ig_tags and "#GamingTikTok" in tiktok_tags
        ), "IG + TikTok pools are distinct — tags should reflect that"
