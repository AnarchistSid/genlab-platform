"""Tests for BacklogClient.update_engagement_status (Sprint 24 — engagement dispatch).

Validates status updates on PendingEngagement items without requiring
Azure credentials by mocking the storage backend layer.
"""

from __future__ import annotations

from genlab_core.http.engagement_store import EngagementStore
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports():
    """Mock Azure SDK and msgraph so tests don't require them installed."""
    mock_cred_cls = MagicMock()
    mock_graph_cls = MagicMock()

    with (
        patch.dict(
            "sys.modules",
            {
                "azure": MagicMock(),
                "azure.identity": MagicMock(ClientSecretCredential=mock_cred_cls),
                "msgraph": MagicMock(GraphServiceClient=mock_graph_cls),
                "kiota_abstractions": MagicMock(),
                "kiota_abstractions.api_error": MagicMock(),
            },
        ),
        patch("genlab_core.http.backlog_client.GraphTableProxy") as mock_proxy_cls,
    ):
        yield mock_proxy_cls


@pytest.fixture
def mock_config(tmp_path):
    """Create a minimal lists_config.yaml including PendingEngagement."""
    import yaml

    config = {
        "Stories": {"list_id": "list-stories"},
        "Blueprints": {"list_id": "list-blueprints"},
        "Templates": {"list_id": "list-templates"},
        "Assets": {"list_id": "list-assets"},
        "Sources": {"list_id": "list-sources"},
        "Publishing_Analytics": {"list_id": "list-pub"},
        "Analytics": {"list_id": "list-analytics"},
        "PendingEngagement": {"list_id": "list-pending-engagement"},
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "lists_config.yaml"
    config_file.write_text(yaml.dump(config))
    return config_file


def _make_settings(**overrides):
    defaults = {
        "azure_tenant_id": "tenant-test",
        "azure_client_id": "client-test",
        "azure_client_secret": "secret-test",
        "sharepoint_site_id": "site-test",
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


def _make_client_with_backend(mock_config):
    """Instantiate BacklogClient and replace pending_engagement proxy with a mock.

    Returns (client, mock_proxy) where mock_proxy is the proxy object
    used by engagement methods for create/find/update operations.
    """
    client = _make_client(mock_config)
    mock_proxy = MagicMock()
    client.pending_engagement = mock_proxy
    client._engagement = EngagementStore(mock_proxy)
    return client, mock_proxy


class TestUpdateEngagementStatus:
    def test_update_engagement_status_replied(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        client.update_engagement_status("item-42", "replied", reply_text="Thanks for watching!")

        mock_proxy.update.assert_called_once()
        call_args = mock_proxy.update.call_args[0]
        item_id = call_args[0]
        fields = call_args[1]
        assert item_id == "item-42"
        assert fields["status"] == "replied"
        assert "processed_at" in fields
        # processed_at should be a valid ISO timestamp string
        assert "T" in fields["processed_at"]
        assert fields["reply_text"] == "Thanks for watching!"

    def test_update_engagement_status_failed_with_error(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        client.update_engagement_status("item-99", "failed", error_msg="Rate limit exceeded")

        call_args = mock_proxy.update.call_args[0]
        fields = call_args[1]
        assert fields["status"] == "failed"
        assert fields["error_message"] == "Rate limit exceeded"

    def test_update_engagement_status_truncates_reply(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        long_reply = "x" * 5000
        client.update_engagement_status("item-1", "replied", reply_text=long_reply)

        call_args = mock_proxy.update.call_args[0]
        fields = call_args[1]
        assert len(fields["reply_text"]) == 2000

    def test_update_engagement_status_truncates_error(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        long_error = "e" * 1000
        client.update_engagement_status("item-2", "failed", error_msg=long_error)

        call_args = mock_proxy.update.call_args[0]
        fields = call_args[1]
        assert len(fields["error_message"]) == 500

    def test_update_engagement_status_invalid_status(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        client.update_engagement_status("item-3", "bogus_status")

        mock_proxy.update.assert_not_called()

    def test_update_engagement_status_no_proxy(self, mock_config):
        client = _make_client(mock_config)
        client.pending_engagement = None
        client._engagement = EngagementStore(None)

        # Should not raise
        client.update_engagement_status("item-4", "replied")

    def test_update_engagement_status_api_error(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.update.side_effect = RuntimeError("Graph API down")

        # Should not raise — catches and logs
        client.update_engagement_status("item-5", "liked")
