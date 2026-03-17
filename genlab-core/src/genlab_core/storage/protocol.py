"""StorageBackend protocol — the interface all backends must implement.

Implementations: SharePointBackend (existing), PostgresBackend (new).
BacklogClient delegates to the appropriate backend per table.

All methods are synchronous. PostgresBackend uses asyncio internally but
exposes a sync API so callers don't need to change.

Record format: ``{"id": "...", "fields": {...}}`` — matches the existing
SharePoint record shape used by BacklogClient.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract storage backend for GenLab data tables.

    Implementations must provide CRUD operations returning records in the
    standard ``{id, fields}`` format that BacklogClient expects.
    """

    def create(self, table: str, record: Dict[str, Any]) -> str:
        """Create a record. Returns the new record ID."""
        ...

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID. Returns None if not found."""
        ...

    def find(
        self,
        table: str,
        *,
        formula: str = "",
        niche_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Find records matching a formula filter. Returns list of records.

        The ``formula`` parameter uses the legacy OData-like syntax:
          - ``{field}='value'``
          - ``AND({f1}='v1', {f2}='v2')``

        The ``niche_id`` parameter enables RLS isolation (PostgresBackend)
        or appends a niche filter (SharePointBackend).
        """
        ...

    def update(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
    ) -> None:
        """Update fields on an existing record."""
        ...

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        ...

    def batch_create(
        self,
        table: str,
        records: List[Dict[str, Any]],
    ) -> List[str]:
        """Create multiple records. Returns list of new record IDs."""
        ...
