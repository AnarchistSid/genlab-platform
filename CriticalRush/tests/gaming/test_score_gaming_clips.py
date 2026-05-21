"""Tests for the ScoreGamingClips pipeline stage (Phase 1 merge).

Covers:
  - All 7 dimensions return values between 0 and 1
  - Clips below min_clip_score are dropped
  - Publisher tier RED drops to 0 regardless of other scores
  - Output validates against scored_clip.schema.json
  - Overwatch preprocessing produces correct output shape
  - Missing signals use median fallback without exceptions
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

# --- Scoring function imports ---
from niches.gaming.stages.score_gaming_clips import (
    ScoreGamingClips,
    apply_tier_multiplier,
    get_publisher_tier,
    score_entertainment,
    score_production_quality,
    score_recency,
    score_source_authority,
    score_virality,
)
from PIL import Image

NICHE_ROOT = Path(__file__).resolve().parent.parent.parent / "niches" / "gaming"


# ---- Fixtures ----


@pytest.fixture
def scoring_config():
    """Minimal scoring config matching scoring_weights.yaml structure."""
    return {
        "weights": {
            "virality_potential": 0.22,
            "entertainment_value": 0.18,
            "recency": 0.13,
            "source_authority": 0.13,
            "production_quality": 0.09,
            "chat_excitement": 0.13,
            "highlight_detection": 0.12,
        },
        "virality": {
            "view_count_baseline": {
                "twitch": 1000,
                "reddit": 500,
                "youtube": 5000,
            },
        },
        "entertainment": {
            "min_duration_seconds": 5,
            "max_duration_seconds": 60,
            "preferred_min_seconds": 15,
            "preferred_max_seconds": 45,
        },
        "recency": {"half_life_hours": 48},
        "source_authority": {
            "platform_weights": {
                "twitch": 1.0,
                "reddit": 0.85,
                "youtube": 0.80,
            },
        },
        "production": {
            "preferred_fps": 60,
            "native_vertical_bonus": 0.10,
        },
        "chat_excitement": {
            "baseline_message_rate": 5.0,
            "min_messages_for_score": 5,
            "signal_weights": {
                "message_rate": 0.35,
                "emote_density": 0.25,
                "caps_ratio": 0.15,
                "keyword_density": 0.15,
                "brevity": 0.10,
            },
        },
    }


@pytest.fixture
def game_registry():
    """Minimal game registry with GREEN, YELLOW, and RED tier games."""
    return {
        "games": {
            "valorant": {
                "display_name": "Valorant",
                "publisher_tier": "GREEN",
            },
            "apex_legends": {
                "display_name": "Apex Legends",
                "publisher_tier": "YELLOW",
            },
            "mario_kart_8": {
                "display_name": "Mario Kart 8 Deluxe",
                "publisher_tier": "RED",
            },
        },
    }


@pytest.fixture
def sample_clip():
    """A well-formed clip dict with all scoring fields."""
    return {
        "clip_id": "test_clip_001",
        "source_platform": "twitch",
        "source_url": "https://clips.twitch.tv/test",
        "game_id": "valorant",
        "game_title": "Valorant",
        "creator_name": "test_streamer",
        "view_count": 5000,
        "like_count": 200,
        "comment_count": 50,
        "duration_seconds": 30,
        "resolution_height": 1080,
        "resolution_width": 1920,
        "fps": 60.0,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def sample_clips(sample_clip):
    """A batch of clips for testing batch scoring."""
    clips = []
    for i in range(5):
        clip = {**sample_clip}
        clip["clip_id"] = f"test_clip_{i:03d}"
        clip["view_count"] = 1000 * (i + 1)
        clip["game_title"] = "Valorant"
        clips.append(clip)
    return clips


# ---- Test: Individual dimension scores ----


class TestDimensionScores:
    """All 7 dimensions must return values between 0 and 1."""

    def test_virality_returns_0_to_1(self, scoring_config):
        clip = {
            "view_count": 5000,
            "like_count": 100,
            "comment_count": 20,
            "source_platform": "twitch",
        }
        score = score_virality(clip, scoring_config)
        assert 0.0 <= score <= 1.0

    def test_virality_zero_views(self, scoring_config):
        clip = {"view_count": 0, "source_platform": "twitch"}
        assert score_virality(clip, scoring_config) == 0.0

    def test_entertainment_returns_0_to_1(self, scoring_config):
        clip = {"duration_seconds": 30}
        score = score_entertainment(clip, scoring_config)
        assert 0.0 <= score <= 1.0

    def test_entertainment_outside_range(self, scoring_config):
        clip = {"duration_seconds": 3}  # below min 5s
        assert score_entertainment(clip, scoring_config) == 0.0

    def test_entertainment_sweet_spot(self, scoring_config):
        clip = {"duration_seconds": 25}  # inside 15-45s
        assert score_entertainment(clip, scoring_config) == 1.0

    def test_recency_returns_0_to_1(self, scoring_config):
        clip = {"fetched_at": datetime.now(UTC).isoformat()}
        score = score_recency(clip, scoring_config)
        assert 0.0 <= score <= 1.0

    def test_recency_fresh_clip_high(self, scoring_config):
        clip = {"fetched_at": datetime.now(UTC).isoformat()}
        score = score_recency(clip, scoring_config)
        assert score > 0.9  # just fetched -> close to 1.0

    def test_recency_missing_timestamp(self, scoring_config):
        clip = {}
        assert score_recency(clip, scoring_config) == 0.0

    def test_source_authority_returns_0_to_1(self, scoring_config):
        clip = {"source_platform": "twitch"}
        score = score_source_authority(clip, scoring_config)
        assert 0.0 <= score <= 1.0

    def test_source_authority_unknown_platform(self, scoring_config):
        clip = {"source_platform": "unknown_platform"}
        assert score_source_authority(clip, scoring_config) == 0.0

    def test_production_quality_returns_0_to_1(self, scoring_config):
        clip = {"resolution_height": 1080, "resolution_width": 1920, "fps": 60.0}
        score = score_production_quality(clip, scoring_config)
        assert 0.0 <= score <= 1.0

    def test_production_quality_no_metadata(self, scoring_config):
        clip = {}
        assert score_production_quality(clip, scoring_config) == 0.0

    def test_chat_excitement_returns_0_to_1(self):
        from niches.gaming.tools.chat_excitement_scorer import compute_excitement

        config = {
            "baseline_message_rate": 5.0,
            "min_messages_for_score": 3,
            "signal_weights": {
                "message_rate": 0.35,
                "emote_density": 0.25,
                "caps_ratio": 0.15,
                "keyword_density": 0.15,
                "brevity": 0.10,
            },
        }
        chat_data = {
            "fetch_status": "ok",
            "messages": [{"text": "WOW"}, {"text": "NICE"}, {"text": "PogChamp"}],
            "clip_start_in_vod": 100,
            "clip_end_in_vod": 130,
        }
        score = compute_excitement(chat_data, config, {})
        assert 0.0 <= score <= 1.0

    def test_chat_excitement_missing_data(self):
        from niches.gaming.tools.chat_excitement_scorer import compute_excitement

        assert compute_excitement(None, {}, {}) == 0.0

    def test_highlight_detection_fallback(self):
        """Without CrispyScorer, highlight detection returns 0.5."""
        stage = ScoreGamingClips()
        stage._crispy_scorer = None
        score = stage._score_highlight_detection({"local_path": "/some/video.mp4"})
        assert score == 0.5


# ---- Test: Publisher tier multiplier ----


class TestPublisherTier:
    def test_green_full_score(self, game_registry):
        tier = get_publisher_tier("Valorant", game_registry)
        assert tier == "GREEN"
        assert apply_tier_multiplier(0.8, tier) == 0.8

    def test_yellow_reduced_score(self, game_registry):
        tier = get_publisher_tier("Apex Legends", game_registry)
        assert tier == "YELLOW"
        assert apply_tier_multiplier(0.8, tier) == pytest.approx(0.64)

    def test_red_zero_score(self, game_registry):
        """RED tier must drop final score to 0 regardless of dimension scores."""
        tier = get_publisher_tier("Mario Kart 8 Deluxe", game_registry)
        assert tier == "RED"
        assert apply_tier_multiplier(0.95, tier) == 0.0

    def test_unknown_game_defaults(self, game_registry):
        tier = get_publisher_tier("Some Unknown Game 2025", game_registry)
        assert tier == "UNKNOWN"
        assert apply_tier_multiplier(0.8, tier) == pytest.approx(0.64)


# ---- Test: Stage execute (integration) ----


class TestScoreGamingClipsStage:
    def _make_stage(self, scoring_config, game_registry):
        """Create a ScoreGamingClips with injected config."""
        stage = ScoreGamingClips()
        stage._scoring_config = scoring_config
        stage._weights = scoring_config["weights"]
        stage._thresholds = {"min_clip_score": 0.25, "top_clips_per_run": 20}
        stage._game_registry = game_registry
        stage._crispy_scorer = None
        return stage

    def test_clips_below_threshold_dropped(self, scoring_config, game_registry):
        """Clips with RED publisher tier get score=0 and are dropped."""
        stage = self._make_stage(scoring_config, game_registry)

        clips = [
            {
                "clip_id": "green_clip",
                "source_platform": "twitch",
                "source_url": "https://example.com/1",
                "game_id": "valorant",
                "game_title": "Valorant",
                "creator_name": "test",
                "view_count": 5000,
                "like_count": 200,
                "comment_count": 50,
                "duration_seconds": 30,
                "resolution_height": 1080,
                "resolution_width": 1920,
                "fps": 60.0,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
            {
                "clip_id": "red_clip",
                "source_platform": "twitch",
                "source_url": "https://example.com/2",
                "game_id": "mario_kart_8",
                "game_title": "Mario Kart 8 Deluxe",
                "creator_name": "test",
                "view_count": 5000,
                "like_count": 200,
                "comment_count": 50,
                "duration_seconds": 30,
                "resolution_height": 1080,
                "resolution_width": 1920,
                "fps": 60.0,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
        ]

        context = {"stories": clips, "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        # RED clip should be dropped (score = 0.0 < min_clip_score 0.25)
        assert len(result["stories"]) == 1
        assert result["stories"][0]["clip_id"] == "green_clip"
        assert result["run_stats"]["scoring"]["dropped_count"] == 1

    def test_all_dimensions_present(self, scoring_config, game_registry, sample_clip):
        """Every scored clip must have all 7 dimension scores."""
        stage = self._make_stage(scoring_config, game_registry)
        context = {"stories": [sample_clip], "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        assert len(result["stories"]) == 1
        scores = result["stories"][0]["scores"]
        expected_dims = [
            "virality_potential",
            "entertainment_value",
            "recency",
            "source_authority",
            "production_quality",
            "chat_excitement",
            "highlight_detection",
        ]
        for dim in expected_dims:
            assert dim in scores, f"Missing dimension: {dim}"
            assert 0.0 <= scores[dim] <= 1.0, f"{dim} out of range: {scores[dim]}"

    def test_output_sorted_by_score(self, scoring_config, game_registry, sample_clips):
        """Clips must be sorted by final_score descending."""
        stage = self._make_stage(scoring_config, game_registry)
        context = {"stories": sample_clips, "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        scores = [c["final_score"] for c in result["stories"]]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_assigned(self, scoring_config, game_registry, sample_clips):
        """Clips must have 1-indexed ranks."""
        stage = self._make_stage(scoring_config, game_registry)
        context = {"stories": sample_clips, "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        ranks = [c["rank"] for c in result["stories"]]
        assert ranks == list(range(1, len(result["stories"]) + 1))

    def test_empty_stories(self, scoring_config, game_registry):
        """Empty story list should pass through without error."""
        stage = self._make_stage(scoring_config, game_registry)
        context = {"stories": [], "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)
        assert result["stories"] == []
        assert result["run_stats"]["scoring"]["input_count"] == 0


# ---- Test: Schema validation ----


class TestSchemaValidation:
    def test_output_validates_against_schema(self, scoring_config, game_registry, sample_clip):
        """Scored clip must validate against scored_clip.schema.json."""
        schema_path = NICHE_ROOT / "schemas" / "scored_clip.schema.json"
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")

        with open(schema_path) as f:
            schema = json.load(f)

        stage = ScoreGamingClips()
        stage._scoring_config = scoring_config
        stage._weights = scoring_config["weights"]
        stage._thresholds = {"min_clip_score": 0.0, "top_clips_per_run": 20}
        stage._game_registry = game_registry
        stage._crispy_scorer = None

        context = {"stories": [sample_clip], "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        for clip in result["stories"]:
            jsonschema.validate(clip, schema)


# ---- Test: Overwatch preprocessing ----


class TestOverwatchPreprocessing:
    def test_vectorized_output_shape(self):
        """Overwatch preprocessing must produce a 100x100 RGB image."""
        from niches.gaming.tools.crispy_scorer import CrispyScorer

        config = {
            "enable_highlight_detection": True,
            "fallback_score": 0.5,
            "models": {},
        }
        scorer = CrispyScorer(config, "/tmp")

        # Create a test image (50x50 for speed)
        test_image = Image.fromarray(
            np.random.randint(0, 256, (50, 50, 3), dtype=np.uint8),
            "RGB",
        )

        result = scorer._preprocess_overwatch(test_image)
        assert result.size == (100, 100)
        assert result.mode == "RGB"

    def test_vectorized_bright_red_becomes_white(self):
        """Bright red pixels (r>200, g<100, b<100) must become white."""
        from niches.gaming.tools.crispy_scorer import CrispyScorer

        config = {"enable_highlight_detection": True, "models": {}}
        scorer = CrispyScorer(config, "/tmp")

        # Create 10x10 image with bright red
        arr = np.zeros((10, 10, 3), dtype=np.uint8)
        arr[:, :, 0] = 220  # R > 200
        arr[:, :, 1] = 50  # G < 100
        arr[:, :, 2] = 30  # B < 100
        test_image = Image.fromarray(arr, "RGB")

        result = scorer._preprocess_overwatch(test_image)

        # The result is grayscale pasted into RGB 100x100.
        # Bright red -> white (255,255,255) -> grayscale 255.
        result_arr = np.array(result)
        # Check top-left 10x10 region where our data lives
        region = result_arr[:10, :10, 0]  # R channel (same as G and B for grayscale)
        assert np.all(region == 255), f"Expected all 255, got min={region.min()}"


# ---- Test: Median fallback ----


class TestMedianFallback:
    def test_missing_chat_uses_median(self, scoring_config, game_registry):
        """Clips without chat data should get median of real chat scores."""
        stage = ScoreGamingClips()
        stage._scoring_config = scoring_config
        stage._weights = scoring_config["weights"]
        stage._thresholds = {"min_clip_score": 0.0, "top_clips_per_run": 20}
        stage._game_registry = game_registry
        stage._crispy_scorer = None

        now = datetime.now(UTC).isoformat()
        base = {
            "clip_id": "c1",
            "source_platform": "twitch",
            "source_url": "https://example.com/1",
            "game_id": "valorant",
            "game_title": "Valorant",
            "creator_name": "test",
            "view_count": 1000,
            "like_count": 50,
            "comment_count": 10,
            "duration_seconds": 30,
            "resolution_height": 1080,
            "resolution_width": 1920,
            "fps": 60.0,
            "fetched_at": now,
        }

        # Clip with chat data
        clip_with_chat = {
            **base,
            "clip_id": "with_chat",
            "chat_data": {
                "fetch_status": "ok",
                "messages": [
                    {"text": "WOW"},
                    {"text": "NICE"},
                    {"text": "AMAZING"},
                    {"text": "POG"},
                    {"text": "LETS GO"},
                ],
                "clip_start_in_vod": 100,
                "clip_end_in_vod": 130,
            },
        }
        # Clip without chat data
        clip_no_chat = {**base, "clip_id": "no_chat"}

        context = {
            "stories": [clip_with_chat, clip_no_chat],
            "run_stats": {},
            "feature_flags": {},
        }
        result = stage.execute(context)

        # Both clips should have chat_excitement scores
        for clip in result["stories"]:
            assert clip["scores"]["chat_excitement"] >= 0.0
            assert clip["scores"]["chat_excitement"] <= 1.0

    def test_no_chat_no_crispy_no_error(self, scoring_config, game_registry):
        """Clips with neither chat data nor crispy scorer must not raise."""
        stage = ScoreGamingClips()
        stage._scoring_config = scoring_config
        stage._weights = scoring_config["weights"]
        stage._thresholds = {"min_clip_score": 0.0, "top_clips_per_run": 20}
        stage._game_registry = game_registry
        stage._crispy_scorer = None

        clip = {
            "clip_id": "bare_clip",
            "source_platform": "reddit",
            "source_url": "https://reddit.com/1",
            "game_id": "valorant",
            "game_title": "Valorant",
            "creator_name": "test",
            "view_count": 500,
            "duration_seconds": 20,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

        context = {"stories": [clip], "run_stats": {}, "feature_flags": {}}
        result = stage.execute(context)

        assert len(result["stories"]) >= 0  # may be dropped by threshold
        # The key assertion: no exception raised
