"""Tests for the storage backend factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.storage.factory import (
    get_backend_for_table,
    reset_backends,
)
from genlab_core.storage.postgres import PostgresBackend
from genlab_core.storage.sharepoint import SharePointBackend


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset factory singletons between tests.

    Clear env vars AND mock the config loader to return empty dict
    (SharePoint default). The on-disk storage_backends.yaml maps all
    tables to postgres, which would break tests expecting SharePoint.
    """
    monkeypatch.delenv("GENLAB_USE_POSTGRES", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STORAGE_BACKENDS_CONFIG", raising=False)
    reset_backends()
    # Patch _load_config to return empty dict (default = sharepoint)
    import genlab_core.storage.factory as _factory

    monkeypatch.setattr(_factory, "_config", {})
    yield
    reset_backends()


class TestGetBackendForTable:
    def test_returns_sharepoint_by_default(self):
        proxies = {"Stories": MagicMock()}
        backend = get_backend_for_table("Stories", sharepoint_proxies=proxies)
        assert isinstance(backend, SharePointBackend)

    def test_returns_same_sharepoint_instance(self):
        proxies = {"Stories": MagicMock(), "Blueprints": MagicMock()}
        b1 = get_backend_for_table("Stories", sharepoint_proxies=proxies)
        b2 = get_backend_for_table("Blueprints", sharepoint_proxies=proxies)
        assert b1 is b2

    def test_returns_postgres_when_configured(self, tmp_path):
        config_file = tmp_path / "storage_backends.yaml"
        config_file.write_text("tables:\n  Stories: postgres\n")

        with patch.dict("os.environ", {"STORAGE_BACKENDS_CONFIG": str(config_file)}):
            reset_backends()
            backend = get_backend_for_table("Stories")
            assert isinstance(backend, PostgresBackend)

    def test_raises_without_proxies_for_sharepoint(self):
        with pytest.raises(ValueError, match="SharePoint proxies must be provided"):
            get_backend_for_table("Stories")

    def test_unknown_table_defaults_to_sharepoint(self):
        proxies = {"UnknownTable": MagicMock()}
        backend = get_backend_for_table("UnknownTable", sharepoint_proxies=proxies)
        assert isinstance(backend, SharePointBackend)

    def test_reset_clears_cache(self):
        proxies = {"Stories": MagicMock()}
        b1 = get_backend_for_table("Stories", sharepoint_proxies=proxies)
        reset_backends()
        b2 = get_backend_for_table("Stories", sharepoint_proxies=proxies)
        assert b1 is not b2

    def test_config_from_default_path(self):
        """Factory finds config/storage_backends.yaml relative to package."""
        # The on-disk config maps all tables to postgres (Sprint 68).
        # Reset _config to None so _load_config reads the real file.
        import genlab_core.storage.factory as _factory

        _factory._config = None
        reset_backends()
        backend = get_backend_for_table("Stories")
        assert isinstance(backend, PostgresBackend)
