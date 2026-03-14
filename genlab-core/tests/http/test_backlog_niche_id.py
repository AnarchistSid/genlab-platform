"""Tests for niche_id passthrough in BacklogClient create methods.

Verifies that create_story() and create_blueprint() include niche_id
in the fields dict when provided in the input, enabling multi-niche
filtering on the shared SharePoint lists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports():
    """Mock Azure SDK and msgraph so tests don't require them installed."""
    mock_cred_cls = MagicMock()
    mock_graph_cls = MagicMock()

    with (
        patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.identity": MagicMock(ClientSecretCredential=mock_cred_cls),
            "msgraph": MagicMock(GraphServiceClient=mock_graph_cls),
            "kiota_abstractions": MagicMock(),
            "kiota_abstractions.api_error": MagicMock(),
        }),
        patch("genlab_core.http.backlog_client.GraphTableProxy"),
    ):
        yield


@pytest.fixture
def mock_config(tmp_path):
    import yaml
    config = {
        "Stories": {"list_id": "list-stories"},
        "Blueprints": {"list_id": "list-blueprints"},
        "Templates": {"list_id": "list-templates"},
        "Assets": {"list_id": "list-assets"},
        "Sources": {"list_id": "list-sources"},
        "Publishing_Analytics": {"list_id": "list-pub"},
        "Analytics": {"list_id": "list-analytics"},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "lists_config.yaml"
    config_file.write_text(yaml.dump(config))
    return config_file


def _make_client(mock_config):
    mock_settings = MagicMock()
    mock_settings.azure_tenant_id = "t"
    mock_settings.azure_client_id = "c"
    mock_settings.azure_client_secret = "s"
    mock_settings.sharepoint_site_id = "site"

    with patch("genlab_core.settings.settings", mock_settings):
        from genlab_core.http.backlog_client import BacklogClient
        client = BacklogClient(config_path=mock_config)
    return client


class TestCreateStoryNicheId:
    def test_story_with_niche_id(self, mock_config):
        client = _make_client(mock_config)
        client.stories.create = MagicMock(return_value={"id": "rec1"})

        story = {
            "story_id": "sid_123",
            "title": "Test Story",
            "url": "https://example.com",
            "niche_id": "sports",
        }
        client.create_story(story)

        call_args = client.stories.create.call_args[0][0]
        assert call_args["niche_id"] == "sports"

    def test_story_without_niche_id(self, mock_config):
        client = _make_client(mock_config)
        client.stories.create = MagicMock(return_value={"id": "rec1"})

        story = {
            "story_id": "sid_123",
            "title": "Test Story",
            "url": "https://example.com",
        }
        client.create_story(story)

        call_args = client.stories.create.call_args[0][0]
        assert "niche_id" not in call_args

    def test_story_with_ai_creators_niche(self, mock_config):
        client = _make_client(mock_config)
        client.stories.create = MagicMock(return_value={"id": "rec1"})

        story = {
            "story_id": "sid_456",
            "title": "AI News Story",
            "url": "https://example.com/ai",
            "niche_id": "ai_creators",
        }
        client.create_story(story)

        call_args = client.stories.create.call_args[0][0]
        assert call_args["niche_id"] == "ai_creators"


class TestCreateBlueprintNicheId:
    def test_blueprint_with_niche_id(self, mock_config):
        client = _make_client(mock_config)
        client.blueprints.create = MagicMock(return_value={"id": "rec_bp"})

        # Mock find_story to return a record
        client.find_story_by_story_id = MagicMock(return_value={"id": "rec_story"})

        blueprint = {
            "candidate_id": "cid_123",
            "story_id": "sid_123",
            "niche_id": "movies",
            "hook": "Test hook",
        }
        client.create_blueprint(blueprint)

        call_args = client.blueprints.create.call_args[0][0]
        assert call_args["niche_id"] == "movies"

    def test_blueprint_without_niche_id(self, mock_config):
        client = _make_client(mock_config)
        client.blueprints.create = MagicMock(return_value={"id": "rec_bp"})
        client.find_story_by_story_id = MagicMock(return_value={"id": "rec_story"})

        blueprint = {
            "candidate_id": "cid_123",
            "story_id": "sid_123",
            "hook": "Test hook",
        }
        client.create_blueprint(blueprint)

        call_args = client.blueprints.create.call_args[0][0]
        assert "niche_id" not in call_args
