"""Storage backend factory — routes tables to the correct StorageBackend.

Reads config/storage_backends.yaml to decide whether each table is
served by SharePoint or PostgreSQL.  Caches backend instances so
repeated calls for the same table return the same object.

Usage:
    backend = get_backend_for_table("Stories", sharepoint_proxies=proxies)
    records = backend.find("Stories", formula="{status}='INTAKE'")
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# Module-level singletons
_config: Optional[Dict[str, str]] = None
_sharepoint_backend: Optional[Any] = None
_postgres_backend: Optional[Any] = None


def _load_config() -> Dict[str, str]:
    """Load and cache the storage_backends.yaml mapping."""
    global _config
    if _config is not None:
        return _config

    # Try multiple locations for the config file
    candidates = []

    env_path = os.getenv("STORAGE_BACKENDS_CONFIG", "")
    if env_path:
        candidates.append(Path(env_path))

    # Relative to genlab-core/config/
    here = Path(__file__).resolve()
    # src/genlab_core/storage/factory.py -> genlab-core/config/
    genlab_core_root = here.parent.parent.parent.parent
    candidates.append(genlab_core_root / "config" / "storage_backends.yaml")

    for candidate in candidates:
        if candidate.exists():
            with open(candidate) as f:
                data = yaml.safe_load(f) or {}
            _config = data.get("tables", {})
            logger.debug("Loaded storage_backends.yaml from %s", candidate)
            return _config

    logger.info(
        "storage_backends.yaml not found, defaulting all tables to 'sharepoint'"
    )
    _config = {}
    return _config


def get_backend_for_table(
    table: str,
    *,
    sharepoint_proxies: Optional[Dict] = None,
    postgres_dsn: str | None = None,
):
    """Return the correct StorageBackend for the given table.

    Args:
        table: Logical table name (e.g. "Stories", "Blueprints").
        sharepoint_proxies: Dict of table_name -> GraphTableProxy.  Required
            if any table routes to SharePoint.
        postgres_dsn: PostgreSQL connection string.  Required if any table
            routes to Postgres.

    Returns:
        A StorageBackend instance (SharePointBackend or PostgresBackend).
    """
    global _sharepoint_backend, _postgres_backend

    config = _load_config()
    engine = config.get(table, "sharepoint").lower()

    if engine == "postgres":
        if _postgres_backend is None:
            from genlab_core.storage.postgres import PostgresBackend

            dsn = postgres_dsn or os.getenv("DATABASE_URL")
            _postgres_backend = PostgresBackend(dsn=dsn)
        return _postgres_backend

    # Default: sharepoint
    if _sharepoint_backend is None:
        if sharepoint_proxies is None:
            raise ValueError(
                "SharePoint proxies must be provided to get_backend_for_table() "
                f"for table '{table}'. Pass sharepoint_proxies= from BacklogClient."
            )
        from genlab_core.storage.sharepoint import SharePointBackend

        _sharepoint_backend = SharePointBackend(sharepoint_proxies)
    return _sharepoint_backend


def reset_backends() -> None:
    """Reset cached backends and config.  Used in tests."""
    global _config, _sharepoint_backend, _postgres_backend
    _config = None
    _sharepoint_backend = None
    _postgres_backend = None
