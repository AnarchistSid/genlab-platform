"""Tests for FrameDrift push_to_backlog stage.

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
    client.stories.all.return_value = []  # PR #175: upsert uses stories.all(formula=)
    client.stories.create.return_value = {"id": "rec_story_1"}
    client.blueprints.all.return_value = []
    client.blueprints.create.return_value = {"id": "rec_bp_1"}
    return client


@pytest.fixture
def sample_context():
    return {
        "niche_id": "anime",
        "stories": [
            {
                "title": "Chainsaw Man Part 2 Anime Announced",
                "source_url": "https://anilist.co/chainsaw-man-s2",
                "published_at": _recent_iso(),
                "source": "anilist",
                "summary": "MAPPA confirms Chainsaw Man Part 2 anime for Fall 2026.",
                "score": 0.95,
                "content": {
                    "hook": "Denji is back. MAPPA is cooking.",
                    "instagram": {
                        "caption": "CSM Part 2 confirmed.",
                        "hashtags": ["#ChainsawMan", "#Anime"],
                    },
                    "youtube": {"title": "CSM S2 Announced", "description": "Everything we know"},
                    "x_twitter": {"tweet": "CHAINSAW MAN PART 2 LETS GO"},
                    "facebook": {"caption": "Chainsaw Man Part 2 anime confirmed for Fall 2026."},
                },
            },
        ],
    }


class TestNicheId:
    def test_push_story_writes_anime_niche_id(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.stories.create.call_args[0][0]
        assert call_args["niche_id"] == "anime"

    def test_push_blueprint_writes_anime_niche_id(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.blueprints.create.call_args[0][0]
        assert call_args["niche_id"] == "anime"

    def test_raises_without_niche_id(self, stage):
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            with pytest.raises(ValueError, match="niche_id"):
                stage.execute({"stories": [{"title": "test"}]})


class TestFieldMapping:
    def test_anilist_story_fields_mapped(self, stage, mock_client, sample_context):
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            stage.execute(sample_context)

        call_args = mock_client.stories.create.call_args[0][0]
        assert call_args["title"] == "Chainsaw Man Part 2 Anime Announced"
        assert call_args["source"] == "anilist"
        assert call_args["status"] == "INTAKE"


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
            "rendered_path": "/tmp/renders/csm_part2.mp4",
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
        assert call_args["visual_paths"] == '["/tmp/renders/csm_part2.mp4"]'


class TestErrorHandling:
    def test_handles_graph_api_error(self, stage, mock_client, sample_context):
        mock_client.find_story_by_story_id.side_effect = Exception("Graph API 429")
        mock_client.stories.all.side_effect = Exception("Graph API 429")  # PR #175: upsert uses stories.all(formula=)
        stage._client = mock_client
        with patch(SETTINGS_PATCH) as mock_settings:
            mock_settings.azure_tenant_id = "t"
            mock_settings.azure_client_id = "c"
            mock_settings.azure_client_secret = "s"
            mock_settings.sharepoint_site_id = "site"
            result = stage.execute(sample_context)

        assert "partial" in result["run_stats"]["backlog_push"]["status"]
