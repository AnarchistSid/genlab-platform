"""Tests for ClutchWire push_to_backlog stage.

Now delegates to the shared genlab_core.pipeline.stages.push_to_backlog.PushToBacklog.
The niche_id is read from context rather than a module-level constant.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

SETTINGS_PATCH = "genlab_core.pipeline.stages.push_to_backlog.settings"
POSTGRES_ENV_PATCH = "genlab_core.pipeline.stages.push_to_backlog.os.getenv"


def _recent_iso() -> str:
    """Return an ISO timestamp from 1 day ago (within the 7-day freshness gate)."""
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


@pytest.fixture
def stage():
    return PushToBacklog()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.find_story_by_story_id.return_value = None
    client.stories.create.return_value = {"id": "rec_story_1"}
    client.blueprints.all.return_value = []
    client.blueprints.create.return_value = {"id": "rec_bp_1"}
    return client


@pytest.fixture
def sample_context():
    return {
        "niche_id": "sports",
        "stories": [
            {
                "title": "Lakers vs Celtics Game 7",
                "source_url": "https://espn.com/lakers-celtics",
                "published_at": _recent_iso(),
                "source": "espn",
                "summary": "Historic Game 7 showdown between the two most storied franchises.",
                "score": 0.92,
                "content": {
                    "hook": "The rivalry that defines basketball.",
                    "instagram": {
                        "caption": "Game 7. Lakers. Celtics. History.",
                        "hashtags": ["#NBA", "#Finals"],
                    },
                    "youtube": {"title": "Lakers vs Celtics G7", "description": "Full breakdown"},
                    "x_twitter": {"tweet": "The rivalry continues."},
                    "facebook": {"caption": "Lakers vs Celtics — Game 7 tonight."},
                },
            },
        ],
    }


class TestNicheId:
    def test_push_story_writes_sports_niche_id(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.stories.create.call_args[0][0]
        assert call_args["niche_id"] == "sports"

    def test_push_blueprint_writes_sports_niche_id(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.blueprints.create.call_args[0][0]
        assert call_args["niche_id"] == "sports"

    def test_push_blueprint_includes_hook_field(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.blueprints.create.call_args[0][0]
        assert call_args["hook_text"] == "The rivalry that defines basketball."

    def test_raises_without_niche_id(self, stage):
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            with pytest.raises(ValueError, match="niche_id"):
                stage.execute({"stories": [{"title": "test"}]})


class TestVisualReadyStatus:
    def test_status_drafted_when_no_rendered_path(self, stage, mock_client, sample_context):
        """Blueprint status is DRAFTED when story has no media.rendered_path."""
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.blueprints.create.call_args[0][0]
        assert call_args["status"] == "DRAFTED"

    def test_status_visual_ready_when_rendered_path(self, stage, mock_client, sample_context):
        """Blueprint status is VISUAL_READY when story has media.rendered_path."""
        sample_context["stories"][0]["media"] = {
            "rendered_path": "/tmp/renders/lakers_celtics_g7.mp4",
        }
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.blueprints.create.call_args[0][0]
        assert call_args["status"] == "VISUAL_READY"
        assert call_args["visual_paths"] == '["/tmp/renders/lakers_celtics_g7.mp4"]'


class TestErrorHandling:
    def test_skips_when_no_credentials(self, stage):
        with (
            patch(SETTINGS_PATCH) as mock_settings,
            patch.dict("os.environ", {"GENLAB_USE_POSTGRES": ""}, clear=False),
        ):
            mock_settings.azure_tenant_id = ""
            mock_settings.azure_client_id = ""
            mock_settings.azure_client_secret = ""
            mock_settings.sharepoint_site_id = ""
            result = stage.execute({"niche_id": "sports", "stories": [{"title": "test"}]})

        assert result["run_stats"]["backlog_push"]["status"] == "skipped_no_credentials"

    def test_handles_graph_api_error(self, stage, mock_client, sample_context):
        mock_client.find_story_by_story_id.side_effect = Exception("Graph API 429")
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            result = stage.execute(sample_context)

        assert "partial" in result["run_stats"]["backlog_push"]["status"]
