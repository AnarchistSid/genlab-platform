"""Contract test: FilterGamingStories trust list matches actual producers.

The 2026-06-19 silent-drop bug class (PR #360) was caused by a hand-
maintained 2-entry frozenset diverging from the actual 4+ upstream fetchers.
After the P1 refactor, the trust list is aggregated from
``FetcherStage.EMITTED_SOURCES`` declarations on the migrated fetchers.

This test pins the relationship: if a migrated fetcher's EMITTED_SOURCES
ever drops a value the filter previously trusted, the test fails. If a new
fetcher declares EMITTED_SOURCES but the filter doesn't pick them up (e.g.
import missing), the test fails.

Future fetchers added to the producer registry call list are picked up
automatically — no change here required.
"""

from __future__ import annotations

from niches.gaming.stages.filter_gaming_stories import (
    _REGISTRY_TRUSTED_SOURCES,
    _TRUSTED_GAMING_SOURCES,
)


class TestProducerRegistryContract:
    def test_registry_includes_twitch_clips_from_FetchTwitchClips(self):
        """If FetchTwitchClips.EMITTED_SOURCES drops 'twitch_clips', the
        filter must pick that up (i.e. the registry must continue to know
        about it through the import)."""
        from genlab_core.pipeline.stages.fetch_twitch_clips import FetchTwitchClips

        assert "twitch_clips" in FetchTwitchClips.EMITTED_SOURCES
        assert "twitch_clips" in _REGISTRY_TRUSTED_SOURCES
        assert "twitch_clips" in _TRUSTED_GAMING_SOURCES

    def test_registry_includes_steam_trailer_from_FetchSteamTrailers(self):
        from genlab_core.pipeline.stages.fetch_steam_trailers import FetchSteamTrailers

        assert "steam_trailer" in FetchSteamTrailers.EMITTED_SOURCES
        assert "steam_trailer" in _REGISTRY_TRUSTED_SOURCES
        assert "steam_trailer" in _TRUSTED_GAMING_SOURCES

    def test_phase_3_migrated_FetchGamingStories_sources_in_registry(self):
        """P1 phase 3: FetchGamingStories migrated. Its 2 trustable source
        values (steam_spike, twitch_trending) now come from the producer
        registry, NOT from the legacy hardcoded set. ``rss`` is INTENTIONALLY
        excluded from EMITTED_SOURCES so it continues to go through the
        keyword filter (RSS feeds can carry off-topic noise)."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories
        from niches.gaming.stages.filter_gaming_stories import (
            _LEGACY_HARDCODED_SOURCES,
            _REGISTRY_TRUSTED_SOURCES,
        )

        # FetchGamingStories declares its 2 gaming-by-construction sources
        assert FetchGamingStories.EMITTED_SOURCES == frozenset({"steam_spike", "twitch_trending"})
        # Both are in the registry-derived trust set
        assert "steam_spike" in _REGISTRY_TRUSTED_SOURCES
        assert "twitch_trending" in _REGISTRY_TRUSTED_SOURCES
        # And NEITHER is in the legacy set (clean migration)
        assert "steam_spike" not in _LEGACY_HARDCODED_SOURCES
        assert "twitch_trending" not in _LEGACY_HARDCODED_SOURCES

    def test_rss_NOT_in_trust_set_so_keyword_filter_still_applies(self):
        """Pin the design: ``rss`` is intentionally excluded from
        EMITTED_SOURCES so RSS noise (phone deals, movie news, etc.) still
        gets keyword-filtered. If a future fetcher accidentally adds ``rss``
        to its EMITTED_SOURCES, this test fails."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories
        from niches.gaming.stages.filter_gaming_stories import (
            _LEGACY_HARDCODED_SOURCES,
            _REGISTRY_TRUSTED_SOURCES,
            _TRUSTED_GAMING_SOURCES,
        )

        assert "rss" not in FetchGamingStories.EMITTED_SOURCES
        assert "rss" not in _REGISTRY_TRUSTED_SOURCES
        assert "rss" not in _LEGACY_HARDCODED_SOURCES
        assert "rss" not in _TRUSTED_GAMING_SOURCES

    def test_phase_4_FetchTrendingVideos_sources_in_registry(self):
        """P1 phase 4 COMPLETE: FetchTrendingVideos migrated. Its 4 source
        values now come from the producer registry. ``channel_subscription``
        was in the v2-review-era hardcoded set but is NOT actually emitted
        as a story.source value (only as TrendingVideo.search_query metadata
        fallback) — correctly dropped during migration."""
        from genlab_core.media.trending_video_fetcher import FetchTrendingVideos
        from niches.gaming.stages.filter_gaming_stories import (
            _REGISTRY_TRUSTED_SOURCES,
        )

        # FetchTrendingVideos declares its 4 actually-emitted source values
        assert FetchTrendingVideos.EMITTED_SOURCES == frozenset(
            {"youtube_trending", "youtube_rss", "youtube_playlist", "shared_pool"}
        )
        # All 4 are in the registry-derived trust set
        for src in ("youtube_trending", "youtube_rss", "youtube_playlist", "shared_pool"):
            assert src in _REGISTRY_TRUSTED_SOURCES, f"{src!r} missing from registry"
        # channel_subscription was a phantom — never actually emitted, now dropped
        assert "channel_subscription" not in FetchTrendingVideos.EMITTED_SOURCES
        assert "channel_subscription" not in _REGISTRY_TRUSTED_SOURCES

    def test_legacy_hardcoded_set_is_EMPTY_after_phase_4(self):
        """P1 phase 4 COMPLETE: every gaming-pipeline fetcher now declares
        its source values via EMITTED_SOURCES. The legacy fallback set goes
        EMPTY — the producer registry is the SOLE source of truth.

        If a future PR re-adds entries here without a TODO to migrate them
        into a FetcherStage subclass, that's the smell that bit us in PR
        #360 (allowlist drift). This test pins the empty state as the
        target architectural shape."""
        from niches.gaming.stages.filter_gaming_stories import _LEGACY_HARDCODED_SOURCES

        assert _LEGACY_HARDCODED_SOURCES == frozenset(), (
            f"_LEGACY_HARDCODED_SOURCES is no longer empty: {_LEGACY_HARDCODED_SOURCES}. "
            "P1 phase 4 closed the migration — new sources MUST come from a "
            "FetcherStage's EMITTED_SOURCES, not be added back here."
        )

    def test_registry_and_legacy_sets_are_disjoint(self):
        """Migration health check: a source value should be in EITHER the
        registry OR the legacy hardcoded set, not both. Overlap means a
        partial migration we forgot to clean up."""
        from niches.gaming.stages.filter_gaming_stories import (
            _LEGACY_HARDCODED_SOURCES,
            _REGISTRY_TRUSTED_SOURCES,
        )

        overlap = _REGISTRY_TRUSTED_SOURCES & _LEGACY_HARDCODED_SOURCES
        assert overlap == set(), (
            f"Source(s) {overlap} are in BOTH the producer registry AND the "
            "legacy hardcoded set. Remove from _LEGACY_HARDCODED_SOURCES — "
            "the migrated fetcher's EMITTED_SOURCES is now the source of truth."
        )

    def test_trust_set_is_immutable(self):
        """Defensive: a downstream caller shouldn't be able to mutate the
        shared trust set."""
        import pytest

        with pytest.raises(AttributeError):
            _TRUSTED_GAMING_SOURCES.add("hostile_source")  # type: ignore[attr-defined]
