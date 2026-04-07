"""Tests for Sprint 64 alternative video source stages."""

from unittest.mock import MagicMock, patch


class TestFetchAnimePromos:
    """Tests for Jikan + AniList anime promo fetcher."""

    def test_skips_non_anime_niche(self):
        from genlab_core.pipeline.stages.fetch_anime_promos import FetchAnimePromos
        stage = FetchAnimePromos()
        ctx = {"niche_id": "sports", "stories": []}
        result = stage.execute(ctx)
        assert "anime_promos_found" not in result.get("run_stats", {})

    def test_jikan_returns_youtube_ids(self):
        from genlab_core.pipeline.stages.fetch_anime_promos import _fetch_jikan_promos
        mock_data = {
            "data": [
                {
                    "entry": {"title": "One Piece", "mal_id": 21},
                    "trailer": {"youtube_id": "abc123"},
                },
                {
                    "entry": {"title": "No Trailer", "mal_id": 22},
                    "trailer": {},
                },
            ]
        }
        with patch("genlab_core.pipeline.stages.fetch_anime_promos.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_get.return_value = mock_resp

            result = _fetch_jikan_promos()

        assert len(result) == 1
        assert result[0]["video_id"] == "abc123"
        assert result[0]["source"] == "jikan_promos"

    def test_jikan_handles_rate_limit(self):
        from genlab_core.pipeline.stages.fetch_anime_promos import _fetch_jikan_promos
        with patch("genlab_core.pipeline.stages.fetch_anime_promos.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_get.return_value = mock_resp
            result = _fetch_jikan_promos()
        assert result == []

    def test_anilist_returns_youtube_trailers(self):
        from genlab_core.pipeline.stages.fetch_anime_promos import _fetch_anilist_trailers
        mock_data = {
            "data": {
                "Page": {
                    "media": [
                        {
                            "title": {"english": "Demon Slayer", "romaji": "Kimetsu no Yaiba"},
                            "trailer": {"id": "xyz789", "site": "youtube"},
                            "popularity": 5000,
                            "trending": 100,
                        },
                        {
                            "title": {"english": None, "romaji": "No Trailer Show"},
                            "trailer": {"id": "abc", "site": "dailymotion"},
                            "popularity": 100,
                            "trending": 5,
                        },
                    ]
                }
            }
        }
        with patch("genlab_core.pipeline.stages.fetch_anime_promos.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_post.return_value = mock_resp
            result = _fetch_anilist_trailers()

        assert len(result) == 1
        assert result[0]["video_id"] == "xyz789"
        assert result[0]["title"] == "Demon Slayer"

    def test_execute_adds_stories_to_context(self):
        from genlab_core.pipeline.stages.fetch_anime_promos import FetchAnimePromos
        stage = FetchAnimePromos()
        ctx = {"niche_id": "anime", "stories": [], "sources_config": {}}

        with patch("genlab_core.pipeline.stages.fetch_anime_promos._fetch_jikan_promos") as mock_jikan, \
             patch("genlab_core.pipeline.stages.fetch_anime_promos._fetch_anilist_trailers") as mock_anilist:
            mock_jikan.return_value = [
                {"title": "Test Anime", "video_id": "yt1", "url": "https://youtube.com/watch?v=yt1", "source": "jikan_promos", "_trending_video": True}
            ]
            mock_anilist.return_value = []
            result = stage.execute(ctx)

        assert result["run_stats"]["anime_promos_found"] == 1
        assert len(result["stories"]) == 1
        assert result["stories"][0]["video_id"] == "yt1"


class TestFetchTMDBTrailers:
    """Tests for TMDB trailer ID fetcher."""

    def test_skips_non_movies_niche(self):
        from genlab_core.pipeline.stages.fetch_tmdb_trailers import FetchTMDBTrailers
        stage = FetchTMDBTrailers()
        ctx = {"niche_id": "gaming"}
        result = stage.execute(ctx)
        assert "tmdb_trailers_found" not in result.get("run_stats", {})

    def test_skips_when_no_api_key(self):
        from genlab_core.pipeline.stages.fetch_tmdb_trailers import FetchTMDBTrailers
        stage = FetchTMDBTrailers()
        with patch.dict("os.environ", {}, clear=True):
            ctx = {"niche_id": "movies", "sources_config": {}}
            result = stage.execute(ctx)
        assert "tmdb_trailers_found" not in result.get("run_stats", {})

    def test_fetch_trailer_ids_filters_official_youtube(self):
        import genlab_core.pipeline.stages.fetch_tmdb_trailers as _mod
        from genlab_core.pipeline.stages.fetch_tmdb_trailers import _fetch_movie_trailer_ids
        mock_videos = {
            "results": [
                {"key": "yt_official", "site": "YouTube", "type": "Trailer", "official": True},
                {"key": "yt_unofficial", "site": "YouTube", "type": "Trailer", "official": False},
                {"key": "vm_trailer", "site": "Vimeo", "type": "Trailer", "official": True},
            ]
        }
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_videos
        mock_session.get.return_value = mock_resp

        # Reset module-level session singleton and inject mock
        old_session = getattr(_mod, "_tmdb_session", None)
        _mod._tmdb_session = mock_session
        try:
            result = _fetch_movie_trailer_ids("fake_key", [{"id": 1, "title": "Test Movie"}])
        finally:
            _mod._tmdb_session = old_session

        assert len(result) == 1
        assert result[0]["video_id"] == "yt_official"


class TestFetchTwitchClips:
    """Tests for Twitch clips fetcher."""

    def test_skips_non_gaming_niche(self):
        from genlab_core.pipeline.stages.fetch_twitch_clips import FetchTwitchClips
        stage = FetchTwitchClips()
        ctx = {"niche_id": "movies"}
        result = stage.execute(ctx)
        assert "twitch_clips_found" not in result.get("run_stats", {})

    def test_skips_when_no_credentials(self):
        from genlab_core.pipeline.stages.fetch_twitch_clips import FetchTwitchClips
        stage = FetchTwitchClips()
        with patch.dict("os.environ", {}, clear=True):
            ctx = {"niche_id": "gaming", "sources_config": {}}
            result = stage.execute(ctx)
        assert "twitch_clips_found" not in result.get("run_stats", {})

    def test_clip_url_uses_page_url(self):
        """Clip URL should be the Twitch clip page URL (yt-dlp downloads natively)."""
        from genlab_core.pipeline.stages.fetch_twitch_clips import _fetch_clips_for_game
        mock_data = {
            "data": [
                {
                    "title": "Amazing play",
                    "thumbnail_url": "https://static-cdn.jtvnw.net/twitch-video-assets/xxx",
                    "view_count": 5000,
                    "url": "https://www.twitch.tv/streamer1/clip/AmazingPlay-abc123",
                    "broadcaster_name": "streamer1",
                    "duration": 30,
                    "game_id": "12345",
                    "created_at": "2026-03-15T10:00:00Z",
                },
            ]
        }
        with patch("genlab_core.pipeline.stages.fetch_twitch_clips.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_data
            mock_get.return_value = mock_resp

            result = _fetch_clips_for_game("12345", {"Authorization": "Bearer x", "Client-Id": "y"})

        assert len(result) == 1
        assert result[0]["clip_url"] == "https://www.twitch.tv/streamer1/clip/AmazingPlay-abc123"
        assert result[0]["source"] == "twitch_clips"


class TestFetchSteamTrailers:
    """Tests for Steam trailer fetcher."""

    def test_skips_non_gaming_niche(self):
        from genlab_core.pipeline.stages.fetch_steam_trailers import FetchSteamTrailers
        stage = FetchSteamTrailers()
        ctx = {"niche_id": "anime"}
        result = stage.execute(ctx)
        assert "steam_trailers_found" not in result.get("run_stats", {})

    def test_prefers_480p_quality(self):
        from genlab_core.pipeline.stages.fetch_steam_trailers import _fetch_trailers_for_app
        mock_data = {
            "730": {
                "success": True,
                "data": {
                    "name": "Counter-Strike 2",
                    "movies": [
                        {
                            "name": "Launch Trailer",
                            "mp4": {
                                "480": "https://cdn.steam.com/480.mp4",
                                "max": "https://cdn.steam.com/max.mp4",
                            },
                        }
                    ],
                },
            }
        }
        with patch("genlab_core.pipeline.stages.fetch_steam_trailers.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_get.return_value = mock_resp

            result = _fetch_trailers_for_app(730)

        assert len(result) == 1
        assert result[0]["clip_url"] == "https://cdn.steam.com/480.mp4"


class TestFetchScoreBat:
    """Tests for ScoreBat football highlights."""

    def test_skips_non_sports_niche(self):
        from genlab_core.pipeline.stages.fetch_scorebat import FetchScoreBatHighlights
        stage = FetchScoreBatHighlights()
        ctx = {"niche_id": "gaming"}
        result = stage.execute(ctx)
        assert "scorebat_highlights_found" not in result.get("run_stats", {})

    def test_filters_by_competition(self):
        from genlab_core.pipeline.stages.fetch_scorebat import _fetch_highlights
        mock_data = [
            {"title": "Arsenal vs Chelsea", "competition": {"name": "Premier League"}, "url": "https://sb.com/1", "embed": "<embed>"},
            {"title": "Random Cup Match", "competition": {"name": "Random Cup"}, "url": "https://sb.com/2", "embed": "<embed>"},
        ]
        with patch("genlab_core.pipeline.stages.fetch_scorebat.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_get.return_value = mock_resp

            result = _fetch_highlights(max_items=10, competitions=["Premier League"])

        assert len(result) == 1
        assert result[0]["title"] == "Arsenal vs Chelsea"
        assert result[0]["clip_url"] is None  # ScoreBat = embed only
