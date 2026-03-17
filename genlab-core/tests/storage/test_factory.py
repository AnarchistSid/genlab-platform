"""Tests for storage backend factory."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_load_config():
    """Config loads from YAML and has expected structure."""
    from genlab_core.storage.factory import load_storage_config

    # Clear LRU cache so it reloads
    load_storage_config.cache_clear()
    config = load_storage_config()
    assert config["default_backend"] == "sharepoint"
    assert "blueprints" in config["table_backends"]
    # Blueprints is now routed to postgres (Phase 1 complete)
    assert config["table_backends"]["blueprints"] == "postgres"


def test_load_config_fallback_when_missing():
    """When config file doesn't exist, returns sensible defaults."""
    from genlab_core.storage import factory

    factory.load_storage_config.cache_clear()
    original = factory._CONFIG_PATH
    try:
        factory._CONFIG_PATH = Path("/nonexistent/storage_backends.yaml")
        factory.load_storage_config.cache_clear()
        config = factory.load_storage_config()
        assert config["default_backend"] == "sharepoint"
    finally:
        factory._CONFIG_PATH = original
        factory.load_storage_config.cache_clear()


def test_blueprints_routes_to_postgres():
    """Blueprints is configured to use postgres backend (Phase 1)."""
    from genlab_core.storage.factory import get_backend_for_table, load_storage_config

    load_storage_config.cache_clear()
    with patch("genlab_core.storage.factory._postgres_backend") as mock_pg:
        mock_backend = MagicMock()
        mock_pg.return_value = mock_backend
        backend = get_backend_for_table("blueprints")
        assert backend is mock_backend
        mock_pg.assert_called_once()


def test_stories_routes_to_sharepoint():
    """Stories (and other non-migrated tables) still use sharepoint."""
    from genlab_core.storage.factory import get_backend_for_table, load_storage_config
    from genlab_core.storage.sharepoint import SharePointBackend

    load_storage_config.cache_clear()
    with patch("genlab_core.storage.factory._sharepoint_backend") as mock_sp:
        mock_backend = MagicMock(spec=SharePointBackend)
        mock_sp.return_value = mock_backend
        backend = get_backend_for_table("stories")
        assert backend is mock_backend
        mock_sp.assert_called_once()


def test_get_backend_for_unknown_table_uses_default():
    """Unknown tables fall back to the default_backend."""
    from genlab_core.storage.factory import get_backend_for_table, load_storage_config

    load_storage_config.cache_clear()
    with patch("genlab_core.storage.factory._sharepoint_backend") as mock_sp:
        mock_sp.return_value = MagicMock()
        backend = get_backend_for_table("some_new_table")
        assert backend is not None
        mock_sp.assert_called_once()


def test_get_backend_for_postgres_table():
    """When a table is configured as postgres, returns PostgresBackend."""
    from genlab_core.storage import factory

    factory.load_storage_config.cache_clear()

    # Temporarily override config
    with patch.object(factory, "load_storage_config") as mock_config:
        mock_config.return_value = {
            "default_backend": "sharepoint",
            "table_backends": {"blueprints": "postgres"},
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "database": "genlab",
                "user": "genlab",
                "password_env": "POSTGRES_PASSWORD",
            },
        }
        with patch("genlab_core.storage.factory._postgres_backend") as mock_pg:
            mock_pg.return_value = MagicMock()
            backend = get_backend_for_table("blueprints")
            assert backend is not None
            mock_pg.assert_called_once()


def test_config_has_postgres_section():
    """storage_backends.yaml includes postgres connection settings."""
    from genlab_core.storage.factory import load_storage_config

    load_storage_config.cache_clear()
    config = load_storage_config()
    pg = config.get("postgres", {})
    assert pg.get("host") == "localhost"
    assert pg.get("port") == 5432
    assert pg.get("database") == "genlab"
    assert pg.get("password_env") == "POSTGRES_PASSWORD"


# Import get_backend_for_table at module level after patches are defined
from genlab_core.storage.factory import get_backend_for_table
