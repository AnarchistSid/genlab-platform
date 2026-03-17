"""Backend factory — routes tables to the correct StorageBackend.

Reads ``config/storage_backends.yaml`` to determine which backend (sharepoint
or postgres) handles each table. Both backends can run simultaneously during
migration.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from genlab_core.storage.protocol import StorageBackend

logger = logging.getLogger(__name__)

# Path: genlab-core/src/genlab_core/storage/factory.py
# parents[3] resolves to genlab-core/
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "storage_backends.yaml"


@lru_cache(maxsize=1)
def load_storage_config() -> dict[str, Any]:
    """Load and cache the storage backends configuration."""
    if _CONFIG_PATH.exists():
        data = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
        logger.info("Loaded storage config from %s", _CONFIG_PATH)
        return data
    logger.warning(
        "storage_backends.yaml not found at %s, using defaults", _CONFIG_PATH
    )
    return {"default_backend": "sharepoint", "table_backends": {}}


def get_backend_for_table(table: str) -> StorageBackend:
    """Return the configured StorageBackend for a given table name.

    Reads the ``table_backends`` mapping from config. Falls back to
    ``default_backend`` if the table isn't explicitly listed.
    """
    config = load_storage_config()
    backend_type = config.get("table_backends", {}).get(
        table, config.get("default_backend", "sharepoint")
    )

    if backend_type == "postgres":
        return _postgres_backend(config.get("postgres", {}))
    return _sharepoint_backend()


def _sharepoint_backend() -> StorageBackend:
    """Return a SharePointBackend wrapping BacklogClient's proxies.

    Lazily imported to avoid circular dependencies with the HTTP layer.
    """
    from genlab_core.storage.sharepoint import SharePointBackend

    # In production, BacklogClient is constructed elsewhere and its proxies
    # are passed to SharePointBackend. For the factory, we create a minimal
    # backend. Callers that need the full BacklogClient should construct it
    # directly and pass the proxies.
    return SharePointBackend({})


def _postgres_backend(pg_config: dict[str, Any]) -> StorageBackend:
    """Return a PostgresBackend configured from the postgres config section."""
    from genlab_core.storage.postgres import PostgresBackend

    password = os.environ.get(
        pg_config.get("password_env", "POSTGRES_PASSWORD"), ""
    )
    return PostgresBackend(
        host=pg_config.get("host", "localhost"),
        port=pg_config.get("port", 5432),
        database=pg_config.get("database", "genlab"),
        user=pg_config.get("user", "genlab"),
        password=password,
        min_size=pg_config.get("min_connections", 2),
        max_size=pg_config.get("max_connections", 10),
    )
