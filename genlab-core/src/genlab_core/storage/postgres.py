"""PostgreSQL storage backend — StorageBackend implementation for Postgres.

Translates the legacy formula-syntax filters to SQL WHERE clauses and
executes queries against a PostgreSQL database. Uses psycopg (v3) with
connection pooling.

NOTE: This is a stub implementation. The full SQL translation and
connection management will be implemented when PostgreSQL is deployed.
For now it raises NotImplementedError to make it clear when code
accidentally routes to Postgres before it's ready.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PostgresBackend:
    """StorageBackend implementation backed by PostgreSQL.

    Stub — raises NotImplementedError until Postgres infrastructure is deployed.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        logger.info("PostgresBackend initialised (stub — not connected)")

    def find(
        self,
        table: str,
        *,
        formula: str | None = None,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            f"PostgresBackend.find() not yet implemented (table={table}). "
            "Set this table to 'sharepoint' in config/storage_backends.yaml."
        )

    def get(self, table: str, record_id: str) -> Dict[str, Any]:
        raise NotImplementedError(
            f"PostgresBackend.get() not yet implemented (table={table})."
        )

    def create(
        self,
        table: str,
        fields: Dict[str, Any],
        *,
        typecast: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            f"PostgresBackend.create() not yet implemented (table={table})."
        )

    def update(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
        *,
        typecast: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            f"PostgresBackend.update() not yet implemented (table={table})."
        )

    def delete(self, table: str, record_id: str) -> None:
        raise NotImplementedError(
            f"PostgresBackend.delete() not yet implemented (table={table})."
        )

    def batch_create(
        self,
        table: str,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            f"PostgresBackend.batch_create() not yet implemented (table={table})."
        )

    def batch_update(
        self,
        table: str,
        records: List[Dict[str, Any]],
        **kwargs,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            f"PostgresBackend.batch_update() not yet implemented (table={table})."
        )

    def upload_attachment(
        self,
        table: str,
        record_id: str,
        field_name: str,
        file_path: str,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError(
            f"PostgresBackend.upload_attachment() not yet implemented (table={table})."
        )
