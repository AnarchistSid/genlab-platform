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
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Module-level singletons — WARNING: these persist for the process lifetime.
# Calling get_backend_for_table() with different sharepoint_proxies or
# postgres_dsn will invalidate and replace the cached backend for that engine.
# Use reset_backends() in tests to clear all cached state.
_config: dict[str, str] | None = None
_sharepoint_backend: Any | None = None
_sharepoint_proxies_id: int | None = None  # id() of the proxies dict used to create _sharepoint_backend
_postgres_backend: Any | None = None
_postgres_dsn_used: str | None = None  # DSN used to create _postgres_backend


def _load_config() -> dict[str, str]:
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
    sharepoint_proxies: dict | None = None,
    postgres_dsn: str | None = None,
):
    """Return the correct StorageBackend for the given table.

    Backend instances are cached at module level.  If called again with a
    *different* ``sharepoint_proxies`` dict (by identity) or a different
    ``postgres_dsn``, the stale cached backend is replaced automatically.
    Use ``reset_backends()`` in tests to clear all cached state.

    Args:
        table: Logical table name (e.g. "Stories", "Blueprints").
        sharepoint_proxies: Dict of table_name -> GraphTableProxy.  Required
            if any table routes to SharePoint.
        postgres_dsn: PostgreSQL connection string.  Required if any table
            routes to Postgres.

    Returns:
        A StorageBackend instance (SharePointBackend or PostgresBackend).
    """
    global _sharepoint_backend, _sharepoint_proxies_id
    global _postgres_backend, _postgres_dsn_used

    config = _load_config()
    engine = config.get(table, "sharepoint").lower()

    if engine == "postgres":
        dsn = postgres_dsn or os.getenv("DATABASE_URL")
        if _postgres_backend is not None and dsn != _postgres_dsn_used:
            logger.debug("Postgres DSN changed — replacing cached backend")
            _postgres_backend = None
        if _postgres_backend is None:
            from genlab_core.storage.postgres import PostgresBackend

            # PostgresBackend takes host/port/database/user/password, not a DSN string.
            # Parse DSN if provided, otherwise use defaults.
            pg_kwargs = {}
            if dsn:
                from urllib.parse import urlparse
                parsed = urlparse(dsn)
                pg_kwargs = {
                    k: v for k, v in {
                        "host": parsed.hostname,
                        "port": parsed.port,
                        "database": (parsed.path or "").lstrip("/") or None,
                        "user": parsed.username,
                        "password": parsed.password,
                    }.items() if v is not None
                }
            _postgres_backend = PostgresBackend(**pg_kwargs)
            _postgres_dsn_used = dsn
        return _postgres_backend

    # Default: sharepoint
    if (
        sharepoint_proxies is not None
        and _sharepoint_backend is not None
        and id(sharepoint_proxies) != _sharepoint_proxies_id
    ):
        logger.debug("SharePoint proxies changed — replacing cached backend")
        _sharepoint_backend = None
    if _sharepoint_backend is None:
        if sharepoint_proxies is None:
            raise ValueError(
                "SharePoint proxies must be provided to get_backend_for_table() "
                f"for table '{table}'. Pass sharepoint_proxies= from BacklogClient."
            )
        from genlab_core.storage.sharepoint import SharePointBackend

        _sharepoint_backend = SharePointBackend(sharepoint_proxies)
        _sharepoint_proxies_id = id(sharepoint_proxies)
    return _sharepoint_backend


def reset_backends() -> None:
    """Reset cached backends and config.  Used in tests."""
    global _config, _sharepoint_backend, _sharepoint_proxies_id
    global _postgres_backend, _postgres_dsn_used
    _config = None
    _sharepoint_backend = None
    _sharepoint_proxies_id = None
    _postgres_backend = None
    _postgres_dsn_used = None
