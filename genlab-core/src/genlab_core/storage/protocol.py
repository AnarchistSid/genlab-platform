"""StorageBackend protocol — abstract interface for data persistence.

All table operations go through this protocol so BacklogClient can
route to either SharePoint (via GraphTableProxy) or PostgreSQL
without changing its domain methods.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Unified CRUD interface for a single storage engine."""

    def find(
        self,
        table: str,
        *,
        formula: str | None = None,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return records matching the filter.

        Args:
            table: Logical table name (e.g. "Stories", "Blueprints").
            formula: Legacy formula-syntax filter string.
            niche_id: Optional niche_id to inject into the filter.
            max_records: Maximum number of records to return.
        """
        ...

    def get(self, table: str, record_id: str) -> dict[str, Any]:
        """Fetch a single record by its ID."""
        ...

    def create(
        self,
        table: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> dict[str, Any]:
        """Create a record and return the full record dict (with 'id')."""
        ...

    def update(
        self,
        table: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> dict[str, Any]:
        """Update a record's fields and return the updated record."""
        ...

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        ...

    def batch_create(
        self,
        table: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create multiple records, returning the created records."""
        ...

    def batch_update(
        self,
        table: str,
        records: list[dict[str, Any]],
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Update multiple records (each with 'id' and 'fields' keys)."""
        ...

    def upload_attachment(
        self,
        table: str,
        record_id: str,
        field_name: str,
        file_path: str,
    ) -> dict[str, Any] | None:
        """Upload a file attachment to a record field."""
        ...
