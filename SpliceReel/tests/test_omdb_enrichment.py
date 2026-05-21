"""Tests for SpliceReel OMDb enrichment."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from sr_strategies.omdb_client import OMDbClient


class TestOMDbClient:
    def test_parses_rt_score_from_ratings_array(self):
        data = {
            "Response": "True",
            "imdbID": "tt1234567",
            "imdbRating": "8.1",
            "Metascore": "72",
            "BoxOffice": "$150,000,000",
            "Awards": "Won 3 Oscars",
            "Ratings": [
                {"Source": "Internet Movie Database", "Value": "8.1/10"},
                {"Source": "Rotten Tomatoes", "Value": "92%"},
                {"Source": "Metacritic", "Value": "72/100"},
            ],
        }
        result = OMDbClient._parse_response(data)
        assert result.rt_score == 92
        assert result.imdb_rating == 8.1
        assert result.metacritic_score == 72
        assert result.imdb_id == "tt1234567"
        assert result.box_office == "$150,000,000"

    def test_returns_none_on_api_key_not_set(self):
        client = OMDbClient(api_key="")
        result = client.enrich_by_title("Oppenheimer")
        assert result is None
        client.close()

    def test_returns_none_on_film_not_found(self):
        with patch("sr_strategies.omdb_client.httpx.Client") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"Response": "False", "Error": "Movie not found!"}
            mock_resp.raise_for_status = MagicMock()
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_cls.return_value = mock_client

            client = OMDbClient(api_key="test_key")
            client._client = mock_client
            result = client.enrich_by_title("Nonexistent Film 9999")
            assert result is None

    def test_returns_none_on_network_error(self):
        with patch("sr_strategies.omdb_client.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("Connection timeout")
            mock_cls.return_value = mock_client

            client = OMDbClient(api_key="test_key")
            client._client = mock_client
            result = client.enrich_by_title("Test Film")
            assert result is None


class TestScoringOMDbIntegration:
    def test_scoring_calls_omdb_only_above_threshold(self, tmp_path):
        """Items below pre_enrich_threshold should NOT trigger OMDb calls."""
        import yaml
        from sr_strategies.scoring import MovieScoringStrategy

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "scoring_weights.yaml").write_text(
            yaml.dump(
                {
                    "scoring_dimensions": {
                        "weights": {
                            "timeliness": 0.25,
                            "magnitude": 0.35,
                            "engagement_potential": 0.25,
                            "novelty": 0.15,
                        },
                        "timeliness": {"decay_half_life_hours": 48.0},
                        "magnitude": {"rt_score_excellent": 0.90, "rt_score_good": 0.75},
                    },
                    "film_lifecycle_multipliers": {
                        "opening_weekend": 1.6,
                        "unknown": 0.3,
                        "pre_release": 1.0,
                        "long_tail": 0.7,
                    },
                    "franchise_multipliers": {"default": 1.0},
                    "thresholds": {
                        "min_score": 0.40,
                        "top_items_per_run": 5,
                        "pre_enrich_threshold": 0.45,
                    },
                }
            )
        )

        with (
            patch("sr_strategies.scoring.NICHE_ROOT", tmp_path),
            patch.dict("os.environ", {"OMDB_API_KEY": "test_key"}),
            patch("sr_strategies.scoring.OMDbClient") as mock_omdb_cls,
        ):
            mock_omdb = MagicMock()
            mock_omdb.__enter__ = MagicMock(return_value=mock_omdb)
            mock_omdb.__exit__ = MagicMock(return_value=False)
            mock_omdb.enrich_by_title.return_value = None
            mock_omdb_cls.return_value = mock_omdb

            s = MovieScoringStrategy()
            s._ensure_config()

            # Low-scoring story (unknown lifecycle 0.3x, low popularity)
            context = {
                "stories": [
                    {
                        "title": "Unknown Film",
                        "film_title": "Unknown Film",
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "lifecycle_stage": "unknown",
                        "franchise": "",
                        "tmdb_popularity": 0,
                        "rt_score": None,
                        "is_trailer_drop": False,
                        "is_box_office_news": False,
                    },
                ]
            }
            result = s.execute(context)
            # Should not call OMDb for items below threshold
            assert result["run_stats"]["scoring"]["omdb_enriched"] == 0

    def test_scoring_handles_omdb_returning_none(self, tmp_path):
        """Strategy should not crash when OMDb returns None."""
        import yaml
        from sr_strategies.scoring import MovieScoringStrategy

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "scoring_weights.yaml").write_text(
            yaml.dump(
                {
                    "scoring_dimensions": {
                        "weights": {
                            "timeliness": 0.25,
                            "magnitude": 0.35,
                            "engagement_potential": 0.25,
                            "novelty": 0.15,
                        },
                        "timeliness": {"decay_half_life_hours": 48.0},
                        "magnitude": {"rt_score_excellent": 0.90, "rt_score_good": 0.75},
                    },
                    "film_lifecycle_multipliers": {
                        "opening_weekend": 1.6,
                        "unknown": 0.3,
                        "pre_release": 1.0,
                        "long_tail": 0.7,
                    },
                    "franchise_multipliers": {"MCU": 1.5, "default": 1.0},
                    "thresholds": {
                        "min_score": 0.10,
                        "top_items_per_run": 5,
                        "pre_enrich_threshold": 0.20,
                    },
                }
            )
        )

        with (
            patch("sr_strategies.scoring.NICHE_ROOT", tmp_path),
            patch.dict("os.environ", {"OMDB_API_KEY": "test_key"}),
            patch("sr_strategies.scoring.OMDbClient") as mock_omdb_cls,
        ):
            mock_omdb = MagicMock()
            mock_omdb.__enter__ = MagicMock(return_value=mock_omdb)
            mock_omdb.__exit__ = MagicMock(return_value=False)
            mock_omdb.enrich_by_title.return_value = None
            mock_omdb_cls.return_value = mock_omdb

            s = MovieScoringStrategy()
            s._ensure_config()

            context = {
                "stories": [
                    {
                        "title": "Avengers 6",
                        "film_title": "Avengers 6",
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "lifecycle_stage": "opening_weekend",
                        "franchise": "MCU",
                        "tmdb_popularity": 100,
                        "rt_score": None,
                        "is_trailer_drop": False,
                        "is_box_office_news": False,
                    },
                ]
            }
            # Should not crash — gracefully handles None from OMDb
            result = s.execute(context)
            assert len(result["stories"]) >= 1
            assert "omdb_enriched" in result["run_stats"]["scoring"]
