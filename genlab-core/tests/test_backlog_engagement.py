"""Tests for BacklogClient engagement methods (Sprint 23 — observe-only layer).

Validates write_pending_engagement and list_pending_engagement without
requiring Azure credentials by mocking the storage backend layer.
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
    return client, mock_proxy


SAMPLE_EVENT = {
    "comment_id": "cmt_abc123",
    "platform": "instagram",
    "post_id": "post_xyz",
    "text": "Great video!",
    "author_name": "user42",
    "created_at": "2026-03-10T12:00:00Z",
    "niche_id": "gaming",
}


class TestWritePendingEngagement:
    def test_calls_create_with_correct_fields(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.return_value = "sp-item-1"

        client.write_pending_engagement(SAMPLE_EVENT)

        mock_proxy.create.assert_called_once()
        call_fields = mock_proxy.create.call_args[0][0]
        # Promoted columns (snake_case)
        assert call_fields["platform"] == "instagram"
        assert call_fields["post_id"] == "post_xyz"
        assert call_fields["niche_id"] == "gaming"
        assert call_fields["status"] == "pending"
        # Non-promoted fields (extra JSONB)
        assert call_fields["comment_id"] == "cmt_abc123"
        assert call_fields["comment_text"] == "Great video!"
        assert call_fields["author_name"] == "user42"

    def test_truncates_long_comment_text(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.return_value = "sp-item-1"

        long_text = "x" * 5000
        event = {**SAMPLE_EVENT, "text": long_text}
        client.write_pending_engagement(event)

        call_fields = mock_proxy.create.call_args[0][0]
        assert len(call_fields["comment_text"]) == 2000

    def test_handles_none_text(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.return_value = "sp-item-1"

        event = {**SAMPLE_EVENT, "text": None}
        client.write_pending_engagement(event)

        call_fields = mock_proxy.create.call_args[0][0]
        assert call_fields["comment_text"] == ""

    def test_handles_missing_fields_gracefully(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.return_value = "sp-item-1"

        client.write_pending_engagement({})

        call_fields = mock_proxy.create.call_args[0][0]
        assert call_fields["platform"] == ""
        assert call_fields["status"] == "pending"

    def test_fails_silently_on_error(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.side_effect = RuntimeError("Graph API down")

        # Should not raise, returns None
        result = client.write_pending_engagement(SAMPLE_EVENT)
        assert result is None

    def test_warns_when_table_not_configured(self, mock_config):
        client = _make_client(mock_config)
        client.pending_engagement = None

        # Should not raise, returns None
        result = client.write_pending_engagement(SAMPLE_EVENT)
        assert result is None

    def test_returns_item_id_on_success(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.create.return_value = "sp-item-42"

        result = client.write_pending_engagement(SAMPLE_EVENT)
        assert result == "sp-item-42"


class TestListPendingEngagement:
    def test_applies_status_filter(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.find.return_value = []

        client.list_pending_engagement(status="reviewed")

        call_kwargs = mock_proxy.find.call_args[1]
        assert "reviewed" in call_kwargs["formula"]
        assert "niche_id" not in call_kwargs["formula"]

    def test_applies_niche_filter(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.find.return_value = []

        client.list_pending_engagement(niche_id="gaming", status="pending")

        call_kwargs = mock_proxy.find.call_args[1]
        formula = call_kwargs["formula"]
        assert "gaming" in formula
        assert "pending" in formula
        assert formula.startswith("AND(")

    def test_default_status_is_pending(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.find.return_value = []

        client.list_pending_engagement()

        call_kwargs = mock_proxy.find.call_args[1]
        assert "pending" in call_kwargs["formula"]

    def test_respects_limit(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.find.return_value = []

        client.list_pending_engagement(limit=10)

        call_kwargs = mock_proxy.find.call_args[1]
        assert call_kwargs["max_records"] == 10

    def test_returns_empty_on_error(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_proxy.find.side_effect = RuntimeError("Graph API down")

        result = client.list_pending_engagement()
        assert result == []

    def test_returns_records(self, mock_config):
        client, mock_proxy = _make_client_with_backend(mock_config)
        mock_records = [
            {"id": "rec1", "fields": {"comment_id": "cmt_1", "status": "pending"}},
            {"id": "rec2", "fields": {"comment_id": "cmt_2", "status": "pending"}},
        ]
        mock_proxy.find.return_value = mock_records

        result = client.list_pending_engagement()
        assert len(result) == 2
        assert result[0]["id"] == "rec1"

    def test_warns_when_table_not_configured(self, mock_config):
        client = _make_client(mock_config)
        client.pending_engagement = None

        result = client.list_pending_engagement()
        assert result == []
