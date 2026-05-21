"""Shared pytest fixtures for the content intelligence pipeline."""

import json
import os
from unittest.mock import MagicMock

import pytest

# Disable Postgres so tests use SharePoint mocks (avoid schema gaps).
# Must set before any genlab_core import triggers pydantic-settings.
os.environ["GENLAB_USE_POSTGRES"] = "false"
os.environ.pop("DATABASE_URL", None)


@pytest.fixture
def mock_backlog_client():
    """A BacklogClient stub that avoids Azure init.

    All table properties return MagicMock objects whose methods can be
    asserted against (e.g., client.blueprints.all.return_value = [...]).
    """
    from genlab_core.http.backlog_client import BacklogClient

    client = BacklogClient.__new__(BacklogClient)
    for table in (
        "stories",
        "blueprints",
        "templates",
        "assets",
        "sources",
        "publishing_analytics",
        "analytics",
        "ab_tests",
    ):
        setattr(client, table, MagicMock())
    return client


@pytest.fixture
def run_dir(tmp_path):
    """Create a minimal run directory with an empty media_index.

    Returns (run_id, run_dir_path).
    """
    run_id = "test_run"
    d = tmp_path / ".tmp" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "media_index.json").write_text(json.dumps({}))
    return run_id, d


@pytest.fixture
def sample_blueprint():
    """A VISUAL_READY carousel blueprint with standard fields."""
    return {
        "id": "42",
        "fields": {
            "candidate_id": "abc123def456",
            "status": "VISUAL_READY",
            "format": "reel",
            "hook": "AI just changed everything",
            "caption": "Big news in AI world today.",
            "hashtags": "#AI #ML #Tech",
            "scheduled_for": "",
            "visual_paths": json.dumps(
                [
                    "https://cdn.example.com/slide1.png",
                    "https://cdn.example.com/slide2.png",
                    "https://cdn.example.com/slide3.png",
                ]
            ),
            "platform_publish_status": "",
            "story": [],
            "youtube_content": json.dumps({"text": "YouTube community post"}),
            "twitter_content": json.dumps(
                {
                    "strategy": "single_tweet",
                    "tweets": [{"text": "AI just changed everything #AI"}],
                }
            ),
        },
    }
