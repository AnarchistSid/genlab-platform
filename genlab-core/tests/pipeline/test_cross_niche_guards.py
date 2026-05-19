"""Tests for cluster-A leak guards: stage-prefix at boot + source allowlist at push."""
from __future__ import annotations

import pytest

from genlab_core.exceptions import NicheConfigError
from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner
from genlab_core.pipeline.stages.push_to_backlog import _is_foreign_source


class TestStagePrefixGuard:
    """Guard #1: rejects foreign strategy modules at pipeline boot."""

    def test_ai_creators_rejects_sr_strategies(self) -> None:
        with pytest.raises(NicheConfigError, match="CROSS-NICHE LEAK BLOCKED"):
            GenericPipelineRunner._check_foreign_stage(
                "ai_creators", "sr_strategies.fetch_film_news",
            )

    def test_ai_creators_rejects_fetch_tmdb_trailers(self) -> None:
        # Exact stage from the 2026-05-18 leak.
        with pytest.raises(NicheConfigError):
            GenericPipelineRunner._check_foreign_stage(
                "ai_creators",
                "genlab_core.pipeline.stages.fetch_tmdb_trailers",
            )

    def test_ai_creators_allows_bb_strategies(self) -> None:
        GenericPipelineRunner._check_foreign_stage(
            "ai_creators", "bb_strategies.content_research",
        )

    def test_ai_creators_allows_shared_genlab_core(self) -> None:
        GenericPipelineRunner._check_foreign_stage(
            "ai_creators", "genlab_core.pipeline.stages.express_lane",
        )

    def test_movies_allows_tmdb_trailers(self) -> None:
        # TMDB is legitimate FOR movies — must NOT be blocked.
        GenericPipelineRunner._check_foreign_stage(
            "movies", "genlab_core.pipeline.stages.fetch_tmdb_trailers",
        )

    def test_gaming_rejects_anime_promos(self) -> None:
        with pytest.raises(NicheConfigError):
            GenericPipelineRunner._check_foreign_stage(
                "gaming", "genlab_core.pipeline.stages.fetch_anime_promos",
            )

    def test_sports_rejects_twitch_clips(self) -> None:
        with pytest.raises(NicheConfigError):
            GenericPipelineRunner._check_foreign_stage(
                "sports", "genlab_core.pipeline.stages.fetch_twitch_clips",
            )

    def test_unknown_niche_id_passes(self) -> None:
        # Unknown niche has no forbidden list — pipeline-level checks
        # handle this case separately.
        GenericPipelineRunner._check_foreign_stage(
            "future_niche", "anywhere.anything",
        )


class TestSourceAllowlist:
    """Guard #2: drops stories with foreign source values at PushToBacklog."""

    def test_exact_leak_pattern_flagged(self) -> None:
        # The 2026-05-18 leak: BB story with tmdb_upcoming source.
        assert _is_foreign_source("ai_creators", "tmdb_upcoming") == "tmdb_"

    def test_movies_keeps_tmdb(self) -> None:
        assert _is_foreign_source("movies", "tmdb_upcoming") is None
        assert _is_foreign_source("movies", "tmdb_trending") is None

    def test_legitimate_bb_source_passes(self) -> None:
        assert _is_foreign_source("ai_creators", "youtube_trending") is None
        assert _is_foreign_source("ai_creators", "rss") is None
        assert _is_foreign_source("ai_creators", "youtube_rss") is None

    def test_gaming_rejects_anilist(self) -> None:
        assert _is_foreign_source("gaming", "anilist") == "anilist"
        assert _is_foreign_source("gaming", "jikan_promos") == "jikan_"

    def test_sports_rejects_twitch(self) -> None:
        assert _is_foreign_source("sports", "twitch_clip") == "twitch_clip"
        assert _is_foreign_source("sports", "twitch_trending") == "twitch_trending"

    def test_empty_source_passes(self) -> None:
        # Legacy stories with empty source must not be falsely flagged.
        assert _is_foreign_source("ai_creators", "") is None
        assert _is_foreign_source("ai_creators", None) is None  # type: ignore[arg-type]

    def test_rss_shared_across_all_niches(self) -> None:
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert _is_foreign_source(niche, "rss") is None

    def test_anime_rejects_steam_and_espn(self) -> None:
        assert _is_foreign_source("anime", "steam_spike") == "steam_"
        assert _is_foreign_source("anime", "espn") == "espn"
