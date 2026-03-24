"""Tests for BacklogClient storage backend routing.

Verifies that all domain methods (create_story, find_blueprint_by_candidate_id,
etc.) delegate to the StorageBackend layer instead of directly using
GraphTableProxy instances.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports():
    """Mock Azure SDK and msgraph so tests don't require them installed.

    Also clears GENLAB_USE_POSTGRES so backend routing tests exercise
    the SharePoint code path regardless of what's in the local .env.
    """
    mock_cred_cls = MagicMock()
    mock_graph_cls = MagicMock()

    with (
        patch.dict("os.environ", {"GENLAB_USE_POSTGRES": "", "DATABASE_URL": ""}, clear=False),
        patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.identity": MagicMock(ClientSecretCredential=mock_cred_cls),
            "msgraph": MagicMock(GraphServiceClient=mock_graph_cls),
            "kiota_abstractions": MagicMock(),
            "kiota_abstractions.api_error": MagicMock(),
        }),
        patch("genlab_core.http.backlog_client.GraphTableProxy") as mock_proxy_cls,
    ):
        yield mock_proxy_cls


@pytest.fixture
def mock_config(tmp_path):
    """Create a minimal lists_config.yaml."""
    import yaml

    config = {
        "Stories": {"list_id": "list-stories"},
        "Blueprints": {"list_id": "list-blueprints"},
        "Templates": {"list_id": "list-templates"},
        "Assets": {"list_id": "list-assets"},
        "Sources": {"list_id": "list-sources"},
        "Publishing_Analytics": {"list_id": "list-pub"},
        "Analytics": {"list_id": "list-analytics"},
        "AB_Tests": {"list_id": "list-abtests"},
        "PendingEngagement": {"list_id": "list-pending"},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "lists_config.yaml"
    config_file.write_text(yaml.dump(config))
    return config_file


def _make_settings(**overrides):
    defaults = {
        "azure_tenant_id": "tenant",
        "azure_client_id": "client",
        "azure_client_secret": "secret",
        "sharepoint_site_id": "site",
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_client(mock_config):
    """Instantiate BacklogClient with mocked credentials and config."""
    mock_settings = _make_settings()
    with patch("genlab_core.settings.settings", mock_settings):
        from genlab_core.http.backlog_client import BacklogClient
        return BacklogClient(config_path=mock_config)


class TestBackendRouting:
    """Verify _backend() returns a SharePointBackend that delegates to proxies."""

    def test_backend_returns_sharepoint_backend(self, mock_config):
        from genlab_core.storage.sharepoint import SharePointBackend

        client = _make_client(mock_config)
        be = client._backend("Stories")
        assert isinstance(be, SharePointBackend)

    def test_backend_caches_per_engine(self, mock_config):
        client = _make_client(mock_config)
        be1 = client._backend("Stories")
        be2 = client._backend("Blueprints")
        # Both are sharepoint, so same cached instance
        assert be1 is be2

    def test_sp_proxies_includes_core_tables(self, mock_config):
        client = _make_client(mock_config)
        expected = {
            "Stories", "Blueprints", "Templates", "Assets",
            "Sources", "Publishing_Analytics", "Analytics",
        }
        assert expected.issubset(set(client._sp_proxies.keys()))

    def test_sp_proxies_includes_optional_tables(self, mock_config):
        client = _make_client(mock_config)
        # AB_Tests and PendingEngagement are in the config
        assert "AB_Tests" in client._sp_proxies
        assert "PendingEngagement" in client._sp_proxies


class TestStoriesBackendDelegation:
    """Verify Stories methods delegate through _backend()."""

    def test_create_story_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.create.return_value = {"id": "rec-new"}

        story = {
            "story_id": "sid_1",
            "title": "Test",
            "url": "https://example.com",
            "niche_id": "gaming",
        }
        result = client.create_story(story)

        assert result == "rec-new"
        # The proxy.create should have been called (via SharePointBackend)
        client.stories.create.assert_called_once()
        fields = client.stories.create.call_args[0][0]
        assert fields["story_id"] == "sid_1"
        assert fields["niche_id"] == "gaming"

    def test_find_story_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.all.return_value = [
            {"id": "rec-1", "fields": {"story_id": "sid_1"}}
        ]

        result = client.find_story_by_story_id("sid_1", niche_id="gaming")

        assert result["id"] == "rec-1"
        client.stories.all.assert_called_once()
        kwargs = client.stories.all.call_args[1]
        # niche_id should be injected into the formula by SharePointBackend.find
        assert "gaming" in kwargs["formula"]

    def test_update_story_status_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.all.return_value = [
            {"id": "rec-1", "fields": {"story_id": "sid_1"}}
        ]
        client.stories.update.return_value = {"id": "rec-1"}

        client.update_story_status("sid_1", "VALIDATED")

        client.stories.update.assert_called_once()
        args = client.stories.update.call_args[0]
        assert args[0] == "rec-1"
        assert args[1]["status"] == "VALIDATED"

    def test_batch_create_stories_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.batch_create.return_value = [
            {"id": "r1"}, {"id": "r2"},
        ]

        stories = [
            {"story_id": "s1", "title": "A", "url": "http://a"},
            {"story_id": "s2", "title": "B", "url": "http://b"},
        ]
        result = client.batch_create_stories(stories)

        assert result == ["r1", "r2"]
        client.stories.batch_create.assert_called_once()


class TestBlueprintsBackendDelegation:
    """Verify Blueprints methods delegate through _backend()."""

    def test_find_blueprint_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        # ScheduleGuardedProxy delegates .all to the underlying proxy via __getattr__
        client.blueprints._proxy.all.return_value = [
            {"id": "bp-1", "fields": {"candidate_id": "cid_1"}}
        ]

        result = client.find_blueprint_by_candidate_id("cid_1", niche_id="sports")

        assert result["id"] == "bp-1"
        client.blueprints._proxy.all.assert_called_once()
        kwargs = client.blueprints._proxy.all.call_args[1]
        assert "sports" in kwargs["formula"]

    def test_get_blueprints_by_status_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.blueprints._proxy.all.return_value = [
            {"id": "bp-1", "fields": {"status": "DRAFTED"}},
        ]

        result = client.get_blueprints_by_status("DRAFTED", niche_id="movies")

        assert len(result) == 1
        kwargs = client.blueprints._proxy.all.call_args[1]
        assert "DRAFTED" in kwargs["formula"]
        assert "movies" in kwargs["formula"]

    def test_update_blueprint_status_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.blueprints._proxy.all.return_value = [
            {"id": "bp-1", "fields": {"candidate_id": "cid_1", "status": "DRAFTED"}}
        ]
        client.blueprints._proxy.update.return_value = {"id": "bp-1"}
        client.blueprints._proxy.get.return_value = {
            "id": "bp-1", "fields": {"status": "DRAFTED"}
        }

        client.update_blueprint_status("cid_1", "VISUAL_READY")

        # The ScheduleGuardedProxy.update is called, which delegates to _proxy.update
        client.blueprints._proxy.update.assert_called_once()

    def test_batch_create_blueprints_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        # Mock find_story
        client.stories.all.return_value = [{"id": "story-1", "fields": {}}]
        client.blueprints._proxy.batch_create.return_value = [{"id": "bp-1"}]

        bps = [{"candidate_id": "c1", "story_id": "s1"}]
        result = client.batch_create_blueprints(bps)

        assert result == ["bp-1"]


class TestTemplatesBackendDelegation:
    def test_create_template_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.templates.create.return_value = {"id": "tmpl-1"}

        template = {
            "template_id": "t1", "name": "Test", "format": "reel",
        }
        result = client.create_template(template)

        assert result == "tmpl-1"
        client.templates.create.assert_called_once()

    def test_find_template_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.templates.all.return_value = [
            {"id": "tmpl-1", "fields": {"template_id": "t1"}}
        ]

        result = client.find_template_by_template_id("t1")

        assert result["id"] == "tmpl-1"
        client.templates.all.assert_called_once()

    def test_get_active_templates_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.templates.all.return_value = []

        client.get_active_templates(niche_id="anime")

        client.templates.all.assert_called_once()
        kwargs = client.templates.all.call_args[1]
        assert "anime" in kwargs["formula"]


class TestAssetsBackendDelegation:
    def test_find_asset_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.assets.all.return_value = [
            {"id": "a-1", "fields": {"asset_id": "aid1"}}
        ]

        result = client.find_asset_by_asset_id("aid1", niche_id="gaming")

        assert result["id"] == "a-1"
        client.assets.all.assert_called_once()
        kwargs = client.assets.all.call_args[1]
        assert "gaming" in kwargs["formula"]

    def test_update_asset_status_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.assets.all.return_value = [
            {"id": "a-1", "fields": {"asset_id": "aid1"}}
        ]
        client.assets.update.return_value = {"id": "a-1"}

        client.update_asset_status("aid1", "READY")

        client.assets.update.assert_called_once()
        args = client.assets.update.call_args[0]
        assert args[0] == "a-1"
        assert args[1]["status"] == "READY"


class TestSourcesBackendDelegation:
    def test_create_source_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.sources.create.return_value = {"id": "src-1"}

        source = {
            "source_id": "s1", "domain": "example.com",
            "url": "https://example.com", "type": "rss",
        }
        result = client.create_source(source)

        assert result == "src-1"
        client.sources.create.assert_called_once()

    def test_get_enabled_sources_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.sources.all.return_value = []

        client.get_enabled_sources(niche_id="gaming")

        client.sources.all.assert_called_once()
        kwargs = client.sources.all.call_args[1]
        assert "gaming" in kwargs["formula"]


class TestAnalyticsBackendDelegation:
    def test_upsert_analytics_create_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.analytics.all.return_value = []
        client.analytics.create.return_value = {"id": "an-1"}

        result = client.upsert_analytics(
            post_id="post1", platform="instagram",
            insights={"reach": 100, "engagement": 10},
        )

        assert result == "an-1"
        client.analytics.create.assert_called_once()

    def test_upsert_analytics_update_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.analytics.all.return_value = [
            {"id": "an-1", "fields": {"post_id": "instagram:post1"}}
        ]
        client.analytics.update.return_value = {"id": "an-1"}

        result = client.upsert_analytics(
            post_id="post1", platform="instagram",
            insights={"reach": 200, "engagement": 20},
        )

        assert result == "an-1"
        client.analytics.update.assert_called_once()


class TestPublishingAnalyticsBackendDelegation:
    def test_log_publish_result_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.publishing_analytics.all.return_value = []
        client.publishing_analytics.create.return_value = {"id": "pa-1"}

        result = client.log_publish_result(
            candidate_id="cand-1", platform="instagram",
            status="SUCCESS", niche_id="gaming",
        )

        assert result == "pa-1"
        client.publishing_analytics.create.assert_called_once()
        fields = client.publishing_analytics.create.call_args[0][0]
        assert fields["niche_id"] == "gaming"

    def test_get_publishing_analytics_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.publishing_analytics.all.return_value = []

        client.get_publishing_analytics(
            platform="instagram", niche_id="gaming",
        )

        client.publishing_analytics.all.assert_called_once()
        kwargs = client.publishing_analytics.all.call_args[1]
        assert "gaming" in kwargs["formula"]


class TestABTestsBackendDelegation:
    def test_create_ab_test_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.ab_tests.create.return_value = {"id": "ab-1"}

        result = client.create_ab_test({"test_id": "t1", "status": "active"})

        assert result == "ab-1"
        client.ab_tests.create.assert_called_once()

    def test_get_ab_tests_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.ab_tests.all.return_value = []

        client.get_ab_tests(status="active", niche_id="gaming")

        client.ab_tests.all.assert_called_once()
        kwargs = client.ab_tests.all.call_args[1]
        assert "gaming" in kwargs["formula"]


class TestEngagementBackendDelegation:
    def test_write_pending_engagement_goes_through_proxy(self, mock_config):
        client = _make_client(mock_config)
        mock_proxy = MagicMock()
        mock_proxy.create.return_value = "pe-1"
        client.pending_engagement = mock_proxy

        result = client.write_pending_engagement({
            "comment_id": "cmt_1", "platform": "instagram",
        })

        assert result == "pe-1"
        mock_proxy.create.assert_called_once()
        # Fields should use snake_case
        call_fields = mock_proxy.create.call_args[0][0]
        assert call_fields["platform"] == "instagram"

    def test_list_pending_engagement_goes_through_proxy(self, mock_config):
        client = _make_client(mock_config)
        mock_proxy = MagicMock()
        mock_proxy.find.return_value = []
        client.pending_engagement = mock_proxy

        client.list_pending_engagement(niche_id="gaming")

        mock_proxy.find.assert_called_once()
        kwargs = mock_proxy.find.call_args[1]
        assert "gaming" in kwargs["formula"]

    def test_update_engagement_status_goes_through_proxy(self, mock_config):
        client = _make_client(mock_config)
        mock_proxy = MagicMock()
        client.pending_engagement = mock_proxy

        client.update_engagement_status("item-1", "replied", reply_text="Thanks!")

        mock_proxy.update.assert_called_once()
        call_args = mock_proxy.update.call_args[0]
        assert call_args[0] == "item-1"
        fields = call_args[1]
        assert fields["status"] == "replied"


class TestHealthCheckBackendDelegation:
    def test_health_check_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.all.return_value = [{"id": "1"}]

        result = client.health_check()

        assert result is True
        client.stories.all.assert_called_once()

    def test_health_check_failure_goes_through_backend(self, mock_config):
        client = _make_client(mock_config)
        client.stories.all.side_effect = RuntimeError("Connection failed")

        with patch("genlab_core.settings.settings") as mock_settings:
            mock_settings.azure_client_secret = ""
            result = client.health_check()

        assert result is False


class TestAttachmentBackendDelegation:
    def test_upload_attachment_goes_through_backend(self, mock_config, tmp_path):
        client = _make_client(mock_config)
        # ScheduleGuardedProxy.__getattr__ delegates upload_attachment
        client.blueprints._proxy.upload_attachment.return_value = {
            "filename": "test.png"
        }

        test_file = tmp_path / "test.png"
        test_file.write_bytes(b"png data")

        result = client.upload_attachment("rec-1", "cover", test_file)

        assert result["filename"] == "test.png"
        client.blueprints._proxy.upload_attachment.assert_called_once()
