"""Unit tests for BacklogClient credential resolution.

Verifies that BacklogClient reads Azure/SharePoint credentials from
genlab_core.settings (which resolves AGENT_ROOT -> .env), and that
explicit kwargs override settings.
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
        patch.dict("os.environ", {"GENLAB_USE_POSTGRES": "", "DATABASE_URL": ""}, clear=False),
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
        patch("genlab_core.http.backlog_client.GraphTableProxy"),
    ):
        yield mock_cred_cls, mock_graph_cls


@pytest.fixture
def mock_config(tmp_path):
    """Create a minimal lists_config.yaml for BacklogClient."""
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


def _make_settings(**overrides):
    """Create a mock settings object with Azure credential attributes."""
    defaults = {
        "azure_tenant_id": "tenant-from-settings",
        "azure_client_id": "client-from-settings",
        "azure_client_secret": "secret-from-settings",
        "sharepoint_site_id": "site-from-settings",
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestSettingsFallback:
    def test_reads_from_settings_when_no_kwargs(self, mock_config):
        """BacklogClient should read credentials from settings when no explicit kwargs."""
        mock_settings = _make_settings()

        with patch("genlab_core.settings.settings", mock_settings):
            from genlab_core.http.backlog_client import BacklogClient

            client = BacklogClient(config_path=mock_config)

        assert client._site_id == "site-from-settings"

    def test_explicit_kwargs_override_settings(self, mock_config):
        """Explicit kwargs must take precedence over settings values."""
        mock_settings = _make_settings()

        with patch("genlab_core.settings.settings", mock_settings):
            from genlab_core.http.backlog_client import BacklogClient

            client = BacklogClient(
                config_path=mock_config,
                tenant_id="tenant-explicit",
                client_id="client-explicit",
                client_secret="secret-explicit",
                site_id="site-explicit",
            )

        assert client._site_id == "site-explicit"

    def test_raises_when_no_credentials_anywhere(self, mock_config):
        """BacklogClient should raise ValueError when creds are absent everywhere."""
        mock_settings = _make_settings(
            azure_tenant_id=None,
            azure_client_id=None,
            azure_client_secret=None,
            sharepoint_site_id=None,
        )

        with patch("genlab_core.settings.settings", mock_settings):
            from genlab_core.http.backlog_client import BacklogClient

            with pytest.raises(ValueError, match="AZURE_TENANT_ID"):
                BacklogClient(config_path=mock_config)

    def test_partial_kwargs_filled_from_settings(self, mock_config):
        """If only some kwargs are provided, the rest should come from settings."""
        mock_settings = _make_settings()

        with patch("genlab_core.settings.settings", mock_settings):
            from genlab_core.http.backlog_client import BacklogClient

            client = BacklogClient(
                config_path=mock_config,
                site_id="site-override",
            )

        assert client._site_id == "site-override"
