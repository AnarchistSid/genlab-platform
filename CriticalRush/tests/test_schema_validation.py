"""Tests for JSON schema validation of CriticalRush pipeline outputs."""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

from niches.gaming.tools.schema_validator import validate_against_schema

# ---------------------------------------------------------------------------
# Fixtures: valid data blobs
# ---------------------------------------------------------------------------

def _valid_written_content() -> dict[str, Any]:
    return {
        "hook": "Elden Ring players can't believe this just happened",
        "instagram": {
            "caption": "The Elden Ring community is going absolutely wild right now. Follow for daily gaming content!",
            "hashtags": ["#eldenring", "#gaming", "#fromsoftware", "#gamer"],
        },
        "youtube": {
            "title": "What Happened to Elden Ring?",
            "description": "The Elden Ring community is buzzing. Here's why everyone is talking about it.",
        },
        "x_twitter": {
            "tweet": "Elden Ring just dropped something huge and the community can't stop talking about it",
            "hashtags": ["#EldenRing", "#gaming"],
        },
        "facebook": {
            "caption": "The Elden Ring community is losing it right now. Have you been keeping up? What's your take?",
        },
        "tiktok": {
            "caption": "Elden Ring players are LOSING IT right now and you need to see why",
            "hashtags": ["#gaming", "#eldenring", "#fyp"],
        },
        "threads": {
            "caption": "The Elden Ring community can't stop talking about this. What's your take?",
        },
    }


def _valid_clip_config() -> dict[str, Any]:
    return {
        "max_duration_seconds": 60,
        "min_duration_seconds": 5,
        "target_resolution": "1080",
        "trim_start_pct": 0.25,
        "output_dir": ".tmp/clips",
        "banned_fragments": ["reaction", "review"],
        "pexels_query_template": "{game_title} gameplay",
        "youtube_search_template": "{game_title} official trailer",
        "twitch_clip_limit": 5,
        "min_views": 1000,
    }


def _valid_enriched_story() -> dict[str, Any]:
    return {
        "title": "Elden Ring DLC breaks Steam records",
        "summary": "Shadow of the Erdtree launches to record concurrent players",
        "source_url": "https://example.com/article/123",
        "igdb_game_id": "119133",
        "steam_app_id": "1245620",
        "game_name": "Elden Ring",
        "genres": ["RPG", "Action"],
        "platforms": ["PC", "PlayStation 5", "Xbox Series X"],
        "cover_url": "https://images.igdb.com/igdb/image/upload/t_cover_big/co4jni.jpg",
        "rating": 96.0,
    }


# ---------------------------------------------------------------------------
# Tests: written_content.schema.json
# ---------------------------------------------------------------------------

class TestWrittenContentSchema:
    def test_valid_written_content_passes(self):
        data = _valid_written_content()
        valid, err = validate_against_schema(data, "written_content.schema.json")
        assert valid is True
        assert err is None

    def test_missing_required_field_fails(self):
        data = _valid_written_content()
        del data["hook"]
        valid, err = validate_against_schema(data, "written_content.schema.json")
        assert valid is False
        assert err is not None
        assert "hook" in err.lower()

    def test_missing_instagram_fails(self):
        data = _valid_written_content()
        del data["instagram"]
        valid, err = validate_against_schema(data, "written_content.schema.json")
        assert valid is False
        assert err is not None

    def test_empty_hook_fails(self):
        data = _valid_written_content()
        data["hook"] = ""
        valid, err = validate_against_schema(data, "written_content.schema.json")
        assert valid is False
        assert err is not None

    def test_with_optional_hook_prediction_score(self):
        data = _valid_written_content()
        data["hook_prediction_score"] = 0.85
        valid, err = validate_against_schema(data, "written_content.schema.json")
        assert valid is True
        assert err is None


# ---------------------------------------------------------------------------
# Tests: clip_sourcer_config.schema.json
# ---------------------------------------------------------------------------

class TestClipSourcerConfigSchema:
    def test_valid_clip_config_passes(self):
        data = _valid_clip_config()
        valid, err = validate_against_schema(data, "clip_sourcer_config.schema.json")
        assert valid is True
        assert err is None

    def test_empty_config_passes(self):
        """All fields have defaults, so an empty object is valid."""
        valid, err = validate_against_schema({}, "clip_sourcer_config.schema.json")
        assert valid is True
        assert err is None

    def test_invalid_trim_pct_fails(self):
        data = _valid_clip_config()
        data["trim_start_pct"] = 1.5  # exceeds maximum of 1
        valid, err = validate_against_schema(data, "clip_sourcer_config.schema.json")
        assert valid is False
        assert err is not None

    def test_extra_field_fails(self):
        """additionalProperties is false."""
        data = _valid_clip_config()
        data["unknown_field"] = True
        valid, err = validate_against_schema(data, "clip_sourcer_config.schema.json")
        assert valid is False
        assert err is not None


# ---------------------------------------------------------------------------
# Tests: enriched_story.schema.json
# ---------------------------------------------------------------------------

class TestEnrichedStorySchema:
    def test_enriched_story_validates(self):
        data = _valid_enriched_story()
        valid, err = validate_against_schema(data, "enriched_story.schema.json")
        assert valid is True
        assert err is None

    def test_minimal_story_validates(self):
        """Only title is required."""
        valid, err = validate_against_schema(
            {"title": "Some headline"}, "enriched_story.schema.json",
        )
        assert valid is True
        assert err is None

    def test_missing_title_fails(self):
        data = _valid_enriched_story()
        del data["title"]
        valid, err = validate_against_schema(data, "enriched_story.schema.json")
        assert valid is False
        assert err is not None


# ---------------------------------------------------------------------------
# Tests: graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_missing_schema_file_passes_gracefully(self):
        """Non-existent schema file should pass (no schema = no validation)."""
        valid, err = validate_against_schema(
            {"anything": "goes"}, "nonexistent_schema.schema.json",
        )
        assert valid is True
        assert err is None

    def test_no_jsonschema_passes_gracefully(self):
        """If jsonschema is not importable, validation should pass."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def _mock_import(name, *args, **kwargs):
            if name == "jsonschema":
                raise ImportError("mocked: jsonschema not installed")
            return original_import(name, *args, **kwargs)

        # Reload the module so we can intercept the import inside validate_against_schema
        # Instead, patch at the point of use
        with patch.dict(sys.modules, {"jsonschema": None}):
            # Force re-import inside the function by removing cached module
            saved = sys.modules.pop("jsonschema", None)
            sys.modules["jsonschema"] = None  # type: ignore[assignment]
            try:
                # The function does `import jsonschema` which will see None and raise ImportError
                valid, err = validate_against_schema(
                    {"hook": "test"}, "written_content.schema.json",
                )
                assert valid is True
                assert err is None
            finally:
                if saved is not None:
                    sys.modules["jsonschema"] = saved
                else:
                    sys.modules.pop("jsonschema", None)
