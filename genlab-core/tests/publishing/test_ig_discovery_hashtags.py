"""Pin ig_discovery_hashtags (2026-08-15 IG-stagnation fix):

  * Flag semantics (off/canary/wildcard/comma-list)
  * augment returns input unchanged when disabled
  * augment appends N discovery tags when enabled
  * empty seed returns input unchanged (fail-open on non-idempotent)
  * idempotent: same seed → same picks
  * doesn't double-add tags already present
  * rotation covers multiple pool tags over aggregate
  * output tags start with #
"""
from __future__ import annotations

from collections import Counter

import pytest

from genlab_core.publishing.ig_discovery_hashtags import (
    _DISCOVERY_POOL,
    _TAGS_TO_APPEND,
    augment_ig_hashtags,
    is_enabled_for,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", val)
        assert is_enabled_for("ai_creators") is False

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", raising=False,
        )
        assert is_enabled_for("ai_creators") is False

    @pytest.mark.parametrize("val", ["all", "*"])
    def test_wildcard(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", val)
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert is_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "gaming,anime",
        )
        assert is_enabled_for("gaming") is True
        assert is_enabled_for("anime") is True
        assert is_enabled_for("movies") is False


class TestAugment:
    def test_disabled_returns_input(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", raising=False,
        )
        input_tags = ["#Anime", "#Manga"]
        out = augment_ig_hashtags(input_tags, "anime", "seed_x")
        assert out == input_tags

    def test_empty_seed_returns_input(self, monkeypatch):
        """No seed = deterministic same-picks-every-time which
        would create a templated signature. Fail-open to input."""
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        input_tags = ["#Anime"]
        out = augment_ig_hashtags(input_tags, "anime", "")
        assert out == input_tags

    def test_appends_N_tags_when_enabled(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        input_tags = ["#Anime", "#Manga"]
        out = augment_ig_hashtags(input_tags, "anime", "seed_x")
        # Input preserved
        for t in input_tags:
            assert t in out
        # Extra tags appended (up to _TAGS_TO_APPEND)
        assert len(out) == len(input_tags) + _TAGS_TO_APPEND

    def test_idempotent_same_seed_same_output(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        input_tags = ["#Anime"]
        a = augment_ig_hashtags(input_tags, "anime", "consistent_seed")
        b = augment_ig_hashtags(input_tags, "anime", "consistent_seed")
        assert a == b

    def test_does_not_double_add_existing_tag(self, monkeypatch):
        """If LLM already put #Reels, augment must not add it again."""
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        # Pre-seed with a tag from the discovery pool.
        input_tags = ["#Reels", "#Anime"]
        out = augment_ig_hashtags(input_tags, "anime", "seed_x")
        reels_count = sum(1 for t in out if t.lower() == "#reels")
        assert reels_count == 1, (
            "augment must not double-add #Reels — Meta will flag "
            f"duplicates: {out}"
        )

    def test_output_tags_start_with_hash(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        out = augment_ig_hashtags([], "gaming", "seed_x")
        for t in out:
            assert t.startswith("#"), f"invalid tag: {t}"

    def test_rotation_covers_multiple_pool_tags(self, monkeypatch):
        """Over 200 distinct seeds, at least 6 different discovery
        tags should appear. Guarantees rotation isn't stuck on a
        small subset (which would look templated to Meta)."""
        monkeypatch.setenv("GENLAB_IG_DISCOVERY_HASHTAGS_NICHES", "all")
        tag_counter: Counter[str] = Counter()
        for i in range(200):
            out = augment_ig_hashtags(
                [], "anime", f"seed_{i}",
            )
            for t in out:
                tag_counter[t] += 1
        distinct = len(tag_counter)
        assert distinct >= 6, (
            f"only {distinct} distinct discovery tags over 200 seeds — "
            f"rotation not spreading load. Pool size = {len(_DISCOVERY_POOL)}"
        )


class TestDiscoveryPool:
    def test_pool_size_healthy(self):
        """Pool should be >= 8 tags so rotation has meaningful variation."""
        assert len(_DISCOVERY_POOL) >= 8

    def test_every_pool_tag_starts_with_hash(self):
        for tag in _DISCOVERY_POOL:
            assert tag.startswith("#"), f"pool tag malformed: {tag}"

    def test_no_niche_specific_tags_in_pool(self):
        """Pool must be niche-agnostic — no #Anime, #Gaming, etc.
        Those come from LLM output + templates.yaml. Discovery pool
        is structural only."""
        forbidden = {"anime", "gaming", "movies", "sports", "ai", "tech"}
        for tag in _DISCOVERY_POOL:
            stripped = tag.lstrip("#").lower()
            assert stripped not in forbidden, (
                f"discovery pool must be niche-agnostic; found {tag}"
            )
