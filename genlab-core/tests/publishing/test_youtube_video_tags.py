"""Pin youtube_video_tags augment (2026-08-15 YT bootstrap fix).

  * augment adds niche anchors + discovery pool
  * dedup is case-insensitive
  * cap at _MAX_TAGS=15
  * unknown niche still adds discovery pool
  * empty base tags still gets augment
  * idempotent (rerun == same output)
"""
from __future__ import annotations

from genlab_core.publishing.youtube_video_tags import (
    _DISCOVERY_POOL,
    _MAX_TAGS,
    _NICHE_ANCHORS,
    augment_youtube_snippet_tags,
)


class TestAugment:
    def test_adds_niche_anchors_for_gaming(self):
        out = augment_youtube_snippet_tags(["Shorts"], "gaming")
        assert "gaming" in out
        assert "esports" in out
        # Original preserved
        assert "Shorts" in out

    def test_adds_niche_anchors_for_anime(self):
        out = augment_youtube_snippet_tags([], "anime")
        assert "anime" in out
        assert "manga" in out

    def test_adds_discovery_pool_tags(self):
        out = augment_youtube_snippet_tags([], "gaming")
        assert "trending" in out
        assert "viral" in out

    def test_dedup_case_insensitive(self):
        """Shorts + shorts should collapse to one."""
        out = augment_youtube_snippet_tags(
            ["Shorts", "gaming", "SHORTS"], "gaming",
        )
        lower = [t.lower() for t in out]
        assert lower.count("shorts") == 1

    def test_cap_at_max_tags(self):
        out = augment_youtube_snippet_tags(
            [f"tag{i}" for i in range(50)], "gaming",
        )
        assert len(out) <= _MAX_TAGS

    def test_unknown_niche_still_adds_discovery(self):
        """Missing niche in _NICHE_ANCHORS still gets discovery pool."""
        out = augment_youtube_snippet_tags(["custom"], "nonexistent_niche")
        assert "custom" in out
        assert "trending" in out

    def test_empty_base_tags_still_augments(self):
        out = augment_youtube_snippet_tags([], "gaming")
        assert len(out) > 0
        assert "gaming" in out

    def test_idempotent(self):
        out1 = augment_youtube_snippet_tags(["Shorts"], "gaming")
        out2 = augment_youtube_snippet_tags(out1, "gaming")
        assert out1 == out2, "double-augment should be no-op"


class TestPools:
    def test_every_niche_has_at_least_3_anchors(self):
        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in _NICHE_ANCHORS
            assert len(_NICHE_ANCHORS[niche]) >= 3, (
                f"{niche} anchor pool too small — need >=3 for meaningful"
                " tag variety"
            )

    def test_discovery_pool_size(self):
        """Discovery pool must be big enough that cap doesn't just show
        the same 2-3 tags every time."""
        assert len(_DISCOVERY_POOL) >= 5

    def test_no_hash_prefix_in_pools(self):
        """YT snippet.tags convention is bare strings, no # prefix.
        Prefix bleeds happen if we copy-paste from IG hashtag pool."""
        for niche, anchors in _NICHE_ANCHORS.items():
            for a in anchors:
                assert not a.startswith("#"), (
                    f"{niche} anchor {a!r} has # prefix — YT snippet.tags "
                    "expects bare strings"
                )
        for tag in _DISCOVERY_POOL:
            assert not tag.startswith("#"), (
                f"discovery tag {tag!r} has # prefix"
            )
