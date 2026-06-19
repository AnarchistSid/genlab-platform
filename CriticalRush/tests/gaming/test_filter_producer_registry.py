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

    def test_legacy_hardcoded_sources_still_present_during_phase_1(self):
        """During phase-1 migration, fetchers not yet migrated still need
        their sources in the trust list. Pins the set explicitly so we can
        easily see what's left to migrate."""
        # Local fetchers (FetchGamingStories — to be migrated in phase 2)
        assert "steam_spike" in _TRUSTED_GAMING_SOURCES
        assert "twitch_trending" in _TRUSTED_GAMING_SOURCES
        # FetchTrendingVideos sources (to be migrated in phase 2)
        assert "youtube_trending" in _TRUSTED_GAMING_SOURCES
        assert "shared_pool" in _TRUSTED_GAMING_SOURCES

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
